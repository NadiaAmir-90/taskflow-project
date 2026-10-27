from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from app.api.chat import router as chat_router
from app.api.tasks import router as tasks_router
from app.database import models  # noqa: F401 - registers SQLAlchemy model metadata
from app.database.session import Base, engine

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield

app = FastAPI(title="TASKFLOW API", version="1.0.0", description="AI-powered task management with LangGraph, SQLite and ChromaDB", lifespan=lifespan)
app.include_router(tasks_router)
app.include_router(chat_router)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

@app.get("/", include_in_schema=False)
def home():
    return FileResponse(FRONTEND_DIR / "index.html")

@app.get("/api/health", tags=["system"])
def health():
    return {"status": "ok", "service": "TASKFLOW"}
