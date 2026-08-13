# -*- coding: utf-8 -*-
"""End-to-end integration tests: an :class:`agentscope.agent.Agent` that uses
:class:`EllmChatModel` with :class:`EllmKeyRefreshMiddleware` as its model.

Beyond the unit-level contracts covered in ``test_ellm_key_refresher.py``
(expiry check / refresh / fallback / concurrency) and
``test_ellm_refresh_middleware.py`` (per-call injection), these tests drive
the model through the real :class:`Agent` reasoning loop
(``agent.reply``) and assert the full chain:

1. **Expired → refresh → inject → think** — a stale credential (``updated_at``
   >= 20 min ago) triggers ``fetch_ellm_key``; the fresh key is written back
   via ``StorageBase.upsert_credential``; the OpenAI ``create`` call receives
   ``extra_headers: {"Authorization": "Bearer new-key"}``; and the ``<think>``
   tag lands in front of the first streamed text block because the credential's
   ``inject_think_tag`` switch is (re)read at call time.
2. **Fresh → reuse stored key** — an unexpired credential is used as-is:
   ``fetch_ellm_key`` is never called, nothing is written back, and the gateway
   sees ``Authorization: Bearer stored-key``.
3. **Think tag off → no prefix** — ``inject_think_tag=False`` (or absent)
   yields streamed text without the ``<think>`` prefix.

All I/O is mocked: the key service via ``fetch_ellm_key`` and the gateway via
a mock ``openai.AsyncClient`` (``_MockAsyncStream``/``_make_stream_chunk``
patterns from ``tests/model_openai_chat_test.py``).
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta
from typing import Any
from unittest import mock

from agentscope.agent import Agent
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.storage import CredentialRecord
from agentscope.credential import CredentialBase

from bocomadp.credential import ELLMCredential
from bocomadp.middleware.ellm_refresh import EllmKeyRefreshMiddleware
from agentscope.message import SystemMsg, UserMsg

from bocomadp.providers.ellm_chat_model import EllmChatModel

# The gateway issues keys valid for ~25 minutes; refresh 5 minutes early,
# so a record is stale once its updated_at is older than 20 minutes.
_FRESH_AGO = timedelta(minutes=5)
_EXPIRED_AGO = timedelta(minutes=30)

_KEY_URL = "http://ellm.example/ELLM-OMSERVICE/createSceneApiKey.do"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _record(
    api_key: str = "stored-key",
    updated_at: datetime | None = None,
    **data_extra: object,
) -> CredentialRecord:
    """Build a CredentialRecord carrying ELLM key metadata."""
    data: dict = {
        "id": "cred-1",
        "type": "bocom_ellm_credential",
        "api_key": api_key,
        "base_url": "http://localhost:8000/v1",
        "scene_code": "P2024146",
        "api_key_url": _KEY_URL,
        "model": "deepseek-v4-flash",
        "inject_think_tag": False,
        **data_extra,
    }
    return CredentialRecord(
        user_id="user-1",
        data=data,
        updated_at=updated_at if updated_at is not None else datetime.now(),
    )


def _credential() -> "ELLMCredential":
    """A dummy ELLMCredential satisfying EllmChatModel.__init__."""
    return ELLMCredential(
        id="cred-1",
        api_key="dummy",
        base_url="http://localhost:8000/v1",
        scene_code="P2024146",
        api_key_url=_KEY_URL,
        model="deepseek-v4-flash",
    )


def _make_model() -> EllmChatModel:
    """Build the model under test."""
    return EllmChatModel(
        credential=_credential(),
        model="Qwen3-235B-A22B",
    )


def _make_agent(model: EllmChatModel, storage: object, bus: object) -> Agent:
    """Build an Agent wrapping the model (empty toolkit is fine)."""
    return Agent(
        name="ellm-assistant",
        system_prompt="You are a helpful assistant.",
        model=model,
        middlewares=[EllmKeyRefreshMiddleware(storage, bus, "user-1")],
    )


def _make_stream_chunk(
    delta_text: str | None = None,
    has_choices: bool = True,
    usage: dict | None = None,
) -> Any:
    """Build a single mock streaming chunk."""
    chunk = mock.MagicMock()
    chunk.id = "resp-1"

    if usage:
        chunk.usage = mock.MagicMock()
        chunk.usage.prompt_tokens = usage.get("prompt_tokens", 0)
        chunk.usage.completion_tokens = usage.get("completion_tokens", 0)
        chunk.usage.prompt_tokens_details = None
    else:
        chunk.usage = None

    if has_choices:
        delta = mock.MagicMock()
        delta.content = delta_text
        delta.reasoning_content = None
        delta.reasoning = None
        delta.audio = None
        delta.tool_calls = None
        choice = mock.MagicMock()
        choice.delta = delta
        chunk.choices = [choice]
    else:
        chunk.choices = []

    return chunk


class _MockAsyncStream:
    """Mock async stream that acts as an async context manager + iterator."""

    def __init__(self, chunks: list) -> None:
        self._chunks = chunks
        self._index = 0

    async def __aenter__(self) -> "_MockAsyncStream":
        return self

    async def __aexit__(self, *args: Any) -> None:
        pass

    def __aiter__(self) -> "_MockAsyncStream":
        return self

    async def __anext__(self) -> Any:
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk


class _FakeStorage:
    """Minimal ``StorageBase`` stand-in with storage-like write semantics.

    ``get_credential`` returns the configured record; ``upsert_credential``
    accepts both the type-correct ``CredentialBase`` payload and a plain
    dict, records the write and refreshes ``updated_at`` (as the real
    backends do).
    """

    def __init__(self, record: CredentialRecord) -> None:
        self.record = record
        self.upsert_calls: list[dict] = []

    async def get_credential(
        self,
        user_id: str,
        credential_id: str,
    ) -> CredentialRecord:
        return self.record

    async def upsert_credential(
        self,
        user_id: str,
        data: CredentialBase | dict,
    ) -> str:
        if isinstance(data, dict):
            self.record.data = dict(data)
        # CredentialBase input: the model stamped record.data in place
        # before the call, so the record already reflects the write.
        self.upsert_calls.append(dict(self.record.data))
        self.record.updated_at = datetime.now()
        return self.record.id


def _make_mock_client(chunks: list) -> tuple[mock.MagicMock, mock.AsyncMock]:
    """Replace the model's openai client with a mock create() over ``chunks``.

    Returns ``(mock_client, mock_create)``.
    """
    mock_client = mock.MagicMock()
    mock_create = mock.AsyncMock(return_value=_MockAsyncStream(chunks))
    mock_client.chat.completions.create = mock_create
    return mock_client, mock_create


# A single text delta followed by a trailing usage-only chunk — the same shape
# the real ELLM gateway emits for a short streaming answer.
def _text_stream_chunks(delta_text: str = "Hello") -> list:
    return [
        _make_stream_chunk(delta_text=delta_text),
        _make_stream_chunk(
            has_choices=False,
            usage={"prompt_tokens": 10, "completion_tokens": 3},
        ),
    ]


# ---------------------------------------------------------------------------
# Scenario 1 — expired credential: refresh → write-back → header → <think>
# ---------------------------------------------------------------------------


class TestAgentExpiredRefresh:
    """Todo 13 — an Agent replying through an expired credential triggers the
    lazy refresh and honours the credential's ``inject_think_tag`` switch."""

    def test_agent_reply_refreshes_key_and_injects_think(self) -> None:
        """End-to-end via ``agent.reply``: stale key is refreshed, persisted,
        forwarded as ``Authorization``, and the think tag is injected."""
        record = _record(
            api_key="old-key",
            updated_at=datetime.now() - _EXPIRED_AGO,
            inject_think_tag=True,
        )
        storage = _FakeStorage(record)
        model = _make_model()
        mock_client, mock_create = _make_mock_client(
            _text_stream_chunks(delta_text="Hello"),
        )
        model.client = mock_client

        agent = _make_agent(model, storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch:
            final_msg = asyncio.run(
                agent.reply(UserMsg(name="user", content="Hello")),
            )

        # a. Expired → fetch_ellm_key was called for the stored scene/url.
        fetch.assert_called_once_with("P2024146", _KEY_URL)

        # b. The fresh key was written back via upsert_credential.
        assert len(storage.upsert_calls) == 1
        assert storage.upsert_calls[0]["api_key"] == "new-key"

        # c. The gateway call carried the refreshed key.
        assert mock_create.call_args.kwargs["extra_headers"] == {
            "Authorization": "Bearer new-key",
        }

        # d. The think-tag switch was (re)read from the credential and the
        #    injected tag survives into the agent's final reply.
        text = final_msg.get_text_content()
        assert text == "<think>Hello"

    def test_first_stream_text_block_has_think_prefix(self) -> None:
        """Directly on ``agent._call_model``: the *first* streamed text block
        carries the ``<think>`` prefix (the literal acceptance criterion)."""
        record = _record(
            api_key="old-key",
            updated_at=datetime.now() - _EXPIRED_AGO,
            inject_think_tag=True,
        )
        storage = _FakeStorage(record)
        model = _make_model()
        mock_client, mock_create = _make_mock_client(
            _text_stream_chunks(delta_text="Hello"),
        )
        model.client = mock_client

        agent = _make_agent(model, storage, InMemoryMessageBus())

        async def _call() -> list:
            messages = [
                SystemMsg(
                    name="system",
                    content="You are a helpful assistant.",
                ),
                UserMsg(name="user", content="Hello"),
            ]
            gen = await agent._call_model(messages, tools=[])
            return [r async for r in gen]

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ):
            responses = asyncio.run(_call())

        # The first yielded chunk is the first text delta.
        first_text_block = next(
            b for b in responses[0].content if b.type == "text"
        )
        assert first_text_block.text.startswith("<think>")
        assert first_text_block.text == "<think>Hello"
        assert mock_create.call_args.kwargs["extra_headers"] == {
            "Authorization": "Bearer new-key",
        }


