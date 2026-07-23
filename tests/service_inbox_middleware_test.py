# -*- coding: utf-8 -*-
# pylint: disable=abstract-method,protected-access
"""Tests for :class:`InboxMiddleware`.

Every cross-session message delivery in the framework (team messages
via ``TeamSay`` / ``AgentCreate``, background-tool completion results,
scheduler fires) goes through this middleware on the consumer side, so
the four branches below cover the whole inbox→context pipeline:

- empty inbox → no injection, no event yield, downstream unchanged;
- last context msg already an assistant msg from this agent → hints are
  *extended* into its ``content``;
- last context msg is something else (system / different agent) → a
  fresh ``AssistantMsg`` is appended;
- empty context → a fresh ``AssistantMsg`` is appended.

Each non-empty drain must also yield one ``HintBlockEvent`` per hint so
the SSE stream renders them.
"""
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Callable
from unittest import IsolatedAsyncioTestCase

from utils import AnyString

from agentscope.app.message_bus import MessageBus
from agentscope.app.middleware import InboxMiddleware
from agentscope.message import (
    AssistantMsg,
    HintBlock,
    SystemMsg,
    TextBlock,
)


class _FakeBus(MessageBus):
    """In-memory bus that only implements the inbox API needed by
    :class:`InboxMiddleware`. All other primitives raise ``NotImplemented``
    to make accidental dependencies obvious."""

    def __init__(self) -> None:
        self._queues: dict[str, list[tuple[str, dict]]] = {}
        self._next_id = 0

    def _alloc_id(self) -> str:
        """Allocate a monotonically increasing entry id."""
        self._next_id += 1
        return f"id-{self._next_id}"

    # Mode A — drain queue
    async def queue_push(
        self,
        key: str,
        payload: dict,
        *,
        ttl_secs: int | None = None,
    ) -> str:
        entry_id = self._alloc_id()
        self._queues.setdefault(key, []).append((entry_id, payload))
        return entry_id

    async def queue_drain(
        self,
        key: str,
        *,
        max_count: int,
    ) -> list[tuple[str, dict]]:
        entries = self._queues.get(key, [])[:max_count]
        self._queues[key] = self._queues.get(key, [])[max_count:]
        return entries

    async def queue_delete(self, key: str) -> None:
        self._queues.pop(key, None)

    # Mode C — log (unused)
    async def log_append(
        self,
        key: str,
        payload: dict,
        *,
        max_len: int | None = None,
        ttl_secs: int | None = None,
    ) -> str:
        raise NotImplementedError

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        raise NotImplementedError

    async def log_trim(
        self,
        key: str,
        before_id: str | None = None,
    ) -> None:
        raise NotImplementedError

    # Mode D — pub/sub (unused)
    async def publish(self, key: str, payload: dict) -> None:
        raise NotImplementedError

    async def subscribe(
        self,
        key: str,
        *,
        on_ready: Callable[[], None] | None = None,
    ) -> AsyncGenerator[dict, None]:
        raise NotImplementedError
        yield  # pragma: no cover  # pylint: disable=unreachable

    # Mode E — lock (unused)
    @asynccontextmanager
    async def acquire_lock(
        self,
        key: str,
        *,
        ttl_secs: int = 600,
    ) -> AsyncGenerator[None, None]:
        raise NotImplementedError
        yield  # pragma: no cover  # pylint: disable=unreachable

    async def is_locked(self, key: str) -> bool:
        raise NotImplementedError

    # Mode F — registry (unused)
    async def registry_set(
        self,
        namespace: str,
        field: str,
        value: str,
        *,
        ttl_secs: int | None = None,
    ) -> None:
        raise NotImplementedError

    async def registry_del(self, namespace: str, field: str) -> None:
        raise NotImplementedError

    async def registry_exists(self, namespace: str, field: str) -> bool:
        raise NotImplementedError

    async def registry_getall(self, namespace: str) -> dict[str, str]:
        raise NotImplementedError

    async def registry_drop(self, namespace: str) -> None:
        raise NotImplementedError


