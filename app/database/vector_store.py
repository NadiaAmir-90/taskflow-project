"""ChromaDB adapter. SQLite remains the source of truth.

If OpenAI embeddings are available, use them. If not, ChromaDB is still initialized
and lexical search is used as a resilient fallback rather than blocking CRUD.
"""
import hashlib
import logging
import re
import math
from pathlib import Path
from typing import Any
import chromadb
from app.config import settings

logger = logging.getLogger(__name__)

class TaskVectorStore:
    def __init__(self) -> None:
        Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=settings.chroma_path)
        self.collection = self.client.get_or_create_collection(name="taskflow_tasks", metadata={"hnsw:space": "cosine"})
        self._openai = None
        if settings.openai_api_key:
            try:
                from openai import OpenAI
                self._openai = OpenAI(api_key=settings.openai_api_key)
            except Exception as exc:
                logger.warning("OpenAI embeddings unavailable: %s", exc)

    def _embedding(self, text: str) -> list[float] | None:
        if not self._openai:
            return None
        try:
            result = self._openai.embeddings.create(model=settings.openai_embedding_model, input=text)
            return result.data[0].embedding
        except Exception as exc:
            logger.warning("Embedding request failed; lexical search will be used: %s", exc)
            return None

    @staticmethod
    def _hash_embedding(text: str, dimensions: int = 384) -> list[float]:
        """Small deterministic local embedding to avoid Chroma downloading a model."""
        vector = [0.0] * dimensions
        for token in re.findall(r"[a-z0-9]+", text.lower()):
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def upsert(self, task: Any) -> None:
        text = f"{task.title}\n{task.description}\nstatus {task.status}\npriority {task.priority}"
        metadata = {"status": task.status, "priority": task.priority, "title": task.title}
        embedding = self._embedding(text)
        try:
            # Never let Chroma silently invoke its default model downloader.
            # With no configured API key, use a local deterministic embedding.
            if embedding is None and self._openai:
                logger.warning("Skipping vector upsert because the configured embedding provider failed")
                return
            if embedding is None:
                embedding = self._hash_embedding(text)
            self.collection.upsert(ids=[task.id], documents=[text], metadatas=[metadata], embeddings=[embedding])
        except Exception as exc:
            logger.warning("Chroma upsert failed for task %s: %s", task.id, exc)

    def delete(self, task_id: str) -> None:
        try:
            self.collection.delete(ids=[task_id])
        except Exception as exc:
            logger.warning("Chroma delete failed for task %s: %s", task_id, exc)

    def search(self, query: str, limit: int = 10) -> list[str]:
        embedding = self._embedding(query)
        if embedding is not None:
            try:
                result = self.collection.query(query_embeddings=[embedding], n_results=max(1, limit))
                return list((result.get("ids") or [[]])[0])
            except Exception as exc:
                logger.warning("Chroma query failed; lexical fallback used: %s", exc)
        # Lightweight fallback used when no API key is configured.
        tokens = set(re.findall(r"[a-z0-9]+", query.lower()))
        if not tokens:
            return []
        try:
            all_items = self.collection.get(include=["documents"])
            scored = []
            for task_id, document in zip(all_items.get("ids", []), all_items.get("documents", [])):
                doc_tokens = set(re.findall(r"[a-z0-9]+", (document or "").lower()))
                score = len(tokens & doc_tokens)
                if score:
                    scored.append((score, task_id))
            scored.sort(reverse=True)
            return [task_id for _, task_id in scored[:limit]]
        except Exception as exc:
            logger.warning("Chroma lexical fallback failed: %s", exc)
            return []

vector_store = TaskVectorStore()
