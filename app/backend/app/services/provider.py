from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Tuple

from openai import AsyncOpenAI

from ..config import get_settings


@dataclass
class CallStats:
    """Aggregated statistics for a single LLM call."""

    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cents: int = 0


# Pricing map expressed in cents per 1M tokens.
_MODEL_PRICING: Dict[str, Tuple[int, int]] = {
    "gpt-4o-mini": (15, 60),
    "gpt-4o": (500, 1500),
    "gpt-3.5-turbo": (150, 400),
    "glm-4": (100, 100),
    "glm-4-air": (20, 80),
}


def _resolve_pricing(model: str) -> Optional[Tuple[int, int]]:
    for prefix, pricing in _MODEL_PRICING.items():
        if model.startswith(prefix):
            return pricing
    return None


def _resolve_api_key(explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    return (
        os.getenv("LLM_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("ZHIPUAI_API_KEY")
        or os.getenv("GLM_API_KEY")
    )


def _resolve_base_url(provider: Optional[str], explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    if not provider:
        return None
    provider = provider.lower()
    if provider in {"glm", "zhipuai"}:
        return get_settings().glm_base_url
    return None


def _coerce_usage(usage: Any) -> Tuple[int, int, int]:
    if not usage:
        return 0, 0, 0
    prompt = 0
    completion = 0
    total = 0
    for key in ("prompt_tokens", "promptTokens", "input_tokens"):
        if isinstance(usage, dict):
            prompt = int(usage.get(key, prompt)) or prompt
        else:
            prompt = int(getattr(usage, key, prompt) or prompt)
        if prompt:
            break
    for key in ("completion_tokens", "output_tokens"):
        if isinstance(usage, dict):
            completion = int(usage.get(key, completion)) or completion
        else:
            completion = int(getattr(usage, key, completion) or completion)
        if completion:
            break
    for key in ("total_tokens", "totalTokens"):
        if isinstance(usage, dict):
            total = int(usage.get(key, total)) or total
        else:
            total = int(getattr(usage, key, total) or total)
        if total:
            break
    if not total:
        total = prompt + completion
    return prompt, completion, total


async def call_llm(
    *,
    messages: Iterable[Dict[str, str]],
    model: str,
    provider: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    response_format: Optional[Dict[str, Any]] = None,
    extra_params: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, CallStats]:
    """Execute an OpenAI-compatible chat completion and return stats."""

    resolved_key = _resolve_api_key(api_key) or get_settings().llm_api_key or ""
    resolved_base = _resolve_base_url(provider, base_url)

    client = AsyncOpenAI(api_key=resolved_key, base_url=resolved_base)

    params: Dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
    }
    if max_tokens is not None:
        params["max_tokens"] = max_tokens
    if response_format is not None:
        params["response_format"] = response_format
    if extra_params:
        params.update(extra_params)

    try:
        response = await client.chat.completions.create(**params)
    except Exception:
        return None, CallStats(model=model)

    usage = getattr(response, "usage", None)
    prompt_tokens, completion_tokens, total_tokens = _coerce_usage(usage)
    pricing = _resolve_pricing(model)
    cost_cents = 0
    if pricing:
        in_cents, out_cents = pricing
        cost = (prompt_tokens / 1_000_000) * in_cents + (completion_tokens / 1_000_000) * out_cents
        cost_cents = int(round(cost))

    stats = CallStats(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_cents=cost_cents,
    )
    return response, stats
