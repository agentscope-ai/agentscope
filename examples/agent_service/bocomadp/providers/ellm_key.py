# -*- coding: utf-8 -*-
"""ELLM API key fetch helper — stateless, lazy key acquisition.

Fetches a fresh API key from the BOCOM ELLM gateway on demand. This is the
"get a new key" primitive for the outer-product lazy-refresh scheme: callers
(``AutoRefreshEllmChatModel``) check expiry before each model call and invoke
:func:`fetch_ellm_key` when the key is stale.

The request/response handling is adapted from deer-flow's
``EllmApiKeyManager._fetch_key_from_server`` / ``_parse_key_response``
(see ``backend/packages/harness/deerflow/models/ellm_apikey_manager.py``),
but deliberately simplified:

- Pure function — no singleton, no threads, no file cache, no lock.
- ``httpx.HTTPError`` (network / HTTP status) propagates to the caller so the
  downstream model can keep serving with the previous key.
- ``ValueError`` is raised for business-level failures (``TRAN_SUCCESS != "1"``,
  missing ``apiKey``).

Returned ``ttl_ms`` is the key's *remaining* validity in milliseconds from
now, normalized from either format the gateway returns:

- a TTL duration (ms), e.g. ``1_500_000`` (25 minutes), or
- an absolute Unix-ms expiry timestamp, e.g. ``1776070825782``
  (distinguished by the ``_TIMESTAMP_THRESHOLD_MS`` magnitude check).

Usage::

    from bocomadp.providers.ellm_key import fetch_ellm_key

    key, ttl_ms = fetch_ellm_key(
        scene_code="P2024146",
        api_key_url="http://eaip-ellm-1.bocomm.com/ELLM.ELLM-OMSERVICE.V-1.0/createSceneApiKey.do",
    )
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Values >= this threshold are treated as Unix timestamps (ms);
# smaller values are treated as TTL durations (ms).
# Rationale: 10^12 ms ≈ Sept 2001 — any real timestamp is far above this,
# while even a 1-year TTL (≈ 3.15 × 10^10 ms) is well below.
_TIMESTAMP_THRESHOLD_MS = 1_000_000_000_000

_DEFAULT_TIMEOUT = 30  # seconds, mirrors deer-flow's DEFAULT_REQUEST_TIMEOUT


def fetch_ellm_key(
    scene_code: str,
    api_key_url: str,
    timeout: float = _DEFAULT_TIMEOUT,
) -> tuple[str, int]:
    """Fetch a fresh ELLM API key from the gateway.

    Args:
        scene_code: BOCOM ELLM scene code, e.g. ``"P2024146"``.
        api_key_url: Gateway URL (``createSceneApiKey.do`` endpoint).
        timeout: HTTP request timeout in seconds.

    Returns:
        A tuple ``(api_key, ttl_ms)`` where ``ttl_ms`` is the key's remaining
        validity in milliseconds from the moment it was obtained.

    Raises:
        httpx.HTTPError: Network failure or non-2xx HTTP status (propagates —
            caller falls back to the previous key).
        ValueError: The gateway rejected the request (``TRAN_SUCCESS != "1"``)
            or the response carried no ``apiKey``.
    """
    req_message = json.dumps(
        {
            "REQ_HEAD": {"TRAN_PROCESS": "", "TRAN_ID": ""},
            "REQ_BODY": {"param": {"sceneCode": scene_code}},
        },
        ensure_ascii=False,
    )

    logger.debug("fetch_ellm_key: requesting new key (scene_code=%s)", scene_code)

    # HTTP/network failures raise here and propagate to the caller untouched.
    response = httpx.post(
        api_key_url,
        data={"REQ_MESSAGE": req_message},
        timeout=timeout,
    )
    response.raise_for_status()

    return _parse_key_response(scene_code, response.json())


def _parse_key_response(scene_code: str, data: dict[str, Any]) -> tuple[str, int]:
    """Parse the ELLM key-service response into ``(api_key, ttl_ms)``.

    Expected response shape::

        {
            "RSP_BODY": {
                "result": {
                    "apiKey": "...",
                    "timeToLive": 1776070825782
                }
            },
            "RSP_HEAD": {"TRAN_SUCCESS": "1"}
        }

    ``timeToLive`` may be a TTL duration (ms) or an absolute Unix-ms expiry
    timestamp; the magnitude threshold decides which, and ``ttl_ms`` is
    normalized to "remaining ms from now" in both cases.
    """
    rsp_head = data.get("RSP_HEAD", {})
    if rsp_head.get("TRAN_SUCCESS") != "1":
        raise ValueError(
            "fetch_ellm_key: key request failed (TRAN_SUCCESS != 1, "
            f"scene_code={scene_code}, response={data})"
        )

    rsp_body = data.get("RSP_BODY", {})
    result = rsp_body.get("result", {})
    api_key = result.get("apiKey")
    time_to_live = result.get("timeToLive")

    if not api_key:
        raise ValueError(
            "fetch_ellm_key: no apiKey in response "
            f"(scene_code={scene_code}, response={data})"
        )

    ttl_int = int(time_to_live) if time_to_live else 0
    if ttl_int >= _TIMESTAMP_THRESHOLD_MS:
        # Absolute expiry timestamp (ms) → remaining ms from now.
        ttl_ms = max(0, ttl_int - int(time.time() * 1000))
    else:
        # TTL duration (ms) — valid for this long from the fetch moment.
        ttl_ms = ttl_int

    logger.info(
        "fetch_ellm_key: key fetched (scene_code=%s, ttl_ms=%s)",
        scene_code,
        ttl_ms,
    )
    return api_key, ttl_ms


__all__ = ["fetch_ellm_key"]