# ---------------------------------------------------------------------------
# Scenario 2 — fresh credential: no refresh, stored key reused
# ---------------------------------------------------------------------------


class TestAgentFreshKeyReuse:
    """Todo 13 — an unexpired credential short-circuits the refresh path."""

    def test_agent_reply_reuses_stored_key(self) -> None:
        """A key fetched < 20 min ago is reused as-is: no fetch, no write-back,
        and the gateway receives ``Authorization: Bearer stored-key``."""
        record = _record(
            api_key="stored-key",
            updated_at=datetime.now() - _FRESH_AGO,
            apikey_expires_at=time.time() + 3600,  # future → not expired
            inject_think_tag=True,
        )
        storage = _FakeStorage(record)
        model = _make_model()
        mock_client, mock_create = _make_mock_client(
            _text_stream_chunks(delta_text="Hello"),
        )
        model.client = mock_client

        agent = _make_agent(model, storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
            return_value=("new-key", 1_500_000),
        ) as fetch:
            asyncio.run(agent.reply(UserMsg(name="user", content="Hello")))

        # fetch_ellm_key must not be called for a fresh key.
        fetch.assert_not_called()
        assert storage.upsert_calls == []
        assert mock_create.call_args.kwargs["extra_headers"] == {
            "Authorization": "Bearer stored-key",
        }