def _make_agent(
    *,
    name: str,
    session_id: str,
    reply_id: str,
    context: list,
) -> Any:
    """Build the smallest object that satisfies what
    :class:`InboxMiddleware.on_reasoning` reads off ``agent``.

    Args:
        name: Agent display name.
        session_id: Identifier for the inbox key.
        reply_id: Reply id stamped onto freshly-appended AssistantMsg.
        context: Mutable list of messages — the middleware mutates this
            in place to inject hints.

    Returns:
        A ``SimpleNamespace`` shaped like the real :class:`Agent` for
        the fields the middleware touches.
    """
    return SimpleNamespace(
        name=name,
        state=SimpleNamespace(
            session_id=session_id,
            reply_id=reply_id,
            context=context,
        ),
    )


async def _noop_next_handler(**_kwargs: Any) -> AsyncGenerator:
    """Stand-in for the downstream reasoning chain. Yields nothing —
    InboxMiddleware should run its inbox-drain step first, then exit
    the iteration cleanly."""
    return
    yield  # pragma: no cover  # pylint: disable=unreachable


async def _drain(gen: AsyncGenerator) -> list:
    """Collect every value yielded by an async generator."""
    out: list = []
    async for item in gen:
        out.append(item)
    return out


def _push_hint(
    bus: _FakeBus,
    sid: str,
    hint: HintBlock,
) -> None:
    """Push a :class:`HintBlock` into the per-session inbox key the
    middleware will drain."""
    key = MessageBus._INBOX_KEY.format(sid=sid)
    # asyncio.run on the bus would be overkill — `_queues` is a plain
    # dict, mutate it directly.
    bus._queues.setdefault(key, []).append(
        (bus._alloc_id(), hint.model_dump(mode="json")),
    )


class TestInboxMiddlewareEmptyInbox(IsolatedAsyncioTestCase):
    """When the inbox is empty, ``on_reasoning`` injects nothing, yields
    nothing, and delegates straight to ``next_handler``."""

    async def test_empty_inbox_is_noop(self) -> None:
        """An empty inbox produces no events and leaves context untouched."""
        bus = _FakeBus()
        agent = _make_agent(
            name="A",
            session_id="s",
            reply_id=uuid.uuid4().hex,
            context=[],
        )
        mw = InboxMiddleware(bus)

        out = await _drain(
            mw.on_reasoning(agent, {}, _noop_next_handler),
        )

        self.assertEqual(out, [])
        self.assertEqual(agent.state.context, [])


