# TASKFLOW — AI-powered task manager

TASKFLOW is a beginner-friendly single-page task manager. The task board and AI chat appear together. FastAPI serves the API and static frontend; SQLite is the source of truth; ChromaDB stores task vectors; LangGraph coordinates AI actions; the direct OpenAI SDK interprets natural-language requests.

## Features
- Create, list, search, edit, move, complete, and delete tasks.
- Fields: title, description, status, priority, due date.
- Persistent SQLite tasks and ChromaDB task index.
- Clarification when a task reference matches multiple tasks.
- Explicit confirmation before deletion.
- Validation and tests.
- Works for CRUD without an API key; AI chat uses a small local fallback parser if no key is configured. OpenAI improves intent parsing and embeddings when configured.

## 1. Install
Python 3.11+ is recommended. From this folder:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
cp .env.example .env
```

Set `OPENAI_API_KEY` in `.env` to enable OpenAI-powered chat and embeddings. API billing is separate from a ChatGPT subscription. Without a key, CRUD works and chat uses a limited fallback parser; ChromaDB remains configured but retrieval falls back to lexical matching when embeddings are unavailable.

## 2. Run

```bash
uvicorn app.main:app --reload
```

Open http://127.0.0.1:8000. API documentation: http://127.0.0.1:8000/docs.

## 3. Test

```bash
pytest -q
```

## Project structure

```text
app/
  api/          FastAPI route handlers
  agent/        LangGraph state, nodes, graph and prompts
  database/     SQLAlchemy models/session and ChromaDB adapter
  services/     Task business logic
  validators/   Pydantic request/response validation models
  config.py     Environment configuration
  main.py       Application entry point
frontend/       Single-page board and chat UI
 tests/         API and service tests
 data/          Local SQLite and ChromaDB persistence (ignored by Git)
```

## Phases 1–9
1. Environment, dependencies, configuration, project structure.
2. SQLite model, validation, CRUD service, deterministic IDs.
3. FastAPI task endpoints and tests.
4. One-page responsive frontend with board and chat.
5. Direct OpenAI SDK integration and structured intent parsing.
6. LangGraph workflow with routing, resolution, confirmation, execution and response nodes.
7. ChromaDB task indexing and semantic retrieval; SQLite remains authoritative.
8. Safety: validation, ambiguity clarification, delete confirmation, recoverable errors and consistency handling.
9. Tests, run instructions, API docs and project handoff.

## Notes about vector search
When an OpenAI API key is available, TASKFLOW uses the configured embedding model. If embeddings cannot be generated, the app still operates and uses lexical search as a fallback. Vector results are always resolved back to SQLite so the UI displays current task data. A failed vector-index update is logged; SQLite task data is not discarded.
# taskflow-project
