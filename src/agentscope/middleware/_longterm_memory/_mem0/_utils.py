# -*- coding: utf-8 -*-
"""Pure helper functions for the mem0 middleware.

These are stateless adapters that translate between AgentScope and
mem0 data shapes. Keeping them out of the middleware class makes them
trivial to unit-test in isolation.
"""
from __future__ import annotations

from typing import Any

from ....event import ExternalExecutionResultEvent, UserConfirmResultEvent
from ....message import Msg


def _extract_query_text(inputs: Any) -> str | None:
    """Pull a single text query out of the agent inputs.

    Returns ``None`` for resumption events or empty/non-user inputs,
    in which case the middleware skips both retrieval and write-back.
    """
    if inputs is None:
        return None
    if isinstance(
        inputs,
        (ExternalExecutionResultEvent, UserConfirmResultEvent),
    ):
        return None

    msgs = inputs if isinstance(inputs, list) else [inputs]
    texts: list[str] = []
    for m in msgs:
        if not isinstance(m, Msg) or m.role != "user":
            continue
        text = m.get_text_content()
        if text:
            texts.append(text)
    return "\n".join(texts) if texts else None


def _mem0_extracted_anything(raw: Any) -> bool:
    """Did mem0's add() actually extract any memories from the input?

    mem0 returns ``{"results": [...]}``; an empty list means its LLM
    extractor decided nothing was worth storing. The
    ``_async_add_with_fallback`` strategy uses this to decide whether
    to try another role / disable inference.
    """
    if not isinstance(raw, dict):
        return False
    results = raw.get("results")
    return isinstance(results, list) and len(results) > 0


def _extract_memory_texts(raw: Any) -> list[str]:
    """Flatten a mem0 search response into a list of memory strings.

    Tolerates the common shapes:
      - ``{"results": [{"memory": str, ...}, ...]}`` (current OSS / Platform)
      - ``[{"memory": str, ...}, ...]`` (legacy / variations)
      - ``{"results": [str, ...]}`` (defensive fallback)
    """
    if raw is None:
        return []
    results = raw.get("results", raw) if isinstance(raw, dict) else raw
    if not isinstance(results, list):
        return []
    out: list[str] = []
    for item in results:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            text = item.get("memory") or item.get("text")
            if text:
                out.append(str(text))
    return out
