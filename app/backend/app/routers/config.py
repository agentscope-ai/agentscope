from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException

from ..config import get_settings, get_settings as _get_settings

router = APIRouter(prefix="/config", tags=["config"])


ENV_PATH = (Path(__file__).resolve().parents[2] / ".env").resolve()
_ALLOWED_KEYS = {
    "DATABASE_URL",
    "LLM_PROVIDER",
    "LLM_API_KEY",
    "RELATION_FACTORY_MODEL",
    "RELATION_FACTORY_PROMPT_PATH",
    "BUDGET_CENTS_DEFAULT",
    "GLM_BASE_URL",
}


def _parse_env_file(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _dump_env_file(path: Path, data: Dict[str, Any]) -> None:
    lines = [f"{k}={v}" for k, v in data.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@router.get("/info")
async def get_config_info():
    s = get_settings()
    return {
        "database_url": s.database_url,
        "llm_provider": s.llm_provider,
        "relation_factory_model": s.relation_factory_model,
        "relation_factory_prompt_path": str(s.relation_factory_prompt_path),
        "budget_cents_default": s.budget_cents_default,
        "glm_base_url": s.glm_base_url,
        "has_llm_api_key": bool(s.llm_api_key),
        "env_path": str(ENV_PATH),
    }


@router.post("/set")
async def set_config(values: Dict[str, Any]):
    if not values:
        raise HTTPException(status_code=400, detail="Empty payload")

    env = _parse_env_file(ENV_PATH)

    for k, v in values.items():
        if k not in _ALLOWED_KEYS:
            raise HTTPException(status_code=400, detail=f"Key '{k}' not allowed")
        env[k] = str(v)

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _dump_env_file(ENV_PATH, env)

    # Reset cached settings so subsequent requests reflect updates
    _get_settings.cache_clear()  # type: ignore[attr-defined]

    return {"ok": True, "written": list(values.keys()), "env_path": str(ENV_PATH)}
