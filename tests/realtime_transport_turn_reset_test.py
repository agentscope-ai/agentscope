# -*- coding: utf-8 -*-
"""A transport that dies mid-turn must retire the unbound local user turn.

The reply_stream teardown ends pending user turns so their ReplyEnd is
handed to the caller, but it must also drop the unbound identities from
``_pending_user_turns``. Otherwise, once the caller attaches a new
transport to the same (still connected) model session, the dead turn
stays first in insertion order and ``_find_user_turn``'s unbound
fallback attributes the next transcript to the reply that already
ended, instead of the freshly started one.
"""

# pylint: disable=protected-access
import asyncio
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.agent import RealtimeAgent
from agentscope.event import (
    ReplyEndEvent,
    ReplyStartEvent,
    TextBlockDeltaEvent,
)
from agentscope.realtime import SpeechTransition, VADBase
from agentscope.realtime import _events as me

from realtime_agent_test import FakeTransport, ScriptedModel


class StartOnlyVAD(VADBase):
    """STARTED on the first chunk, then nothing — the transport dies
    while the user is still talking, before any transcript settles."""

    sample_rate = 16000

    def __init__(self) -> None:
        self.seen = 0

    def push(self, pcm: bytes) -> SpeechTransition | None:
        """STARTED once; the session never gets to the second chunk."""
        self.seen += 1
        return SpeechTransition.STARTED if self.seen == 1 else None

    def reset(self) -> None:
        """Start counting again."""
        self.seen = 0


class TransportTeardownTurnTest(IsolatedAsyncioTestCase):
    """Unbound local turns must not survive their transport."""

    @staticmethod
    def _drain(agent: RealtimeAgent) -> list[tuple[Any, ...]]:
        """Read all queued user lifecycle and text events."""
        events: list[tuple[Any, ...]] = []
        while not agent._out.empty():
            event = agent._out.get_nowait().event
            if isinstance(event, ReplyStartEvent):
                events.append(("start", event.reply_id))
            elif isinstance(event, ReplyEndEvent):
                events.append(("end", event.reply_id))
            elif isinstance(event, TextBlockDeltaEvent):
                events.append(("text", event.reply_id, event.delta))
        return events

    async def test_transport_end_retires_unbound_local_turns(self) -> None:
        """A transcript after reconnect lands on the new turn, not the
        dead one ended by the transport teardown."""
        model = ScriptedModel([[]], input_audio_transcription=True)
        agent = RealtimeAgent("Friday", "be brief", model, vad=StartOnlyVAD())

        async with agent:
            # Run 1: the only audio frame starts a local (unbound) user
            # turn; the transport then dies before any transcript for it
            # can settle.
            transport = FakeTransport(frames=1)
            run1: list[tuple[Any, ...]] = []
            async with transport:
                async for event in agent.reply_stream(transport):
                    if isinstance(event, ReplyStartEvent):
                        run1.append(("start", event.reply_id))
                    elif isinstance(event, ReplyEndEvent):
                        run1.append(("end", event.reply_id))
            self.assertEqual(
                [kind for kind, *_ in run1],
                ["start", "end"],
                "the dead transport must hand back the turn's end",
            )
            dead_id = run1[0][1]

            # Run 2: a fresh transport on the same model session. The
            # user starts speaking again — a new local turn opens.
            await agent._forward_audio(b"", SpeechTransition.STARTED)
            run2_start = self._drain(agent)
            self.assertEqual(run2_start, [("start", run2_start[0][1])])
            new_id = run2_start[0][1]
            self.assertNotEqual(new_id, dead_id)

            # The provider settles the new turn's transcript. It must
            # land on the new turn — not be captured by the dead one.
            await agent._on_model_event(
                me.InputTranscriptionEvent(item_id="u9", text="hello"),
            )
            settled = self._drain(agent)

        self.assertEqual(
            settled,
            [("text", new_id, "hello"), ("end", new_id)],
            "the transcript must attach to the new turn",
        )
        self.assertEqual(
            [
                (message.id, message.get_text_content())
                for message in agent.state.context
                if message.role == "user"
            ],
            [(new_id, "hello")],
            "context must hold the transcript under the new turn's id",
        )
