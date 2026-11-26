# -*- coding: utf-8 -*-
"""Regression tests for the session SSE replay/live seam."""

import asyncio
from contextlib import aclosing
import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from utils import MockModel

from agentscope.agent import Agent
from agentscope.app._bus_ops import publish_session_event
from agentscope.app._router._chat import chat
from agentscope.app._router._schema._chat import ChatRequest
from agentscope.app._router._session import stream_session_events
from agentscope.app._service._session_projection import SessionProjection
from agentscope.app.message_bus import InMemoryMessageBus, MessageBusKeys
from agentscope.app.storage import (
    SessionRecord,
    StorageBase,
    TeamData,
    TeamMember,
    TeamRecord,
)
from agentscope.event import (
    ConfirmResult,
    ToolResultEndEvent,
    UserConfirmResultEvent,
)
from agentscope.message import AssistantMsg, TextBlock, ToolCallBlock
from agentscope.model import ChatResponse
from agentscope.state import AgentState


class _Storage:
    """Return one session for the route ownership check."""

    async def get_session(self, *_: object) -> SessionRecord:
        """Return an existing standalone session."""
        return SessionRecord.model_construct()


class _SeamBus(InMemoryMessageBus):
    """Publish once while the route is reading its replay log."""

    def __init__(self) -> None:
        super().__init__()
        self._published = False

    async def log_read(
        self,
        key: str,
        since: str | None = None,
        max_count: int = 100,
    ) -> list[tuple[str, dict]]:
        """Put one event into both the replay and live paths."""
        if not self._published:
            self._published = True
            await publish_session_event(self, "s-1", {"sequence": 2})
        return await super().log_read(
            key,
            since=since,
            max_count=max_count,
        )


def _decode(frame: str | bytes) -> dict:
    """Decode one SSE data frame."""
    if isinstance(frame, bytes):
        frame = frame.decode()
    return json.loads(frame.removeprefix("data: ").strip())


