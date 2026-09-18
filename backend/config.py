import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent

IGNORE_DIRS = {
    ".git", "__pycache__", "node_modules", "venv", "env", "agentenv",
    ".vscode", ".idea", "dist", "build", ".next", ".pytest_cache",
}

IGNORE_EXTS = {
    ".pyc", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".pdf", ".zip",
    ".sqlite3", ".db", ".exe", ".bin", ".mp4", ".json", ".jsonc", ".css",
    ".scss", ".sass", ".html", ".htm", ".xml", ".yaml", ".yml", ".toml",
    ".lock", ".csv", ".tsv", ".md", ".txt", ".rst", ".gitignore", ".env",
    ".local",
}


class Settings(BaseSettings):
    APP_NAME: str = "CodeGraph AI"
    ENVIRONMENT: str = "development"
    API_PREFIX: str = "/api"

    BASE_DIR: Path = BASE_DIR
    UPLOAD_DIR: Path = BASE_DIR / "uploads"
    CHROMA_DB_DIR: Path = BASE_DIR / "chroma_db"
    PROJECT_TREES_DIR: Path = BASE_DIR / "project_trees"
    ARTIFACTS_DIR: Path = BASE_DIR / "artifacts"
    SQLITE_DIR: Path = BASE_DIR / "data"
    GRAMMARS_DIR: Path = BASE_DIR / "grammars"

    BACKEND_CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    DATABASE_URL: str | None = None
    STORAGE_MODE: str = "local"

    MODEL_PROVIDER_ORDER: str = "groq,gemini,ollama,openrouter,nvidia,huggingface"
    SUPERVISOR_REASONING_MODELS: str = (
        "groq:openai/gpt-oss-20b,"
        "gemini:gemini-2.5-flash,"
        "ollama:qwen2.5-coder:3b,"
        "groq:openai/gpt-oss-120b,"
        "gemini:gemini-2.5-pro,"
        "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"
    )
    DEBUGGER_CODING_MODELS: str = (
        "groq:openai/gpt-oss-120b,"
        "gemini:gemini-2.5-flash,"
        "ollama:qwen2.5-coder:3b,"
        "groq:openai/gpt-oss-120b,"
        "openrouter:cohere/north-mini-code:free"
    )
    COMMENTER_CODE_DOCS_MODELS: str = (
        "groq:openai/gpt-oss-20b,"
        "gemini:gemini-2.5-flash-lite,"
        "ollama:qwen2.5-coder:3b,"
        "openrouter:cohere/north-mini-code:free"
    )
    ARCHITECTURE_DESIGN_MODELS: str = (
        "gemini:gemini-2.5-flash,"
        "ollama:qwen2.5-coder:3b,"
        "groq:openai/gpt-oss-120b,"
        "openrouter:nvidia/nemotron-3-ultra-550b-a55b:free"
    )
    RETRIEVAL_QUERY_MODELS: str = "gemini:gemini-2.5-flash-lite,ollama:qwen2.5-coder:3b,groq:openai/gpt-oss-20b"
    VISION_ERROR_ANALYSIS_MODELS: str = (
        "gemini:gemini-2.5-flash,"
        "nvidia:meta/llama-3.2-90b-vision-instruct,"
        "nvidia:meta/llama-3.2-11b-vision-instruct,"
        "openrouter:google/gemini-2.5-flash,"
        "ollama:llava:7b"
    )
    GUARDRAIL_MODELS: str = "gemini:gemini-2.5-flash-lite,groq:llama-guard-4-12b"
    EMBEDDING_MODELS: str = (
        "gemini:gemini-embedding-001,"
        "huggingface:sentence-transformers/all-MiniLM-L6-v2,"
        "ollama:nomic-embed-text"
    )

    OLLAMA_BASE_URL: str = "http://localhost:11434"
    PROVIDER_TIMEOUT_SECONDS: float = 20.0
    PROVIDER_MAX_RETRIES: int = 0
    MODEL_TEMPERATURE: float = 0.1
    TRUSTED_SEARCH_DOMAINS: str = (
        "react.dev,nextjs.org,vite.dev,developer.mozilla.org,docs.python.org,"
        "fastapi.tiangolo.com,python.langchain.com,js.langchain.com,"
        "docs.pydantic.dev,docs.sqlalchemy.org,npmjs.com,pypi.org,github.com"
    )
    WEB_SEARCH_MAX_RESULTS: int = 3
    WEB_SEARCH_TIMEOUT_SECONDS: float = 8.0
    VISION_ANALYSIS_TIMEOUT_SECONDS: float = 35.0

    AUTH_PROVIDER: str = "local"
    JWT_SECRET_KEY: str = "fallback_secret_key"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    SUPABASE_URL: str | None = None
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_JWT_SECRET: str | None = None

    OPENROUTER_API_KEY: str | None = None
    GROQ_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    HUGGINGFACE_API_KEY: str | None = None
    NVIDIA_API_KEY: str | None = None

    LANGSMITH_TRACING: bool = False
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = "CodeGraph_AI"
    LANGSMITH_ENDPOINT: str = "https://apac.api.smith.langchain.com"

    MAX_PROJECT_FILES: int = 300
    MAX_FILE_BYTES: int = 350_000
    MAX_ZIP_BYTES: int = 1_073_741_824
    MAX_IMAGE_BYTES: int = 5_000_000
    ALLOWED_IMAGE_TYPES: set[str] = {"image/png", "image/jpeg", "image/webp"}

    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.lower() == "production"

    @property
    def use_supabase_storage(self) -> bool:
        return self.STORAGE_MODE.lower() == "supabase" or (self.is_production and bool(self.DATABASE_URL))

    @property
    def resolved_database_url(self) -> str:
        if not self.DATABASE_URL:
            return f"sqlite:///{(self.SQLITE_DIR / 'codegraph_users.db').as_posix()}"
        if self.DATABASE_URL.startswith("postgresql://"):
            return self.DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
        if self.DATABASE_URL.startswith("postgres://"):
            return self.DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
        return self.DATABASE_URL

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]

    def configure_langsmith(self):
        if self.LANGSMITH_API_KEY and self.LANGSMITH_TRACING:
            os.environ["LANGSMITH_TRACING"] = str(self.LANGSMITH_TRACING).lower()
            os.environ["LANGSMITH_API_KEY"] = self.LANGSMITH_API_KEY
            os.environ["LANGSMITH_PROJECT"] = self.LANGSMITH_PROJECT
            os.environ["LANGSMITH_ENDPOINT"] = self.LANGSMITH_ENDPOINT

    def create_required_directories(self):
        for directory in [
            self.UPLOAD_DIR,
            self.CHROMA_DB_DIR,
            self.PROJECT_TREES_DIR,
            self.ARTIFACTS_DIR,
            self.SQLITE_DIR,
            self.GRAMMARS_DIR,
        ]:
            directory.mkdir(parents=True, exist_ok=True)


settings = Settings()
