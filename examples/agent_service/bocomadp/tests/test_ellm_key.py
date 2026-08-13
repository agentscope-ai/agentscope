# -*- coding: utf-8 -*-
"""Tests for the ELLM API key fetch helper (:func:`fetch_ellm_key`).

Adapted from deer-flow's ``EllmApiKeyManager`` logic but pinned to the
stateless, lazy pure-function contract used by the AgentScope outer product:

- ``fetch_ellm_key(scene_code, api_key_url, timeout=30) -> (api_key, ttl_ms)``
- ``ttl_ms`` is the key's *remaining* validity in milliseconds from now,
  normalized from either a TTL-duration value (e.g. 1_500_000 = 25 min) or an
  absolute Unix-millisecond expiry timestamp (>= ``_TIMESTAMP_THRESHOLD_MS``).
- HTTP/network failures propagate as ``httpx.HTTPError``; business failures
  (``TRAN_SUCCESS != "1"``, missing apiKey) raise ``ValueError``.
"""

from __future__ import annotations

import json
import time
from unittest import mock

import httpx
import pytest

from bocomadp.providers.ellm_key import fetch_ellm_key


def _response(payload: dict, url: str = "http://ellm.example/api") -> httpx.Response:
    """Build a 200 OK httpx.Response carrying a JSON body.

    httpx >= 0.28 requires a bound ``request`` on the response for
    ``raise_for_status()``, so we attach one.
    """
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", url),
    )


@pytest.fixture
def mock_post():
    """Mock the ``httpx.post`` call so no network is touched."""
    with mock.patch("httpx.post") as m:
        yield m


class TestFetchEllmKey:
    def test_fetch_returns_key_and_ttl(self, mock_post) -> None:
        """Duration-style timeToLive (1_500_000 ms = 25 min) → (key, ttl_ms)."""
        mock_post.return_value = _response(
            {
                "RSP_HEAD": {"TRAN_SUCCESS": "1"},
                "RSP_BODY": {"result": {"apiKey": "key-abc", "timeToLive": 1_500_000}},
            }
        )

        key, ttl_ms = fetch_ellm_key(
            "P2024146", "http://ellm.example/ELLM-OMSERVICE/createSceneApiKey.do"
        )

        assert key == "key-abc"
        assert ttl_ms == 1_500_000

        # Request contract: POST to api_key_url with the scene code embedded in
        # a JSON REQ_MESSAGE form field (mirrors deer-flow's request shape).
        call = mock_post.call_args
        assert call.args[0] == "http://ellm.example/ELLM-OMSERVICE/createSceneApiKey.do"
        assert call.kwargs["timeout"] == 30
        req_message = json.loads(call.kwargs["data"]["REQ_MESSAGE"])
        assert req_message["REQ_BODY"]["param"]["sceneCode"] == "P2024146"

    def test_fetch_rejects_failure(self, mock_post) -> None:
        """TRAN_SUCCESS != "1" → ValueError."""
        mock_post.return_value = _response(
            {
                "RSP_HEAD": {"TRAN_SUCCESS": "0", "TRAN_MSG": "auth failed"},
                "RSP_BODY": {"result": {}},
            }
        )

        with pytest.raises(ValueError):
            fetch_ellm_key("P2024146", "http://ellm.example/api")

    def test_fetch_handles_timestamp_ttl(self, mock_post) -> None:
        """Absolute Unix-ms expiry timestamp (>= 1e12) → remaining ms from now."""
        now_ms = int(time.time() * 1000)
        future_ts = now_ms + 3_600_000  # absolute expiry 1 hour from now
        mock_post.return_value = _response(
            {
                "RSP_HEAD": {"TRAN_SUCCESS": "1"},
                "RSP_BODY": {"result": {"apiKey": "key-ts", "timeToLive": future_ts}},
            }
        )

        key, ttl_ms = fetch_ellm_key("P2024146", "http://ellm.example/api")

        assert key == "key-ts"
        assert 3_599_000 <= ttl_ms <= 3_601_000

    def test_fetch_propagates_http_error(self, mock_post) -> None:
        """Network failure (httpx.HTTPError) propagates to the caller."""
        mock_post.side_effect = httpx.ConnectError("connection refused")

        with pytest.raises(httpx.HTTPError):
            fetch_ellm_key("P2024146", "http://ellm.example/api")
