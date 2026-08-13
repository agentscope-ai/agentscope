# -*- coding: utf-8 -*-
"""Tests for :class:`EllmKeyRefreshMiddleware` — the agent middleware that
refreshes the ELLM api key and injects it into the model per call.

Covers:

- Only ``EllmChatModel`` instances get the key injected; other models pass
  through untouched (same instance).
- The model instance is NOT replaced: the fresh key lands on the same
  instance via ``set_api_key`` and the think-tag switch is synced from the
  credential record.
- End-to-end through a real :class:`Agent`: an expired credential triggers
  a refresh, the new key is sent as ``Authorization: Bearer <key>``, the
  think-tag switch is honoured, and the refreshed key is persisted.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime
from typing import Any
from unittest import mock

from agentscope.agent import Agent
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import CredentialRecord
from agentscope.app.storage._utils import _dump_with_secrets
from agentscope.message import UserMsg

from bocomadp.credential import ELLMCredential
from bocomadp.middleware.ellm_refresh import EllmKeyRefreshMiddleware
from bocomadp.providers.ellm_chat_model import EllmChatModel

_KEY_URL = "http://ellm.example/createSceneApiKey.do"

_CREDENTIAL_ID = "cred-1"


class _FakeStorage:
    """StorageBase stand-in with realistic write semantics (dict data)."""

    def __init__(self, record: CredentialRecord) -> None:
        self.record = record
        self.upsert_calls = 0

    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord:
        return self.record

    async def upsert_credential(self, user_id: str, cred_obj: Any) -> str:
        self.record.data = _dump_with_secrets(cred_obj)
        self.record.updated_at = datetime.now()
        self.upsert_calls += 1
        return self.record.id


def _record(api_key: str, expires_at: float | None) -> CredentialRecord:
    return CredentialRecord(
        user_id="user-1",
        data={
            "id": _CREDENTIAL_ID,
            "type": "bocom_ellm_credential",
            "api_key": api_key,
            "base_url": "http://localhost:8000/v1",
            "scene_code": "P2024146",
            "api_key_url": _KEY_URL,
            "model": "Qwen3-235B-A22B",
            "inject_think_tag": True,
            "apikey_expires_at": expires_at,
        },
    )


def _base_model() -> EllmChatModel:
    return EllmChatModel(
        credential=ELLMCredential(
            id=_CREDENTIAL_ID,
            api_key="placeholder",
            base_url="http://localhost:8000/v1",
            model="Qwen3-235B-A22B",
        ),
        model="Qwen3-235B-A22B",
    )


async def _dummy_next(**kwargs: Any) -> Any:
    """Record the current_model that would reach the real model call."""
    _dummy_next.seen = kwargs.get("current_model")
    return _dummy_next.seen


class _MockAsyncStream:
    def __init__(self, text: str) -> None:
        self._chunks = [self._chunk(text)]
        self._index = 0

    @staticmethod
    def _chunk(text: str) -> Any:
        c = mock.MagicMock()
        c.id = "resp-1"
        c.usage = None
        d = mock.MagicMock()
        d.content = text
        d.reasoning_content = None
        d.tool_calls = None
        ch = mock.MagicMock()
        ch.delta = d
        c.choices = [ch]
        return c

    async def __aenter__(self) -> "_MockAsyncStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def __aiter__(self) -> "_MockAsyncStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        c = self._chunks[self._index]
        self._index += 1
        return c


class TestInjection:
    """Middleware per-call key injection behaviour."""

    def test_ellm_model_gets_key_injected(self) -> None:
        """An EllmChatModel receives the fresh key + think switch on the
        SAME instance (no class swap, no client rebuild)."""
        storage = _FakeStorage(_record("old-key", time.time() - 1800))
        mw = EllmKeyRefreshMiddleware(storage, InMemoryMessageBus(), "user-1")
        base = _base_model()
        base.client = mock.MagicMock()

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key-abc", 1_500_000),
        ):
            result = asyncio.run(
                mw.on_model_call(
                    agent=None,
                    input_kwargs={"current_model": base},
                    next_handler=_dummy_next,
                ),
            )

        assert result is base
        assert _dummy_next.seen is base
        assert base._api_key_override == "new-key-abc"
        assert base.inject_think_tag is True
        assert storage.upsert_calls == 1
        assert storage.record.data["api_key"] == "new-key-abc"

    def test_non_ellm_model_passes_through(self) -> None:
        """Non-EllmChatModel models are returned untouched."""
        mw = EllmKeyRefreshMiddleware(
            _FakeStorage(_record("k", time.time() + 3600)),
            InMemoryMessageBus(),
            "user-1",
        )

        result = asyncio.run(
            mw.on_model_call(
                agent=None,
                input_kwargs={"current_model": "not-a-model"},
                next_handler=_dummy_next,
            ),
        )

        assert result == "not-a-model"
        assert _dummy_next.seen == "not-a-model"

    def test_missing_credential_id_passes_through(self) -> None:
        """A model without a credential id is not injected."""
        storage = _FakeStorage(_record("k", time.time() + 3600))
        mw = EllmKeyRefreshMiddleware(storage, InMemoryMessageBus(), "user-1")
        no_id = _base_model()
        no_id.credential.id = None

        result = asyncio.run(
            mw.on_model_call(
                agent=None,
                input_kwargs={"current_model": no_id},
                next_handler=_dummy_next,
            ),
        )

        assert result is no_id
        assert no_id._api_key_override is None


class TestEndToEnd:
    """Full agent loop: expired credential → refresh → inject → think."""

    def test_agent_reply_refreshes_and_injects(self) -> None:
        storage = _FakeStorage(_record("old-key", time.time() - 1800))
        mw = EllmKeyRefreshMiddleware(storage, InMemoryMessageBus(), "user-1")

        base = _base_model()
        mock_client = mock.MagicMock()
        mock_client.chat.completions.create = mock.AsyncMock(
            return_value=_MockAsyncStream("Hello"),
        )
        base.client = mock_client
        agent = Agent(name="a", system_prompt="sys", model=base, middlewares=[mw])

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key-abc", 1_500_000),
        ) as fetch:
            final = asyncio.run(
                agent.reply(UserMsg(name="user", content="hi")),
            )

        assert fetch.call_count == 1
        assert mock_client.chat.completions.create.call_args.kwargs[
            "extra_headers"
        ] == {"Authorization": "Bearer new-key-abc"}
        assert final.get_text_content() == "<think>Hello"
        assert storage.record.data["api_key"] == "new-key-abc"
        assert storage.upsert_calls == 1
        # The agent's model is the SAME instance — never swapped.
        assert agent.model is base
