# -*- coding: utf-8 -*-
"""Fetch webpage Markdown via r.jina.ai gateway.

Tool: web_fetch_markdown_via_gateway(url)

Behavior:
- GET https://r.jina.ai/{encoded_url} to retrieve a Markdown rendition.
- On 2xx: return the Markdown (truncated to a safe length).
- On non-2xx or exceptions: return content-only error message; logger records
  URL, status, and brief diagnostics.

Contract:
- Parameters: {url: str} only (zero-deviation schema).
- Result: ToolResponse with text content only. No business metadata.
"""
from __future__ import annotations

import traceback
from urllib.parse import quote

try:
    import requests  # type: ignore
except Exception:  # pragma: no cover - defer import error to runtime path
    requests = None  # type: ignore

from .._response import ToolResponse
from ...message import TextBlock
from ..._logging import logger
from ._common import (
    USER_AGENT,
    HTTP_TIMEOUT_SECONDS,
    truncate_text,
    normalize_and_validate_url,
    error_response,
)


def web_fetch_markdown_via_gateway(url: str) -> ToolResponse:
    """Fetch webpage Markdown via r.jina.ai gateway.

    Use only when you need a quick Markdown view of a public page. Input
    `url` (string). On failures, return a short error message.
    """
    # Validate input URL first
    try:
        norm_url = normalize_and_validate_url(url)
    except Exception as e:  # pylint: disable=broad-except
        logger.warning("web_fetch_markdown_via_gateway: invalid url=%r; err=%s", url, e)
        return error_response(str(e))

    # Network client availability
    if requests is None:
        logger.error("requests module unavailable; cannot perform HTTP fetch")
        return error_response("requests module unavailable (TODO: install requests)")

    gw = f"https://r.jina.ai/{quote(norm_url, safe='') }"

    try:
        res = requests.get(
            gw,
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT_SECONDS,
        )
        status = res.status_code
        if 200 <= status < 300:
            text = truncate_text(res.text)
            return ToolResponse(content=[TextBlock(type="text", text=text)])

        # Non-2xx: log details, return concise error
        preview = truncate_text(res.text or "", 1000)
        logger.error(
            "gateway fetch non-2xx: url=%s status=%s preview=%s", norm_url, status, preview,
        )
        return error_response(f"gateway HTTP {status}")

    except Exception as e:  # pylint: disable=broad-except
        logger.error(
            "gateway fetch exception: url=%s err=%s\n%s", norm_url, e, traceback.format_exc(),
        )
        return error_response(str(e))


__all__ = ["web_fetch_markdown_via_gateway"]

