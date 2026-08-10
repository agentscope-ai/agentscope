# -*- coding: utf-8 -*-
"""ELLM API key fetch/refresh helpers.

- :func:`fetch_ellm_key` — the stateless "get a new key" primitive.
- :class:`EllmKeyRefresher` — the lazy-refresh state machine (expiry
  check / distributed-lock debounce / fetch / write-back), extracted from
  the former ``AutoRefreshEllmChatModel`` so the key lifecycle lives
  outside the model.  :class:`EllmKeyRefreshMiddleware` uses it and
  injects the fresh key per call via ``EllmChatModel.set_api_key``.

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

import asyncio
import json
import logging
import time
from typing import Any

import httpx

from agentscope.app.message_bus import MessageBus
from agentscope.app.storage import CredentialRecord, StorageBase
from agentscope.credential import CredentialFactory

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


# Distributed-lock lease for a single key refresh — a crash while holding
# it delays the next refresh by at most this long.
_LOCK_TTL_SECS = 30


class EllmKeyRefresher:
    """Lazily refresh the ELLM api key stored in a credential record.

    The gateway issues keys valid for ~25 minutes; the stored key's expiry
    is judged solely from the independent ``record.data["apikey_expires_at"]``
    (Unix seconds, stamped on every refresh).  A record without a usable
    stamp is treated as expired, so the refresh writes it and the record
    converges.  An empty ``api_key`` (e.g. the frontend cleared it on
    update) forces an immediate refresh regardless of any expiry stamp.

    All key state lives in the user-scoped credential record identified by
    ``credential_id``; the record's ``data`` dict is expected to carry
    ``api_key``, ``scene_code``, ``api_key_url`` and (optionally)
    ``inject_think_tag``.
    """

    _LOCK_TTL_SECS = _LOCK_TTL_SECS

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        user_id: str,
    ) -> None:
        """Initialize the refresher.

        Args:
            storage (StorageBase): Credential read/write backend —
                accessed only through ``get_credential`` /
                ``upsert_credential``.
            message_bus (MessageBus): Transport used for the refresh lock
                (``acquire_lock``); ``InMemoryMessageBus`` in tests.
            user_id (str): Owner of the credential records.
        """
        self._storage = storage
        self._message_bus = message_bus
        self._user_id = user_id

    @property
    def user_id(self) -> str:
        """The credential owner this refresher serves."""
        return self._user_id

    def _is_expired(self, record: CredentialRecord) -> bool:
        """Whether the record's stored key is stale enough to refresh.

        Judged in priority order:

        1. An empty/absent ``data["api_key"]`` — external updates (e.g.
           the frontend) clear it to force a refresh — is immediately
           expired regardless of any expiry stamp.
        2. A valid ``data["apikey_expires_at"]`` (Unix seconds, stamped on
           every write-back) is expired once ``now`` passes it.  A record
           without a usable expiry stamp is treated as expired, so the
           refresh writes the stamp and the record converges.
        """
        api_key = record.data.get("api_key")
        if not api_key:
            return True
        apikey_expires_at = record.data.get("apikey_expires_at")
        if isinstance(apikey_expires_at, (int, float)) and apikey_expires_at > 0:
            return time.time() > apikey_expires_at
        return True

    async def ensure_fresh_key(
        self,
        credential_id: str,
    ) -> tuple[str, CredentialRecord]:
        """Return ``(api_key, record)`` with a currently-valid key.

        Fast path — read the credential once; reuse the stored key while
        it is not yet expired (no lock, no network).

        Slow path — when stale, refresh under ``ellm:refresh:<id>`` lock
        with a freshness double-check, so concurrent refreshers for the
        same credential fetch from the gateway at most once.

        Args:
            credential_id (str): The stored ELLM credential record id.

        Returns:
            A tuple ``(api_key, record)``; ``record.data`` carries the
            freshest ``api_key`` / ``apikey_expires_at`` and any runtime
            switches (e.g. ``inject_think_tag``).
        """
        record = await self._storage.get_credential(
            self._user_id,
            credential_id,
        )
        if record is None:
            raise RuntimeError(
                "EllmKeyRefresher: credential "
                f"{credential_id!r} not found for user {self._user_id!r}",
            )
        if not self._is_expired(record):
            return record.data["api_key"], record

        lock_key = f"ellm:refresh:{credential_id}"
        async with self._message_bus.acquire_lock(
            lock_key,
            ttl_secs=self._LOCK_TTL_SECS,
        ):
            record = await self._storage.get_credential(
                self._user_id,
                credential_id,
            )
            if record is None:
                raise RuntimeError(
                    "EllmKeyRefresher: credential "
                    f"{credential_id!r} disappeared during refresh "
                    f"for user {self._user_id!r}",
                )
            if not self._is_expired(record):
                return record.data["api_key"], record
            return await self._refresh_key(record)

    async def _refresh_key(
        self,
        record: CredentialRecord,
    ) -> tuple[str, CredentialRecord]:
        """Fetch a fresh key and persist it; on failure keep the old one.

        The synchronous gateway call runs in a worker thread so the event
        loop is not blocked for the (up to 30 s) HTTP timeout.
        """
        try:
            new_key, ttl_ms = await asyncio.to_thread(
                fetch_ellm_key,
                record.data["scene_code"],
                record.data["api_key_url"],
            )
        except Exception as exc:  # noqa: BLE001 — keep serving on failure
            logger.warning(
                "EllmKeyRefresher: key refresh failed for credential %s; "
                "falling back to previous key (error=%s)",
                record.data.get("id"),
                exc,
            )
            return record.data["api_key"], record

        record.data["api_key"] = new_key
        record.data["apikey_expires_at"] = time.time() + ttl_ms / 1000
        credential_obj = CredentialFactory.from_dict(record.data)
        await self._storage.upsert_credential(self._user_id, credential_obj)
        return new_key, record


__all__ = ["fetch_ellm_key", "EllmKeyRefresher"]
