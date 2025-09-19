from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
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

_AGENTSCOPE_MODEL_CACHE: Optional[Dict[str, Any]] = None


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
        or os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("OLLAMA_API_KEY")
    )


def _resolve_base_url(provider: Optional[str], explicit: Optional[str]) -> Optional[str]:
    if explicit:
        return explicit
    if not provider:
        return None
    provider = provider.lower()
    if provider in {"glm", "zhipuai"}:
        return get_settings().glm_base_url
    if provider == "ollama":
        return os.getenv("OLLAMA_BASE_URL")
    return None


def _load_agentscope_models() -> Optional[Dict[str, Any]]:
    global _AGENTSCOPE_MODEL_CACHE
    if _AGENTSCOPE_MODEL_CACHE is not None:
        return _AGENTSCOPE_MODEL_CACHE

    try:
        from agentscope.model import (
            AnthropicChatModel,
            DashScopeChatModel,
            GeminiChatModel,
            OllamaChatModel,
        )
    except ImportError:
        root = Path(__file__).resolve().parents[4]
        src_path = root / "src"
        if src_path.exists() and str(src_path) not in sys.path:
            sys.path.append(str(src_path))
        try:
            from agentscope.model import (
                AnthropicChatModel,
                DashScopeChatModel,
                GeminiChatModel,
                OllamaChatModel,
            )
        except ImportError:
            _AGENTSCOPE_MODEL_CACHE = None
            return None

    _AGENTSCOPE_MODEL_CACHE = {
        "dashscope": DashScopeChatModel,
        "anthropic": AnthropicChatModel,
        "gemini": GeminiChatModel,
        "ollama": OllamaChatModel,
    }
    return _AGENTSCOPE_MODEL_CACHE


def _chat_response_to_openai_payload(response: Any) -> Any:
    if response is None:
        return None
    if isinstance(response, dict):
        return response

    content_blocks = getattr(response, "content", None)
    if not isinstance(content_blocks, list):
        return response

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in content_blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            try:
                arguments = json.dumps(block.get("input", {}))
            except (TypeError, ValueError):
                arguments = ""
            tool_calls.append(
                {
                    "id": block.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": block.get("name") or "",
                        "arguments": arguments,
                    },
                }
            )

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_parts),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls

    payload: dict[str, Any] = {"choices": [{"message": message}]}

    metadata = getattr(response, "metadata", None)
    if metadata is not None:
        payload["choices"][0]["message"]["metadata"] = metadata

    usage = getattr(response, "usage", None)
    prompt_tokens, completion_tokens, total_tokens = _coerce_usage(usage)
    if prompt_tokens or completion_tokens or total_tokens:
        payload["usage"] = {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        }

    return payload


async def _call_openai_like(
    *,
    messages: Iterable[Dict[str, str]],
    model: str,
    api_key: Optional[str],
    base_url: Optional[str],
    temperature: float,
    max_tokens: Optional[int],
    response_format: Optional[Dict[str, Any]],
    extra_params: Optional[Dict[str, Any]],
) -> Tuple[Any, CallStats]:
    client_kwargs: Dict[str, Any] = {"api_key": api_key or None}
    if base_url:
        client_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**client_kwargs)

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


async def _call_agentscope_model(
    provider: str,
    *,
    messages: Iterable[Dict[str, str]],
    model: str,
    api_key: Optional[str],
    base_url: Optional[str],
    temperature: float,
    max_tokens: Optional[int],
    response_format: Optional[Dict[str, Any]],
    extra_params: Optional[Dict[str, Any]],
) -> Tuple[Any, CallStats]:
    adapters = _load_agentscope_models()
    if not adapters or provider not in adapters:
        return None, CallStats(model=model)

    ModelCls = adapters[provider]
    init_kwargs: Dict[str, Any] = {"model_name": model, "stream": False}

    if provider != "ollama":
        init_kwargs["api_key"] = api_key
    else:
        if base_url:
            init_kwargs["host"] = base_url

    if provider == "dashscope" and base_url:
        init_kwargs["base_http_api_url"] = base_url

    try:
        model_client = ModelCls(**init_kwargs)
    except Exception:
        return None, CallStats(model=model)

    call_kwargs: Dict[str, Any] = {}
    if temperature is not None:
        call_kwargs["temperature"] = temperature
    if max_tokens is not None:
        call_kwargs["max_tokens"] = max_tokens
    if extra_params:
        call_kwargs.update(extra_params)
    if response_format is not None:
        call_kwargs.setdefault("response_format", response_format)

    try:
        response = await model_client(list(messages), **call_kwargs)
    except Exception:
        return None, CallStats(model=model)

    payload = _chat_response_to_openai_payload(response)
    usage = getattr(response, "usage", None)
    prompt_tokens, completion_tokens, total_tokens = _coerce_usage(usage)

    stats = CallStats(
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost_cents=0,
    )
    return payload, stats


async def _call_dashscope(**kwargs: Any) -> Tuple[Any, CallStats]:
    return await _call_agentscope_model("dashscope", **kwargs)


async def _call_anthropic(**kwargs: Any) -> Tuple[Any, CallStats]:
    return await _call_agentscope_model("anthropic", **kwargs)


async def _call_gemini(**kwargs: Any) -> Tuple[Any, CallStats]:
    return await _call_agentscope_model("gemini", **kwargs)


async def _call_ollama(**kwargs: Any) -> Tuple[Any, CallStats]:
    return await _call_agentscope_model("ollama", **kwargs)


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

    provider_normalized = (provider or "openai").lower()
    resolved_key = _resolve_api_key(api_key) or get_settings().llm_api_key or None
    resolved_base = _resolve_base_url(provider_normalized, base_url)

    params: Dict[str, Any] = {
        "messages": list(messages),
        "model": model,
        "api_key": resolved_key,
        "base_url": resolved_base,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": response_format,
        "extra_params": extra_params,
    }

    if provider_normalized in {"openai", "glm", "zhipuai", None}:
        return await _call_openai_like(**params)
    if provider_normalized == "dashscope":
        return await _call_dashscope(**params)
    if provider_normalized == "anthropic":
        return await _call_anthropic(**params)
    if provider_normalized == "gemini":
        return await _call_gemini(**params)
    if provider_normalized == "ollama":
        return await _call_ollama(**params)

    return await _call_openai_like(**params)
