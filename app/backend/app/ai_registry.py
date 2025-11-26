from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from .config import get_settings


@dataclass
class AIEndpointConfig:
    model: str
    prompt_path: Path
    temperature: float = 0.2
    max_output_tokens: int = 1024


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def get_ai_registry() -> Dict[str, AIEndpointConfig]:
    settings = get_settings()
    base_prompt = settings.relation_factory_prompt_path

    return {
        "relation_factory": AIEndpointConfig(
            model=settings.relation_factory_model,
            prompt_path=base_prompt,
            temperature=0.2,
            max_output_tokens=800,
        ),
        # Future endpoints (probe, judge, etc.) can be added here.
    }


def get_relation_factory_prompt() -> str:
    registry = get_ai_registry()
    config = registry["relation_factory"]
    return load_prompt(config.prompt_path)