# ---------------------------------------------------------------------------
# Scenario 3 — inject_think_tag off/missing: no <think> prefix
# ---------------------------------------------------------------------------


class TestAgentThinkTagDisabled:
    """Todo 13 — without the think-tag switch the reply has no prefix."""

    def test_agent_reply_with_switch_false_has_no_think_prefix(self) -> None:
        """``inject_think_tag=False`` in the credential data → no tag."""
        record = _record(
            api_key="stored-key",
            updated_at=datetime.now() - _FRESH_AGO,
            apikey_expires_at=time.time() + 3600,  # future → not expired
            inject_think_tag=False,
        )
        storage = _FakeStorage(record)
        model = _make_model()
        mock_client, _ = _make_mock_client(_text_stream_chunks())
        model.client = mock_client

        agent = _make_agent(model, storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
        ) as fetch:
            final_msg = asyncio.run(
                agent.reply(UserMsg(name="user", content="Hello")),
            )

        text = final_msg.get_text_content()
        assert text == "Hello"
        assert not text.startswith("<think>")
        fetch.assert_not_called()

    def test_agent_reply_with_missing_switch_has_no_think_prefix(self) -> None:
        """An absent ``inject_think_tag`` key defaults to no tag."""
        record = _record(
            api_key="stored-key",
            updated_at=datetime.now() - _FRESH_AGO,
            apikey_expires_at=time.time() + 3600,  # future → not expired
        )
        del record.data["inject_think_tag"]  # switch absent from storage

        storage = _FakeStorage(record)
        model = _make_model()
        mock_client, _ = _make_mock_client(_text_stream_chunks())
        model.client = mock_client

        agent = _make_agent(model, storage, InMemoryMessageBus())

        with mock.patch(
            "bocomadp.providers.ellm_key.fetch_ellm_key",
        ):
            final_msg = asyncio.run(
                agent.reply(UserMsg(name="user", content="Hello")),
            )

        text = final_msg.get_text_content()
        assert text == "Hello"
        assert not text.startswith("<think>")
