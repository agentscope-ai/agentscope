# -*- coding: utf-8 -*-
"""Tests for :class:`EllmKeyRefresher` — lazy key refresh, refresh failure
fallback, write-back, and concurrency debounce via
``MessageBus.acquire_lock``.

Migrated from the former ``AutoRefreshEllmChatModel`` tests: the refresh
state machine now lives in ``EllmKeyRefresher`` and the model is untouched
per call (``EllmKeyRefreshMiddleware`` injects the key via
``EllmChatModel.set_api_key``).

Contract under test:

- **Lazy refresh** — the stored key is reused while the credential is not
  expired.  Expiry is judged in priority order: an empty ``api_key``
  (external update cleared it) is always expired; otherwise the
  independent ``data["apikey_expires_at"]`` (Unix seconds) decides when
  present.  A record without a usable expiry stamp (legacy) is treated as
  expired, so the refresh writes the stamp and the record converges.
- **Write-back** — after a successful fetch the new key **and** the
  ``apikey_expires_at = now + ttl_ms / 1000`` stamp are persisted via
  ``StorageBase.upsert_credential`` (no raw-Redis access).  The write
  payload is a real :class:`CredentialBase` instance (the backends' type
  contract), never a raw dict.
- **Failure fallback** — when the fetch fails the previous key is kept and
  a warning is logged; the call is not interrupted.
- **Concurrency debounce** — concurrent refreshes for the same credential
  race on ``MessageBus.acquire_lock`` and re-check freshness under the
  lock (double-checked locking), so ``fetch_ellm_key`` runs at most once.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from unittest import mock

from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import CredentialRecord
from agentscope.credential import CredentialBase

from bocomadp.credential import ELLMCredential  # noqa: F401 — 导入即注册，_refresh_key 反序列化需要

from bocomadp.providers.ellm_key import EllmKeyRefresher

# The gateway issues keys valid for ~25 minutes; refresh 5 minutes early.
# Expiry is judged solely from the stored ``apikey_expires_at`` stamp; a
# record without a usable stamp is treated as expired and refreshed.
_FRESH_AGO = timedelta(minutes=5)
_EXPIRED_AGO = timedelta(minutes=30)

_KEY_URL = "http://ellm.example/ELLM-OMSERVICE/createSceneApiKey.do"

_CREDENTIAL_ID = "cred-1"


def _record(
    api_key: str = "stored-key",
    updated_at: datetime | None = None,
    apikey_expires_at: float | None = None,
    **data_extra: object,
) -> CredentialRecord:
    """Build a CredentialRecord carrying ELLM key metadata."""
    data: dict = {
        "id": _CREDENTIAL_ID,
        "type": "bocom_ellm_credential",
        "api_key": api_key,
        "base_url": "http://localhost:8000/v1",
        "scene_code": "P2024146",
        "api_key_url": _KEY_URL,
        "model": "deepseek-v4-flash",
        "inject_think_tag": False,
        **data_extra,
    }
    if apikey_expires_at is not None:
        data["apikey_expires_at"] = apikey_expires_at
    return CredentialRecord(
        user_id="user-1",
        data=data,
        updated_at=updated_at if updated_at is not None else datetime.now(),
    )


def _make_refresher(storage: object, bus: object) -> EllmKeyRefresher:
    """Build the refresher under test around a fake storage + message bus."""
    return EllmKeyRefresher(
        storage=storage,
        message_bus=bus,
        user_id="user-1",
    )


class _FakeStorage:
    """Minimal ``StorageBase`` stand-in with storage-like write semantics.

    - ``get_credential`` returns the configured record; when ``barrier`` is
      set it blocks until *two* readers have arrived, so the concurrency
      test can force both refresh paths past the expiry check before either
      fetches.
    - ``upsert_credential`` accepts the type-correct payload (a
      ``CredentialBase`` — what the real Redis/SQL backends require) as
      well as a plain dict (legacy).  It records the received argument in
      ``upsert_objects`` and the resulting record ``data`` dict in
      ``upsert_calls``, and refreshes ``updated_at`` (as the real backends
      do) — that is what lets a second lock-holder observe a fresh key
      instead of re-fetching.
    """

    def __init__(self, record: CredentialRecord, barrier: bool = False) -> None:
        self.record = record
        self.upsert_calls: list[dict] = []
        self.upsert_objects: list[CredentialBase | dict] = []
        self._barrier = barrier
        self._arrived = 0
        # Lazy — asyncio.Event must be created inside a running loop.
        self._both_read: asyncio.Event | None = None

    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord:
        if self._barrier:
            if self._both_read is None:
                self._both_read = asyncio.Event()
            self._arrived += 1
            if self._arrived >= 2:
                self._both_read.set()
            await self._both_read.wait()
        return self.record

    async def upsert_credential(
        self,
        user_id: str,
        data: CredentialBase | dict,
    ) -> str:
        self.upsert_objects.append(data)
        if isinstance(data, dict):
            self.record.data = dict(data)
        # CredentialBase input: the model already stamped record.data in
        # place (api_key / apikey_expires_at) before handing us the
        # normalized credential, so the free dict already reflects the
        # write — snapshot it as the persisted record data.
        self.upsert_calls.append(dict(self.record.data))
        self.record.updated_at = datetime.now()
        return self.record.id


class TestExpiryCheck:
    """Lazy expiry check before every refresh."""

    def test_not_expired_returns_stored_key(self) -> None:
        """A key with a future apikey_expires_at is reused; fetch_ellm_key
        is skipped."""
        storage = _FakeStorage(
            _record(
                api_key="stored-key",
                updated_at=datetime.now() - _FRESH_AGO,
                apikey_expires_at=time.time() + 1000,
            ),
        )
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
        ) as fetch:
            key, _ = asyncio.run(
                refresher.ensure_fresh_key(_CREDENTIAL_ID),
            )

        assert key == "stored-key"
        fetch.assert_not_called()
        assert storage.upsert_calls == []

    def test_expired_triggers_refresh(self) -> None:
        """A stale key triggers a fetch + write-back."""
        record = _record(
            api_key="old-key",
            updated_at=datetime.now() - _EXPIRED_AGO,
        )
        storage = _FakeStorage(record)
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch:
            key, _ = asyncio.run(
                refresher.ensure_fresh_key(_CREDENTIAL_ID),
            )

        assert key == "new-key"
        fetch.assert_called_once_with("P2024146", _KEY_URL)
        assert len(storage.upsert_calls) == 1
        assert storage.upsert_calls[0]["api_key"] == "new-key"

    def test_apikey_expires_at_future_not_expired(self) -> None:
        """apikey_expires_at in the future → no refresh, even if updated_at
        is old."""
        storage = _FakeStorage(
            _record(
                api_key="stored-key",
                updated_at=datetime.now() - _EXPIRED_AGO,  # fallback window old
                apikey_expires_at=time.time() + 1000,  # but expiry future
            ),
        )
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
        ) as fetch:
            key, _ = asyncio.run(
                refresher.ensure_fresh_key(_CREDENTIAL_ID),
            )

        assert key == "stored-key"
        fetch.assert_not_called()
        assert storage.upsert_calls == []

    def test_apikey_expires_at_past_triggers_refresh(self) -> None:
        """apikey_expires_at in the past → refresh, even if updated_at is
        fresh."""
        record = _record(
            api_key="old-key",
            updated_at=datetime.now() - _FRESH_AGO,  # updated_at still fresh
            apikey_expires_at=time.time() - 1,  # but expiry already past
        )
        storage = _FakeStorage(record)
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch:
            key, _ = asyncio.run(
                refresher.ensure_fresh_key(_CREDENTIAL_ID),
            )

        assert key == "new-key"
        fetch.assert_called_once_with("P2024146", _KEY_URL)
        assert storage.upsert_calls[0]["api_key"] == "new-key"

    def test_apikey_empty_triggers_refresh(self) -> None:
        """An empty api_key (external update cleared it) forces a refresh
        even when apikey_expires_at is still in the future."""
        record = _record(
            api_key="",
            updated_at=datetime.now() - _FRESH_AGO,
            apikey_expires_at=time.time() + 1000,  # stamp still valid
        )
        storage = _FakeStorage(record)
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch:
            key, _ = asyncio.run(
                refresher.ensure_fresh_key(_CREDENTIAL_ID),
            )

        assert key == "new-key"
        fetch.assert_called_once_with("P2024146", _KEY_URL)
        assert storage.upsert_calls[0]["api_key"] == "new-key"

    def test_missing_apikey_expires_at_treated_as_expired(self) -> None:
        """No apikey_expires_at → always treated as expired, regardless of
        how fresh updated_at is; the refresh writes the stamp and the
        record converges."""
        stale = _FakeStorage(
            _record(api_key="old-key", updated_at=datetime.now() - _EXPIRED_AGO),
        )
        refresher_stale = _make_refresher(stale, InMemoryMessageBus())
        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch:
            key, _ = asyncio.run(
                refresher_stale.ensure_fresh_key(_CREDENTIAL_ID),
            )
        assert key == "new-key"
        fetch.assert_called_once()

        fresh = _FakeStorage(
            _record(api_key="stored-key", updated_at=datetime.now() - _FRESH_AGO),
        )
        refresher_fresh = _make_refresher(fresh, InMemoryMessageBus())
        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch_fresh:
            key, _ = asyncio.run(
                refresher_fresh.ensure_fresh_key(_CREDENTIAL_ID),
            )
        assert key == "new-key"
        fetch_fresh.assert_called_once()
        assert fresh.upsert_calls[0]["api_key"] == "new-key"

    def test_refresh_writes_apikey_expires_at(self) -> None:
        """Write-back carries apikey_expires_at ≈ now + ttl_ms/1000 after a
        refresh."""
        record = _record(
            api_key="old-key",
            updated_at=datetime.now() - _EXPIRED_AGO,
        )
        storage = _FakeStorage(record)
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch:
            key, _ = asyncio.run(
                refresher.ensure_fresh_key(_CREDENTIAL_ID),
            )

        assert key == "new-key"
        fetch.assert_called_once()
        apikey_expires_at = storage.upsert_calls[0]["apikey_expires_at"]
        assert abs(apikey_expires_at - (time.time() + 1_500_000 / 1000)) < 5

    def test_refresh_passes_credential_object_not_dict(self) -> None:
        """The write-back passes a real ``CredentialBase`` instance (the
        StorageBase type contract) — not a raw dict, which would crash the
        Redis/SQL backends with an AttributeError."""
        record = _record(
            api_key="old-key",
            updated_at=datetime.now() - _EXPIRED_AGO,
        )
        storage = _FakeStorage(record)
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ):
            asyncio.run(refresher.ensure_fresh_key(_CREDENTIAL_ID))

        assert len(storage.upsert_objects) == 1
        assert isinstance(storage.upsert_objects[0], CredentialBase)
        assert not isinstance(storage.upsert_objects[0], dict)

    def test_missing_updated_at_treated_as_expired(self) -> None:
        """A credential whose updated_at is None/missing is tolerated and
        treated as expired (refresh instead of crashing)."""
        record = _record(api_key="old-key")
        record.updated_at = None  # corrupted record — must not crash
        storage = _FakeStorage(record)
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("fresh-key", 1_500_000),
        ) as fetch:
            key, _ = asyncio.run(
                refresher.ensure_fresh_key(_CREDENTIAL_ID),
            )

        assert key == "fresh-key"
        fetch.assert_called_once()
        assert storage.upsert_calls[0]["api_key"] == "fresh-key"

    def test_refresh_failure_falls_back_to_old_key(self) -> None:
        """When fetch_ellm_key raises, the previous key is used with a
        warning instead of aborting the call."""
        record = _record(
            api_key="old-key",
            updated_at=datetime.now() - _EXPIRED_AGO,
        )
        storage = _FakeStorage(record)
        refresher = _make_refresher(storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            side_effect=RuntimeError("gateway unreachable"),
        ) as fetch:
            key, _ = asyncio.run(
                refresher.ensure_fresh_key(_CREDENTIAL_ID),
            )

        assert key == "old-key"
        fetch.assert_called_once()
        assert storage.upsert_calls == []

    def test_concurrent_refresh_single_fetch(self) -> None:
        """Two concurrent ensure_fresh_key calls (shared credential) run
        fetch_ellm_key exactly once thanks to the lock + double-check."""
        storage = _FakeStorage(
            _record(api_key="old-key", updated_at=datetime.now() - _EXPIRED_AGO),
            barrier=True,  # force both readers past the expiry check first
        )
        bus = InMemoryMessageBus()
        refresher_a = _make_refresher(storage, bus)
        refresher_b = _make_refresher(storage, bus)

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch:
            async def _refresh_both() -> list[str]:
                # gather must be created inside the running loop.
                return await asyncio.gather(
                    refresher_a.ensure_fresh_key(_CREDENTIAL_ID),
                    refresher_b.ensure_fresh_key(_CREDENTIAL_ID),
                )

            results = asyncio.run(_refresh_both())

        assert [key for key, _ in results] == ["new-key", "new-key"]
        fetch.assert_called_once()
        assert len(storage.upsert_calls) == 1
