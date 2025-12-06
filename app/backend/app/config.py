from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR.parent / ".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "sqlite+aiosqlite:///./relation_zettel.db"

    # LLM provider + keys
    llm_provider: Optional[str] = None  # openai|dashscope|anthropic|gemini|ollama|glm
    llm_api_key: Optional[str] = None

    # Provider-specific
    glm_base_url: str = "https://open.bigmodel.cn/api/paas/v4"

    # Relation Factory defaults
    relation_factory_model: str = "gpt-4o-mini"
    relation_factory_prompt_path: Path = BASE_DIR / "prompts" / "relation_factory.md"
    budget_cents_default: int = 100


@lru_cache()
def get_settings() -> Settings:
    return Settings()
