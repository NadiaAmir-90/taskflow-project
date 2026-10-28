# TASKFLOW — AI-powered task manager

TASKFLOW is a single-page task manager with two ways to work: a normal task board you click, and an AI chat (**Flow**) you type to. Both do the same four operations — create, read, update, delete — on the same data. You can say things like *"Create a high-priority task to finish the demo tomorrow, then show all unfinished tasks"* and the agent carries out every step in order.

![status](https://img.shields.io/badge/python-3.11%2B-blue) ![api](https://img.shields.io/badge/FastAPI-SQLite%20%2B%20ChromaDB-green)


## 1. Features

| Feature | From the task board (manual) | From the chat (prompt) |
|---|---|---|
| **Create** a task | **＋ New task** form (title, description, priority, status, due date) | "Create a high-priority task to finish the demo tomorrow" |
| **Read** / list / search | Filter tabs: All, To do, In progress, Done | "Show all unfinished tasks related to documentation" |
| **Update** title, description, due date | **Edit** button on a task | "Change the due date of the demo task to Friday" |
| **Change priority** | Edit form → Priority | "Change the priority of the API task to Low" |
| **Change status** | Checkbox (done / reopen) or Edit form → Status | "Move the demo task to In progress" |
| **Complete** a task | Tick the checkbox | "Mark the weekly report as done" |
| **Delete** permanently | **×** button (asks for confirmation) | "Delete the demo task" (asks you to confirm) |

Also included:
- **Single and multiple operations in one message** — "Move the demo task to In progress and mark the weekly report Done" runs both, in order.
- **Conversation memory** — the agent remembers the last 5 exchanges (your 5 messages and its 5 replies), so it can answer "what is my name?" or resolve "mark it as done".
- **General conversation** — greetings, thanks and questions about what Flow can do get a normal chat reply.
- **Clarification** — if a name matches several tasks, Flow lists them and asks which one you mean.
- **Delete confirmation** — nothing is deleted from chat until you confirm.
- **Semantic search** — tasks are indexed in ChromaDB so you can search by meaning when an embedding key is configured.
- **Persistence** — data survives a page refresh and a server restart.
- **Validation** — every request is validated, and errors are explained in plain language.
- **Works without an AI key** — the board always works, and the chat falls back to a simple built-in parser.

---

## 2. Tech stack

| Layer | Technology |
|---|---|
| Backend | Python 3.11+, FastAPI, Uvicorn |
| Database (source of truth) | SQLite via SQLAlchemy |
| Vector store | ChromaDB |
| Agent workflow | LangGraph |
| LLM | Any OpenAI-compatible provider (Groq, Google Gemini, OpenRouter, Ollama, …) through the `openai` SDK |
| Validation | Pydantic |
| Frontend | HTML, CSS, vanilla JavaScript (one page) |
| Tests | pytest, httpx |

---

## 3. Run the project 

### Step 1 — Clone
```bash
git clone https://github.com/NadiaAmir-90/taskflow-project.git
cd taskflow-project
```

### Step 2 — Create a virtual environment and install packages

macOS / Linux:
```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Windows (PowerShell):
```powershell
python -m venv venv
venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3 — Configure the environment
```bash
cp .env.example .env        # Windows: copy .env.example .env
```

Open `.env` and fill in your AI provider (example uses Groq):

```dotenv
# Chat model that turns messages into task actions (any OpenAI-compatible provider)
LLM_API_KEY=your-key-here
LLM_BASE_URL=https://api.groq.com/openai/v1
LLM_MODEL=llama-3.3-70b-versatile

# Optional: embeddings for meaning-based search (leave empty to use word matching)
OPENAI_API_KEY=
OPENAI_EMBEDDING_MODEL=text-embedding-3-small

DATABASE_URL=sqlite:///./data/tasks.db
CHROMA_PATH=./data/chroma
APP_HOST=127.0.0.1
APP_PORT=8000
```

| Provider | `LLM_BASE_URL` |
|---|---|
| Groq | `https://api.groq.com/openai/v1` |

### Step 4 — Start the server
```bash
uvicorn app.main:app --reload
```

### Step 5 — Open the app
- App: <http://127.0.0.1:8000>
- Interactive API docs: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/api/health>

Tasks are stored in `data/tasks.db` and the search index in `data/chroma/`. Both are created automatically.

---

## 4. API reference

Base URL: `http://127.0.0.1:8000`. Full interactive docs: `/docs`.

### Tasks

| Method | Endpoint | Description | Success |
|---|---|---|---|
| `GET` | `/api/tasks` | List tasks. Optional `status` (`todo`, `in_progress`, `done`) and `priority` (`low`, `medium`, `high`). | `200` |
| `GET` | `/api/tasks/search?q=...` | Search tasks. Optional `status`. | `200` |
| `POST` | `/api/tasks` | Create a task. | `201` |
| `PATCH` | `/api/tasks/{id}` | Update any of the fields below. | `200` |
| `DELETE` | `/api/tasks/{id}` | Delete a task permanently. | `204` |

Task object:

```json
{
  "id": "0b6f0c0e-5d5c-4a0a-9a39-6d3c7f6d1c11",
  "title": "Finish the demo",
  "description": "",
  "status": "todo",
  "priority": "high",
  "due_date": "2026-10-29",
  "created_at": "2026-10-28T09:00:00+00:00",
  "updated_at": "2026-10-28T09:00:00+00:00"
}
```

Create example:

```bash
curl -X POST http://127.0.0.1:8000/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"title": "Finish the demo", "priority": "high", "due_date": "2026-10-29"}'
```

Update example (only send the fields to change):

```bash
curl -X PATCH http://127.0.0.1:8000/api/tasks/<id> \
  -H "Content-Type: application/json" \
  -d '{"priority": "low", "status": "in_progress"}'
```

### Chat

`POST /api/chat`

| Field | Type | Description |
|---|---|---|
| `message` | string (1–4000) | What the user typed. |
| `history` | list | Last chat entries: `{"role": "user" or "assistant", "content": "..."}`. |
| `confirm` | boolean | `true` when the user confirmed a delete. |
| `pending_action` | object or null | Returned by the previous response; send it back unchanged. |
| `candidate_task_ids` | list | Returned by the previous response when a choice is needed. |
| `awaiting_clarification` | boolean | Returned by the previous response. |

Request:

```json
{
  "message": "Move the demo task to In progress and mark the weekly report Done",
  "history": [
    {"role": "user", "content": "hello, I am Nadia"},
    {"role": "assistant", "content": "Hello Nadia! How can I help you manage your tasks today?"}
  ]
}
```

Response:

```json
{
  "reply": "Task updated successfully! ...\n\n———\n\nTask updated successfully! ...",
  "tasks": [ { "id": "...", "title": "Finish the demo", "status": "in_progress" } ],
  "pending_action": null,
  "candidate_task_ids": [],
  "awaiting_clarification": false,
  "selected_task_id": null
}
```

When `pending_action` is not `null`, the agent is waiting for you (delete confirmation or a task choice). The browser sends it back with your next message.

### System

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/health` | `{"status": "ok", "service": "TASKFLOW"}` |



## 5. Testing

```bash
pytest -q
```

The tests cover the health check, create / list / update / delete, validation of blank titles and invalid statuses, and the chat endpoint. For a manual check, work through `prompts.txt`, then refresh the page and restart the server to confirm the data persists.

---

## 6. Troubleshooting

| Problem | Fix |
|---|---|
| Chat replies with the generic help text, or only understands simple sentences | The LLM is not being used. Check that `LLM_API_KEY`, `LLM_BASE_URL` and `LLM_MODEL` are set in `.env`, then restart `uvicorn`. The terminal prints `LLM intent parsing failed; using fallback parser: ...` with the reason. |
| `Incorrect API key provided` | The key is not valid for that provider. Create a new key in the provider's console. |
| Model not found | Pick a model name from your provider's current list and set `LLM_MODEL`. |
| Search misses tasks that mean the same thing but use different words | Set `OPENAI_API_KEY` to enable embedding-based search. Without it only shared words match. |
| Port already in use | Run `uvicorn app.main:app --reload --port 8001`. |
| Want a clean start | Stop the server and delete the `data/` folder contents (keep `.gitkeep`). |

---