"""Settings loaded from .env (never print secrets)."""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
DATA_RAW = ROOT / "data" / "raw"
DATA_PROCESSED = ROOT / "data" / "processed"

load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
    llm_model: str = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-flash-0731")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")  # local, via sentence-transformers
    embedding_device: str = os.getenv("EMBEDDING_DEVICE", "")  # "", "cuda" or "cpu" ("" = auto)
    # "local" (sentence-transformers, for bulk indexing) or "openrouter" (same vectors, no GPU)
    embedding_backend: str = os.getenv("EMBEDDING_BACKEND", "local")
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME", "neo4j")   # Aura's file says USERNAME
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "tipitaka-dev-pw")


settings = Settings()
