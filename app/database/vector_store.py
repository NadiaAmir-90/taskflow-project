
import logging
from pathlib import Path
from typing import Any

import chromadb

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MAX_DISTANCE = 0.75  # cosine distance


def _task_text(task: Any) -> str:
    """Only the words that describe the task; status/priority are filters, not meaning."""
    description = (task.description or "").strip()
    return f"{task.title}. {description}" if description else str(task.title)


class TaskVectorStore:
    def __init__(self) -> None:
        Path(settings.chroma_path).mkdir(parents=True, exist_ok=True)
        self.client = chromadb.PersistentClient(path=settings.chroma_path)
        self._openai = None
        self._local = None
        self.provider = "none"
        self.collection = None

        openai_key = getattr(settings, "openai_api_key", "")

        if openai_key:
            try:
                from openai import OpenAI

                self._openai = OpenAI(api_key=openai_key)
                self.provider = "openai"
            except Exception as exc:
                logger.warning("OpenAI embeddings unavailable: %s", exc)

        if self._openai is None:
            try:
                from chromadb.utils import embedding_functions

                self._local = embedding_functions.DefaultEmbeddingFunction()
                self.provider = "local"
            except Exception as exc:
                logger.warning("Local embeddings unavailable: %s", exc)

        if self.provider != "none":
            self.collection = self.client.get_or_create_collection(
                name=f"taskflow_tasks_{self.provider}",
                metadata={"hnsw:space": "cosine"},
            )

        logger.info("Task search embeddings: %s", self.provider)

  
    def _embed(self, text: str) -> list[float] | None:
        try:
            if self._openai is not None:
                result = self._openai.embeddings.create(
                    model=getattr(settings, "openai_embedding_model", "text-embedding-3-small"),
                    input=text
                )
                return list(result.data[0].embedding)
            if self._local is not None:
                return [float(value) for value in self._local([text])[0]]
        except Exception as exc:
            logger.warning("Embedding failed; word matching will be used: %s", exc)
        return None

    
    def upsert(self, task: Any) -> None:
        if self.collection is None:
            return
        embedding = self._embed(_task_text(task))
        if embedding is None:
            logger.warning("Task %s was not indexed (no embedding).", task.id)
            return
        try:
            self.collection.upsert(
                ids=[task.id],
                documents=[_task_text(task)],
                metadatas=[{"status": task.status, "priority": task.priority, "title": task.title}],
                embeddings=[embedding],
            )
        except Exception as exc:
            logger.warning("Chroma upsert failed for task %s: %s", task.id, exc)

    def delete(self, task_id: str) -> None:
        if self.collection is None:
            return
        try:
            self.collection.delete(ids=[task_id])
        except Exception as exc:
            logger.warning("Chroma delete failed for task %s: %s", task_id, exc)

    def sync(self, tasks: list[Any]) -> None:
        """Make the index match SQLite: index missing tasks, drop deleted ones."""
        if self.collection is None:
            return
        existing = set(self.collection.get(include=[])["ids"])
        wanted = {task.id for task in tasks}
        stale = list(existing - wanted)
        if stale:
            self.collection.delete(ids=stale)
        for task in tasks:
            if task.id not in existing:
                self.upsert(task)

   
    def search_scored(self, query: str, limit: int = 10) -> list[tuple[str, float]]:
        """(task id, distance) pairs, closest first. Useful for tuning the cutoff."""
        if self.collection is None:
            return []
        total = self.collection.count()
        if total == 0:
            return []
        embedding = self._embed(query)
        if embedding is None:
            return []
        try:
            result = self.collection.query(
                query_embeddings=[embedding],
                n_results=max(1, min(limit, total)),
                include=["distances"],
            )
        except Exception as exc:
            logger.warning("Chroma query failed: %s", exc)
            return []
        ids = (result.get("ids") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        return list(zip(ids, distances))

    def search(self, query: str, limit: int = 10) -> list[str]:
        """Task ids that are close enough in meaning to the query, best first."""
        max_distance = getattr(settings, "search_max_distance", DEFAULT_MAX_DISTANCE)
        return [
            task_id
            for task_id, distance in self.search_scored(query, limit)
            if distance <= max_distance
        ]


vector_store = TaskVectorStore()