class TestInboxMiddlewareInjection(IsolatedAsyncioTestCase):
    """Branch coverage for the three injection cases."""

    async def test_extends_into_last_assistant_msg_from_same_agent(
        self,
    ) -> None:
        """When the last context msg is already an assistant msg from
        this agent, hints are extended into its ``content`` and a new
        msg is NOT appended."""
        bus = _FakeBus()
        existing = AssistantMsg(
            name="A",
            content=[TextBlock(text="hello")],
        )
        agent = _make_agent(
            name="A",
            session_id="s",
            reply_id=uuid.uuid4().hex,
            context=[existing],
        )
        hint = HintBlock(hint="poke", source="tester")
        _push_hint(bus, "s", hint)
        mw = InboxMiddleware(bus)

        events = await _drain(
            mw.on_reasoning(agent, {}, _noop_next_handler),
        )

        # No new message appended; hint extended into existing assistant msg.
        self.assertEqual(len(agent.state.context), 1)
        self.assertIs(agent.state.context[0], existing)
        self.assertDictEqual(
            existing.model_dump(),
            {
                "id": AnyString(),
                "name": "A",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": "hello", "id": AnyString()},
                    {
                        "type": "hint",
                        "hint": "poke",
                        "id": hint.id,
                        "source": "tester",
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "usage": None,
            },
        )
        # One HintBlockEvent yielded.
        self.assertEqual(
            [e.model_dump(mode="json") for e in events],
            [
                {
                    "type": "HINT_BLOCK",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "reply_id": agent.state.reply_id,
                    "block_id": hint.id,
                    "source": "tester",
                    "hint": "poke",
                },
            ],
        )

    async def test_appends_new_msg_when_last_is_different_agent(
        self,
    ) -> None:
        """When the last context msg is from a different agent (or a
        system msg), a fresh :class:`AssistantMsg` is appended with the
        hints as its content."""
        bus = _FakeBus()
        system_msg = SystemMsg(name="system", content="boot")
        agent = _make_agent(
            name="A",
            session_id="s",
            reply_id="rid-1",
            context=[system_msg],
        )
        hint = HintBlock(hint="hi", source="x")
        _push_hint(bus, "s", hint)
        mw = InboxMiddleware(bus)

        await _drain(mw.on_reasoning(agent, {}, _noop_next_handler))

        self.assertEqual(len(agent.state.context), 2)
        self.assertDictEqual(
            agent.state.context[-1].model_dump(),
            {
                "id": "rid-1",
                "name": "A",
                "role": "assistant",
                "content": [
                    {
                        "type": "hint",
                        "hint": "hi",
                        "id": hint.id,
                        "source": "x",
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "usage": None,
            },
        )

    async def test_appends_new_msg_when_context_empty(self) -> None:
        """When ``agent.state.context`` is empty, a fresh
        :class:`AssistantMsg` containing the hints is the first entry."""
        bus = _FakeBus()
        agent = _make_agent(
            name="A",
            session_id="s",
            reply_id="rid-empty",
            context=[],
        )
        hint = HintBlock(hint="hi", source="x")
        _push_hint(bus, "s", hint)
        mw = InboxMiddleware(bus)

        await _drain(mw.on_reasoning(agent, {}, _noop_next_handler))

        self.assertEqual(len(agent.state.context), 1)
        self.assertDictEqual(
            agent.state.context[0].model_dump(),
            {
                "id": "rid-empty",
                "name": "A",
                "role": "assistant",
                "content": [
                    {
                        "type": "hint",
                        "hint": "hi",
                        "id": hint.id,
                        "source": "x",
                    },
                ],
                "metadata": {},
                "created_at": AnyString(),
                "finished_at": None,
                "finished_reason": None,
                "structured_output": None,
                "error": None,
                "usage": None,
            },
        )


class TestInboxMiddlewareYieldsHintBlockEvents(IsolatedAsyncioTestCase):
    """One ``HintBlockEvent`` is yielded per injected hint, in order,
    each carrying the hint's own block_id / source / content."""

    async def test_event_count_and_payload(self) -> None:
        """One HintBlockEvent is yielded per inbox hint, in arrival order."""
        bus = _FakeBus()
        agent = _make_agent(
            name="A",
            session_id="s",
            reply_id="rid-evt",
            context=[],
        )
        h1 = HintBlock(hint="a", source="alice")
        h2 = HintBlock(hint="b", source="bob")
        _push_hint(bus, "s", h1)
        _push_hint(bus, "s", h2)
        mw = InboxMiddleware(bus)

        events = await _drain(
            mw.on_reasoning(agent, {}, _noop_next_handler),
        )

        self.assertEqual(
            [e.model_dump(mode="json") for e in events],
            [
                {
                    "type": "HINT_BLOCK",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "reply_id": "rid-evt",
                    "block_id": h1.id,
                    "source": "alice",
                    "hint": "a",
                },
                {
                    "type": "HINT_BLOCK",
                    "id": AnyString(),
                    "created_at": AnyString(),
                    "metadata": {},
                    "reply_id": "rid-evt",
                    "block_id": h2.id,
                    "source": "bob",
                    "hint": "b",
                },
            ],
        )


class TestInboxMiddlewareDelegatesDownstream(IsolatedAsyncioTestCase):
    """After the inbox is drained, events from ``next_handler`` come
    out of the middleware in order."""

    async def test_downstream_events_pass_through(self) -> None:
        """Downstream events appear after any HintBlockEvents."""
        bus = _FakeBus()
        agent = _make_agent(
            name="A",
            session_id="s",
            reply_id="rid",
            context=[],
        )
        hint = HintBlock(hint="hi", source="x")
        _push_hint(bus, "s", hint)
        mw = InboxMiddleware(bus)

        async def downstream(**_k: Any) -> AsyncGenerator[str, None]:
            yield "ds-1"
            yield "ds-2"

        out = await _drain(mw.on_reasoning(agent, {}, downstream))

        # 1 HintBlockEvent followed by 2 downstream items.
        self.assertEqual(len(out), 3)
        self.assertDictEqual(
            out[0].model_dump(mode="json"),
            {
                "type": "HINT_BLOCK",
                "id": AnyString(),
                "created_at": AnyString(),
                "metadata": {},
                "reply_id": "rid",
                "block_id": hint.id,
                "source": "x",
                "hint": "hi",
            },
        )
        self.assertEqual(out[1:], ["ds-1", "ds-2"])
