# -*- coding: utf-8 -*-
"""Lazy auto-refreshing ELLM chat model.

``AutoRefreshEllmChatModel`` extends :class:`EllmChatModel` with the BOCOM
ELLM gateway key-rotation scheme, adapted from deer-flow's
``EllmApiKeyManager`` but re-architected for the AgentScope outer product:

- **Lazy refresh** — the key is stored in a user-scoped credential record
  (via the ``StorageBase`` interface, never a raw Redis connection).  Every
  ``_call_api`` checks whether the record is still within its validity
  window; ``fetch_ellm_key`` is invoked only when the key is close to
  expiry (25 min TTL, refreshed 5 min early).
- **Concurrency debounce** — the refresh runs inside
  ``MessageBus.acquire_lock`` (the same distributed lock ``ChatService``
  uses), so concurrent calls cannot stampede the key service.  The record
  is re-read under the lock and freshness double-checked, so a caller that
  lost the race simply reuses the key the winner stored.
- **Failure fallback** — a failed refresh only logs a warning and the
  previous key is kept, so transient gateway failures never interrupt a
  model call.
- **Per-call injection** — ``_call_api`` forwards the fresh key as
  ``Authorization: Bearer <key>`` via ``extra_headers`` and (re)reads the
  ``inject_think_tag`` switch from the credential data, so the think-tag
  behavior can be toggled at runtime without recreating the model.

Usage::

    from agentscope.app.message_bus import MessageBus
    from agentscope.app.storage import StorageBase
    from bocomadp.providers.auto_refresh_ellm_model import AutoRefreshEllmChatModel

    model = AutoRefreshEllmChatModel(
        storage=storage,
        message_bus=message_bus,
        user_id=user_id,
        credential_id=credential_id,
        credential=ellm_credential,
        model="Qwen3-235B-A22B",
    )
    response = await model(messages)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator

from agentscope.app.message_bus import MessageBus
from agentscope.app.storage import CredentialRecord, StorageBase
from agentscope.credential import CredentialFactory
from agentscope.message import Msg
from agentscope.model import ChatResponse, EllmChatModel
from agentscope.tool import ToolChoice

from bocomadp.providers.ellm_key import fetch_ellm_key

logger = logging.getLogger(__name__)

# The gateway issues keys valid for ~25 minutes; refresh 5 minutes early.
# The stored key's expiry is judged solely from the independent
# ``record.data["apikey_expires_at"]`` (Unix seconds, stamped on every
# refresh); a record without a usable stamp is treated as expired, so the
# refresh writes it and the record converges.  An empty ``api_key`` (e.g.
# the frontend cleared it on update) forces an immediate refresh regardless
# of any expiry stamp.
# Distributed-lock lease for a single key refresh — a crash while holding
# it delays the next refresh by at most this long.
_LOCK_TTL_SECS = 30


class AutoRefreshEllmChatModel(EllmChatModel):
    """An :class:`EllmChatModel` that lazily refreshes its ELLM api key.

    All key state lives in the user-scoped credential record identified by
    ``credential_id``.  The record's ``data`` dict is expected to carry
    ``api_key``, ``scene_code``, ``api_key_url`` and (optionally)
    ``inject_think_tag``; the independent ``data["apikey_expires_at"]``
    (Unix seconds) drives the expiry check — a record without a usable
    stamp is treated as expired (the refresh writes it, so the record
    converges), and an empty ``api_key`` (external update cleared it) is
    immediately expired.

    Args:
        storage (StorageBase): Credential read/write backend — accessed
            only through ``get_credential`` / ``upsert_credential``.
        message_bus (MessageBus): Transport used for the refresh lock
            (``acquire_lock``); ``InMemoryMessageBus`` in tests.
        user_id (str): Owner of the credential record.
        credential_id (str): The stored ELLM credential record id.
        *args: Forwarded to :class:`EllmChatModel` (credential, model, ...).
        **kwargs: Forwarded to :class:`EllmChatModel`.
    """

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        user_id: str,
        credential_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        self._storage = storage
        self._message_bus = message_bus
        self._user_id = user_id
        self._credential_id = credential_id
        # The most recent credential record read by ``_ensure_fresh_key``;
        # ``_call_api`` reads the think-tag switch from it.
        self._record: CredentialRecord | None = None
        super().__init__(*args, **kwargs)

    # ------------------------------------------------------------------
    # Expiry check
    # ------------------------------------------------------------------

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
            # External update may have cleared the key — refresh it.
            return True
        apikey_expires_at = record.data.get("apikey_expires_at")
        if isinstance(apikey_expires_at, (int, float)) and apikey_expires_at > 0:
            # Independent apikey expiry moment — refreshed write-backs stamp it.
            return time.time() > apikey_expires_at
        # No usable expiry stamp (legacy record) — treat as expired so the
        # refresh writes apikey_expires_at and the record converges.
        return True

    # ------------------------------------------------------------------
    # Key refresh
    # ------------------------------------------------------------------

    async def _ensure_fresh_key(self) -> str:
        """Return a currently-valid ELLM api key, refreshing if needed.

        Fast path — read the credential once; reuse the stored key while
        it is not yet expired (no lock, no network).

        Slow path — when stale, refresh under ``ellm:refresh:<id>`` lock
        with a freshness double-check, so concurrent refreshers for the
        same credential fetch from the gateway at most once.
        """
        record = await self._storage.get_credential(
            self._user_id,
            self._credential_id,
        )
        if record is None:
            raise RuntimeError(
                "AutoRefreshEllmChatModel: credential "
                f"{self._credential_id!r} not found for user {self._user_id!r}",
            )
        self._record = record
        if not self._is_expired(record):
            return record.data["api_key"]

        lock_key = f"ellm:refresh:{self._credential_id}"
        async with self._message_bus.acquire_lock(
            lock_key,
            ttl_secs=_LOCK_TTL_SECS,
        ):
            # Re-read under the lock: another caller may have refreshed
            # while we waited — if so, reuse their key instead of fetching.
            record = await self._storage.get_credential(
                self._user_id,
                self._credential_id,
            )
            if record is None:
                raise RuntimeError(
                    "AutoRefreshEllmChatModel: credential "
                    f"{self._credential_id!r} disappeared during refresh "
                    f"for user {self._user_id!r}",
                )
            self._record = record
            if not self._is_expired(record):
                return record.data["api_key"]
            return await self._refresh_key(record)

    async def _refresh_key(self, record: CredentialRecord) -> str:
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
                "AutoRefreshEllmChatModel: key refresh failed for credential "
                "%s; falling back to previous key (error=%s)",
                self._credential_id,
                exc,
            )
            return record.data["api_key"]

        record.data["api_key"] = new_key
        # Stamp the independent apikey expiry (Unix seconds) from the real
        # remaining TTL the gateway reported — this is the primary expiry
        # judgement for the next _is_expired check, decoupled from updated_at.
        record.data["apikey_expires_at"] = time.time() + ttl_ms / 1000
        # StorageBase.upsert_credential expects a CredentialBase (not the
        # raw data dict) — the Redis/SQL backends read credential_data.name
        # / dump via model_dump, so a dict would raise AttributeError.
        credential_obj = CredentialFactory.from_dict(record.data)
        await self._storage.upsert_credential(self._user_id, credential_obj)
        return new_key

    # ------------------------------------------------------------------
    # Model call
    # ------------------------------------------------------------------

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **generate_kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the ELLM API with a freshly-refreshed key.

        Before delegating to ``EllmChatModel._call_api``:

        1. ``_ensure_fresh_key`` lazily refreshes the ELLM api key.
        2. The key is forwarded as ``extra_headers`` →
           ``Authorization: Bearer <key>``.
        3. ``inject_think_tag`` is (re)read from the stored credential, so
           the runtime toggle is honoured without a model restart.
        """
        key = await self._ensure_fresh_key()
        record = self._record
        self.inject_think_tag = bool(
            record.data.get("inject_think_tag", False) if record else False,
        )
        generate_kwargs["extra_headers"] = {
            "Authorization": f"Bearer {key}",
        }
        return await super()._call_api(
            model_name,
            messages,
            tools=tools,
            tool_choice=tool_choice,
            **generate_kwargs,
        )


__all__ = ["AutoRefreshEllmChatModel"]
