# -*- coding: utf-8 -*-
"""Pure helper functions for the ReMe middleware.

These are stateless adapters that translate between AgentScope and ReMe
data shapes. Keeping them out of the middleware class makes them trivial
to unit-test in isolation.
"""
from __future__ import annotations

from typing import Any

from ....event import ExternalExecutionResultEvent, UserConfirmResultEvent
from ....message import Msg


def _extract_query_text(inputs: Any) -> str | None:
    """Pull a single text query out of the agent inputs.

    Returns ``None`` for resumption events or empty/non-user inputs,
    in which case the middleware skips both retrieval and write-back.

    Args:
        inputs (`Any`):
            The unified ``on_reply`` inputs — a ``Msg``, a list of
            ``Msg``, a HITL resumption event, or ``None``.

    Returns:
        `str | None`:
            The joined text of all user messages, or ``None`` when there
            is nothing to search/write.
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


def _extract_memory_texts(raw: Any) -> list[str]:
    """Flatten a ReMe ``search`` response into a list of memory strings.

    ReMe's ``search`` returns a ``Response`` envelope whose
    ``metadata["results"]`` is a list of file chunks. This helper
    tolerates the common shapes:

    - ``{"metadata": {"results": [{"text": str, "path": str, ...}, ...]}}``
      (the standard ``search`` response)
    - ``{"results": [{"text": str, ...}, ...]}`` (already-unwrapped)
    - ``[{"text": str, ...}, ...]`` (plain list of chunks)
    - ``[str, ...]`` / ``{"results": [str, ...]}`` (defensive fallback)

    Each chunk's text is taken from ``text`` (ReMe's ``FileChunk.text``),
    falling back to ``memory`` / ``content`` for forward compatibility.

    Args:
        raw (`Any`):
            The raw object returned by ReMe's ``search`` job (e.g.
            ``ReMe.run_job("search", ...)``) or the decoded ReMe response
            envelope.

    Returns:
        `list[str]`:
            Retrieved memory texts, in response order.
    """
    if raw is None:
        return []

    results: Any = raw
    if isinstance(raw, dict):
        # Unwrap one level of ``metadata`` if present, then ``results``.
        if (
            isinstance(raw.get("metadata"), dict)
            and "results" in raw["metadata"]
        ):
            results = raw["metadata"]["results"]
        else:
            results = raw.get("results", raw)

    if not isinstance(results, list):
        return []

    out: list[str] = []
    for item in results:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            text = (
                item.get("text") or item.get("memory") or item.get("content")
            )
            if text:
                out.append(str(text))
    return out