class SessionEventStreamTest(IsolatedAsyncioTestCase):
    """The session route changes from replay to live without a gap."""

    async def test_event_at_replay_live_seam_is_delivered_once(self) -> None:
        """A seam event is deduplicated without hiding the next event."""
        bus = _SeamBus()
        await publish_session_event(bus, "s-1", {"sequence": 1})
        response = await stream_session_events(
            session_id="s-1",
            agent_id="a-1",
            user_id="u-1",
            storage=_Storage(),  # type: ignore[arg-type]
            message_bus=bus,
            after=None,
        )

        async with aclosing(response.body_iterator) as stream:
            first = _decode(await anext(stream))
            second = _decode(await anext(stream))
            snapshot = _decode(await anext(stream))
            self.assertEqual(
                (snapshot["name"], snapshot["value"]),
                ("subagent_hitl_snapshot", {"pending": []}),
            )
            await publish_session_event(bus, "s-1", {"sequence": 3})
            third = _decode(
                await asyncio.wait_for(anext(stream), timeout=1),
            )

        self.assertListEqual(
            [first, second, third],
            [{"sequence": 1}, {"sequence": 2}, {"sequence": 3}],
        )

    async def test_concurrent_reconnect_reissues_legacy_member_prompt(
        self,
    ) -> None:
        """A missing projection and unbound call recover one usable ID."""
        bus = InMemoryMessageBus()
        storage = AsyncMock(spec=StorageBase)
        worker = SessionRecord.model_construct(
            id="worker",
            agent_id="a",
            team_id="team",
            state=AgentState(),
        )
        leader = SessionRecord.model_construct(
            id="leader",
            agent_id="boss",
            team_id="team",
            state=AgentState(),
        )
        call = ToolCallBlock(id="c", name="write", input="{}", state="asking")
        worker.state.append_context("member", [call])
        history = worker.state.get_reply_message(None).model_copy(deep=True)
        storage.get_team.return_value = TeamRecord.model_construct(
            session_id=leader.id,
            data=TeamData(
                name="team",
                members=[
                    TeamMember(
                        owner_id="u",
                        agent_id="a",
                        session_id=worker.id,
                        role="invited",
                    ),
                ],
            ),
        )
        saving, release, contending = (asyncio.Event() for _ in range(3))

        def read(_user: str, _agent: str, sid: str) -> SessionRecord:
            if sid == worker.id and saving.is_set():
                contending.set()
            record = worker if sid == worker.id else leader
            return record.model_copy(deep=True)

        storage.get_session.side_effect = read
        storage.get_message.return_value = history.model_copy(deep=True)

        async def save(*, state: AgentState, **_: str) -> None:
            saving.set()
            await release.wait()
            worker.state = state.model_copy(deep=True)

        storage.update_session_state.side_effect = save

        async def reconnect() -> dict:
            response = await stream_session_events(
                leader.id,
                leader.agent_id,
                None,
                "u",
                storage,
                bus,
            )
            async with aclosing(response.body_iterator) as stream:
                return _decode(await anext(stream))["value"]

        first = asyncio.create_task(reconnect())
        try:
            await asyncio.wait_for(saving.wait(), 2)
            second = asyncio.create_task(reconnect())
            await asyncio.wait_for(contending.wait(), 2)
            self.assertEqual((first.done(), second.done()), (False, False))
        finally:
            release.set()
        cards = await asyncio.wait_for(asyncio.gather(first, second), 2)
        request = worker.state.confirmation_request(None, call.id)
        self.assertIsNotNone(request)
        self.assertEqual(cards[0], cards[1])
        storage.upsert_message.assert_awaited_once()
        history = storage.upsert_message.await_args.args[2]
        self.assertEqual(
            (cards[0]["request_id"], history.confirmation_request(call.id)),
            (request, request),
        )
        storage.update_session_state.assert_awaited_once()
        receipt = UserConfirmResultEvent(
            reply_id=history.id,
            confirm_results=[
                ConfirmResult(
                    request_id=request,
                    confirmed=False,
                    tool_call=call,
                ),
            ],
        )
        routed = await chat(
            ChatRequest(
                agent_id=leader.agent_id,
                session_id=leader.id,
                input=receipt,
            ),
            user_id="u",
            storage=storage,
            message_bus=bus,
        )
        self.assertEqual(
            (
                routed.session_id,
                worker.state.confirmation_status(None, receipt),
            ),
            (worker.id, "ready"),
        )

        ((_, trigger),) = await bus.queue_drain(MessageBusKeys.wakeup_queue())
        model = MockModel(context_size=10000)
        model.set_responses(
            [
                ChatResponse(content=[TextBlock(text="Done")], is_last=True),
            ],
        )
        member = Agent(
            name="member",
            system_prompt="Test",
            model=model,
            state=worker.state,
        )
        resumed = UserConfirmResultEvent.model_validate(trigger["input"])
        self.assertEqual(
            [
                (event.reply_id, event.tool_call_id, event.state)
                async for event in member.reply_stream(resumed)
                if isinstance(event, ToolResultEndEvent)
            ],
            [(history.id, call.id, "denied")],
        )

    async def test_reconnect_replaces_resolved_member_cards(self) -> None:
        """Each subscription lists exactly the cards still pending."""
        bus = InMemoryMessageBus()
        projection = SessionProjection(bus)
        storage = AsyncMock(spec=StorageBase)
        keys = [
            {"worker_session_id": worker, "reply_id": "r", "request_id": "p"}
            for worker in ("worker-a", "worker-b")
        ]
        cards: list[dict] = [
            dict(
                key,
                worker_agent_id="a",
                event_type="require_user_confirm",
                event={"id": "p", "tool_calls": [{"id": "call"}]},
            )
            for key in keys
        ]
        sessions = {"leader": SessionRecord.model_construct()}
        for card in cards:
            worker = card["worker_session_id"]
            reply = AssistantMsg(
                id="r",
                name="a",
                content=[
                    ToolCallBlock(
                        id="call",
                        name="check",
                        input="{}",
                        state="asking",
                    ),
                ],
            )
            reply.set_confirmation_request("call", "p")
            state = AgentState(context=[reply])
            state.reply_id = reply.id
            sessions[worker] = SessionRecord.model_construct(
                state=state,
            )
            await projection.upsert(
                "leader",
                "subagent_hitl",
                f"{worker}:r",
                card,
            )
        storage.get_session.side_effect = lambda _u, _a, sid: sessions.get(sid)
        for count in (2, 1, 0):
            with self.subTest(count=count):
                response = await stream_session_events(
                    session_id="leader",
                    agent_id="a",
                    user_id="u",
                    storage=storage,
                    message_bus=bus,
                    after=None,
                )
                async with aclosing(response.body_iterator) as stream:
                    events = [
                        _decode(await asyncio.wait_for(anext(stream), 1))
                        for _ in range(count + 1)
                    ]
                self.assertEqual(
                    [(e["name"], e["value"]) for e in events],
                    [
                        ("subagent_require_user_confirm", card)
                        for card in cards[:count]
                    ]
                    + [("subagent_hitl_snapshot", {"pending": keys[:count]})],
                )
                if count:
                    worker = cards[count - 1]["worker_session_id"]
                    sessions[worker].state.context.clear()
