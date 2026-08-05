"""Request trace context helpers.

Adapted from deer-flow-2.0's trace_context.py. Pure stdlib, no deps.

The value stored here is a request-level correlation id. It is bound by
:class:`TraceMiddleware` for every HTTP request and can be propagated into
agent runs / background tasks via :func:`ensure_trace_context`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Final

TRACE_ID_HEADER: Final[str] = "X-Trace-Id"
_MAX_TRACE_ID_LENGTH: Final[int] = 512

_current_trace_id: Final[ContextVar[str | None]] = ContextVar(
    "bocomadp_current_trace_id",
    default=None,
)


def generate_trace_id() -> str:
    """Return a fresh header-safe trace id (32-char hex)."""
    return uuid.uuid4().hex


def normalize_trace_id(value: object) -> str | None:
    """Return a safe trace id string, or ``None`` when *value* is unusable.

    Only printable ASCII (0x20-0x7E) is accepted. C0/C1 controls and DEL
    are rejected for header-safety (latin-1 round-trip) and log-injection
    defense.
    """
    if not isinstance(value, str):
        return None
    trace_id = value.strip()
    if not trace_id or len(trace_id) > _MAX_TRACE_ID_LENGTH:
        return None
    if any(ord(ch) < 32 or ord(ch) > 126 for ch in trace_id):
        return None
    return trace_id


def set_current_trace_id(trace_id: str) -> Token[str | None]:
    """Bind *trace_id* to the current execution context."""
    normalized = normalize_trace_id(trace_id)
    if normalized is None:
        normalized = generate_trace_id()
    return _current_trace_id.set(normalized)


def reset_current_trace_id(token: Token[str | None]) -> None:
    """Restore the trace context captured by *token*."""
    _current_trace_id.reset(token)


def get_current_trace_id() -> str | None:
    """Return the current request trace id, if one is bound."""
    return _current_trace_id.get()


@contextmanager
def request_trace_context(trace_id: str | None = None) -> Iterator[str]:
    """Bind a request trace id for the duration of a request."""
    normalized = normalize_trace_id(trace_id) or generate_trace_id()
    token = _current_trace_id.set(normalized)
    try:
        yield normalized
    finally:
        _current_trace_id.reset(token)


@contextmanager
def ensure_trace_context(trace_id: str | None = None) -> Iterator[str]:
    """Bind *trace_id*, inherit the current trace, or create a fresh one.

    Use this when a background task / agent run may or may not have an
    inbound trace — it never clobbers a caller's trace with ``None``.
    """
    normalized = (
        normalize_trace_id(trace_id)
        or get_current_trace_id()
        or generate_trace_id()
    )
    token = _current_trace_id.set(normalized)
    try:
        yield normalized
    finally:
        _current_trace_id.reset(token)
