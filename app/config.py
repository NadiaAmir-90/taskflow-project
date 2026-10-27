from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent

class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'tasks.db'}"
    chroma_path: str = str(ROOT_DIR / "data" / "chroma")
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    model_config = SettingsConfigDict(env_file=ROOT_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
