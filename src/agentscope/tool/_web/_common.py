# -*- coding: utf-8 -*-
"""Common helpers for web tools (no tool registration here).

Responsibilities:
- URL normalization/validation (minimal, non-strict)
- Shared constants (UA, timeouts, truncate limits)
- Error ToolResponse factory
- HTML sanitation placeholder (TODO)

Note: Keep this module free of any network/browser side-effects.
"""
from __future__ import annotations

from typing import Final
from urllib.parse import urlparse

from .._response import ToolResponse
from ...message import TextBlock


USER_AGENT: Final[str] = "AgentScope-Web/1.0 (+https://github.com/)"
HTTP_TIMEOUT_SECONDS: Final[int] = 15
MAX_OUTPUT_CHARS: Final[int] = 50_000


def normalize_and_validate_url(url: str) -> str:
    """Return the url if it looks valid; otherwise raise ValueError.

    Minimal check: scheme in {http, https} and netloc present.
    """
    url = (url or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"Invalid URL: {url!r}")
    return url


def truncate_text(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if limit <= 0:
        return text or ""
    t = text or ""
    return t[:limit]


def error_response(message: str) -> ToolResponse:
    """Create a ToolResponse carrying an error message in content only."""
    text = f"Error: {message}"
    return ToolResponse(
        content=[TextBlock(type="text", text=text)],
    )


def sanitize_html(html: str) -> str:
    """Placeholder for HTML sanitation.

    TODO: remove <script/style/noscript/iframe/svg>, drop event attributes,
    collapse whitespace, preserve visible semantics.
    """
    return html


__all__ = [
    "USER_AGENT",
    "HTTP_TIMEOUT_SECONDS",
    "MAX_OUTPUT_CHARS",
    "normalize_and_validate_url",
    "truncate_text",
    "error_response",
    "sanitize_html",
]
