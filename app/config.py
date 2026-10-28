from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # Grok / xAI
    groq_api_key: str = ""
    groq_model: str = "openai/gpt-oss-20b"

    # Database and vector store
    database_url: str = f"sqlite:///{ROOT_DIR / 'data' / 'tasks.db'}"
    chroma_path: str = str(ROOT_DIR / "data" / "chroma")

    # Application settings
    app_host: str = "127.0.0.1"
    app_port: int = 8000

    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    search_max_distance: float = 0.75


settings = Settings()
