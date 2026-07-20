# -*- coding: utf-8 -*-
"""Reply-termination vocabulary shared by the ``message`` and ``event``
modules. Lives in ``types`` (a leaf) so both can depend on it downward
without an import cycle."""
from enum import StrEnum

from pydantic import BaseModel


class ReplyFinishedReason(StrEnum):
    """The reason a reply finished."""

    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    EXCEED_MAX_ITERS = "exceed_max_iters"
    ERROR = "error"


class ErrorType(StrEnum):
    """Classification of a fatal error that terminated a reply.

    Not model-specific: the status-derived members apply to any upstream
    service reached during a reply (chat model, embedding, TTS, MCP)."""

    AUTHENTICATION = "authentication"
    """401 — credential missing or wrong."""
    PERMISSION = "permission"
    """403 — authenticated but not allowed."""
    RATE_LIMIT = "rate_limit"
    """429 — rate/quota exceeded."""
    INVALID_REQUEST = "invalid_request"
    """400 / 422 — malformed request."""
    UPSTREAM = "upstream"
    """5xx — an upstream service failed."""
    CONNECTION = "connection"
    """Network error / timeout — no HTTP status available."""
    INTERNAL = "internal"
    """Framework bug or otherwise unexpected exception."""
    UNKNOWN = "unknown"
    """Fallback when no better classification is possible."""


class ErrorInfo(BaseModel):
    """Structured, UI-facing description of a fatal reply error."""

    type: ErrorType = ErrorType.UNKNOWN
    """Stable classification key; the frontend localizes off it."""
    message: str
    """Short, sanitized, human-readable description."""
