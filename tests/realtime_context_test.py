# -*- coding: utf-8 -*-
"""Context checkpoint and proactive reconnect behavior."""

# pylint: disable=protected-access, useless-return
import asyncio
from types import SimpleNamespace
from typing import Any, AsyncIterator
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from pydantic import SecretStr
from utils import MockModel

from agentscope.agent import RealtimeAgent, RealtimeContextConfig
from agentscope.credential import DashScopeCredential
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.model import StructuredResponse
from agentscope.realtime import (
    AudioFrame,
    PlayoutPosition,
    RealtimeModelBase,
    RealtimeModelCard,
    SpeechTransition,
    VADBase,
)
from agentscope.realtime import _events as me


class ContextModel(RealtimeModelBase):
    """A session model that can pause its second connection."""

    type = "context-test"
    supports_ready_ack = True
    supports_text_input = True

    def __init__(self) -> None:
        super().__init__(
            "context-test",
            DashScopeCredential(api_key=SecretStr("sk-test")),
            model_card=RealtimeModelCard(
                name="context-test",
                label="Context test",
                max_context_tokens=100,
                input_sample_rate=16000,
            ),
        )
        self.connections = 0
        self.instructions: list[str] = []
        self.audio: list[bytes] = []
        self.delivered: list[bytes | str] = []
        self.tool_results: list[ToolResultBlock] = []
        self.second_started = asyncio.Event()
        self.allow_second = asyncio.Event()
        self.fail_second = False
        self._closed = asyncio.Event()

    async def connect(
        self,
        instructions: str,
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> None:
        """Record instructions and pause the second connection."""
        self.connections += 1
        self.instructions.append(instructions)
        self._closed.clear()
        if self.connections == 2:
            self.second_started.set()
            await self.allow_second.wait()
            if self.fail_second:
                raise ConnectionError("second session failed")

    async def close(self) -> None:
        """End the current event stream."""
        self._closed.set()

    async def events(self) -> AsyncIterator[me.ModelEvent]:
        """Wait until close ends the session."""
        await self._closed.wait()
        yield me.SessionEndedEvent(reason="closed")

    async def push_audio(self, pcm: bytes) -> None:
        """Record a delivered frame."""
        self.audio.append(pcm)
        self.delivered.append(pcm)

    async def push_text(self, text: str) -> None:
        """Record a delivered text turn."""
        self.delivered.append(text)

    async def push_tool_result(self, block: ToolResultBlock) -> None:
        """Record the provider-visible result."""
        self.tool_results.append(block)

    async def commit_turn(self) -> None:
        """No local turn detection is used."""

    async def request_response(self) -> None:
        """No model response is generated."""

    async def cancel_response(self) -> None:
        """No model response is generated."""

    async def truncate(
        self,
        item_id: str,
        played_ms: int,
        played_text: str,
    ) -> None:
        """No response needs truncation."""


class CountingVAD(VADBase):
    """Track whether a reconnect clears audio already analyzed locally."""

    sample_rate = 16000

    def __init__(self) -> None:
        self.seen = 0
        self.resets = 0

    def push(self, pcm: bytes) -> SpeechTransition | None:
        """Observe one frame without producing a boundary."""
        self.seen += 1
        return None

    def reset(self) -> None:
        """Record a reset of VAD history."""
        self.resets += 1
        self.seen = 0


class RealtimeContextTest(IsolatedAsyncioTestCase):
    """The checkpoint preserves live messages and reconnect audio."""

    def setUp(self) -> None:
        """Prepare a fake compression model and short context limit."""
        self.compressor = MockModel()
        setattr(
            self.compressor,
            "generate_structured_output",
            AsyncMock(
                return_value=StructuredResponse(
                    content={"task_overview": "Summary of A B C"},
                ),
            ),
        )
        self.model = ContextModel()
        self.config = RealtimeContextConfig(
            compression_model=self.compressor,
            context_length=100,
            summary_template="{task_overview}",
        )
        self.agent = RealtimeAgent(
            "assistant",
            "Be brief.",
            self.model,
            context_config=self.config,
        )

    async def asyncTearDown(self) -> None:
        """Stop any background tasks left by a test."""
        self.model.allow_second.set()
        await self.agent.close()

    def _seed_context(self) -> None:
        """Create four equally sized completed messages."""
        self.agent.state.context = [
            UserMsg(name="user", content=f"{letter} " * 20)
            for letter in "ABCD"
        ]

    async def test_snapshot_keeps_tail_and_messages_added_during_summary(
        self,
    ) -> None:
        """The summary replaces only the immutable old prefix."""
        self._seed_context()
        started = asyncio.Event()
        release = asyncio.Event()

        async def summarize(**_kwargs: object) -> StructuredResponse:
            started.set()
            await release.wait()
            return StructuredResponse(content={"task_overview": "A B C"})

        setattr(
            self.compressor,
            "generate_structured_output",
            AsyncMock(side_effect=summarize),
        )
        task = asyncio.create_task(self.agent._compress_context())
        await asyncio.wait_for(started.wait(), 1)
        self.agent.state.context.extend(
            [
                UserMsg(name="user", content="E"),
                UserMsg(name="user", content="F"),
            ],
        )
        release.set()
        await task
        self.assertEqual(self.agent.state.summary, "A B C")
        self.assertEqual(
            [m.get_text_content() for m in self.agent.state.context],
            ["D " * 20, "E", "F"],
        )

    async def test_modified_prefix_rejects_stale_summary(self) -> None:
        """An in-place edit of snapshotted content invalidates the result."""
        self._seed_context()
        started = asyncio.Event()
        release = asyncio.Event()

        async def summarize(**_kwargs: object) -> StructuredResponse:
            started.set()
            await release.wait()
            return StructuredResponse(content={"task_overview": "stale"})

        setattr(
            self.compressor,
            "generate_structured_output",
            AsyncMock(side_effect=summarize),
        )
        task = asyncio.create_task(self.agent._compress_context())
        await asyncio.wait_for(started.wait(), 1)
        self.agent.state.context[0].content = [TextBlock(text="A changed")]
        release.set()
        await task
        self.assertEqual(self.agent.state.summary, "")
        self.assertEqual(len(self.agent.state.context), 4)

    async def test_pressure_uses_latest_usage_and_provider_limits(
        self,
    ) -> None:
        """Latest response usage is not accumulated across turns."""
        await self.agent._on_model_event(
            me.ResponseDoneEvent(item_id="r1", input_tokens=80),
        )
        self.assertEqual(self.agent._context_pressure(), 0.8)
        await self.agent._on_model_event(
            me.ResponseDoneEvent(item_id="r2", input_tokens=75),
        )
        self.assertEqual(self.agent._context_pressure(), 0.75)
        self.model.card.max_audio_turns = 8
        self.agent._audio_turns = 7
        self.assertEqual(self.agent._context_pressure(), 7 / 8)
        self.model.card.max_audio_duration_s = 10
        self.agent._audio_seconds = 9
        self.assertEqual(self.agent._context_pressure(), 0.9)

    async def test_response_done_starts_background_compression(self) -> None:
        """A completed response triggers compression at the soft limit."""
        self._seed_context()
        await self.agent._on_model_event(
            me.ResponseDoneEvent(
                item_id="response",
                input_tokens=75,
                output_tokens=1,
            ),
        )
        self.agent.state.context.append(UserMsg(name="user", content="E"))
        task = self.agent._compression_task
        assert task is not None
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.agent.state.summary, "Summary of A B C")
        self.assertEqual(
            self.agent.state.context[-1].get_text_content(),
            "E",
        )
        messages = self.compressor.generate_structured_output.call_args.kwargs[
            "messages"
        ]
        self.assertNotIn("E", [m.get_text_content() for m in messages])

    async def test_hard_limit_rolls_over_with_raw_history(self) -> None:
        """A limit still renews the session when there is no old prefix."""
        self.agent.state.context = [UserMsg(name="user", content="one")]
        self.model.allow_second.set()
        await self.agent.connect()
        await self.agent._on_model_event(
            me.ResponseDoneEvent(
                item_id="response",
                input_tokens=90,
                output_tokens=1,
            ),
        )
        task = self.agent._rollover_task
        assert task is not None
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.model.connections, 2)
        self.assertIn("one", self.model.instructions[1])

    async def test_summary_rolls_over_without_provider_opt_in(self) -> None:
        """Any ready-capable model uses the shared rollover flow."""
        self._seed_context()
        await self.agent.connect()
        await self.agent._on_model_event(
            me.ResponseDoneEvent(item_id="response", input_tokens=75),
        )
        await asyncio.wait_for(self.model.second_started.wait(), 1)
        self.model.allow_second.set()
        task = self.agent._rollover_task
        assert task is not None
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.model.connections, 2)
        self.assertIn("Summary of A B C", self.model.instructions[1])
        self.assertIn("D " * 20, self.model.instructions[1])

    async def test_reconnect_replays_audio_in_order_with_summary(self) -> None:
        """Input arriving during the new connection reaches it in order."""
        self.agent.state.summary = "Earlier summary"
        self.agent.state.context = [UserMsg(name="user", content="D")]
        await self.agent.connect()
        self.agent._rollover_pending = True
        self.agent._maybe_schedule_rollover()
        await asyncio.wait_for(self.model.second_started.wait(), 1)
        self.assertTrue(self.agent._rotating)
        await self.agent._on_audio(me_audio(b"a"))
        await self.agent.send("typed")
        await self.agent._on_audio(me_audio(b"b"))
        self.model.allow_second.set()
        assert self.agent._rollover_task is not None
        await asyncio.wait_for(self.agent._rollover_task, 1)
        self.assertEqual(self.model.audio, [b"a", b"b"])
        self.assertEqual(self.model.delivered, [b"a", "typed", b"b"])
        self.assertEqual(
            self.agent.state.context[-1].get_text_content(),
            "typed",
        )
        self.assertIn("Earlier summary", self.model.instructions[1])
        self.assertIn("D", self.model.instructions[1])
        self.assertEqual(len(self.agent._backlog), 0)

    async def test_tool_result_matches_provider_after_truncation(self) -> None:
        """The provider and local state receive the same limited result."""
        self.config.tool_result_limit = 10
        offloader = SimpleNamespace(
            offload_tool_result=AsyncMock(return_value="workspace://tool"),
        )
        self.agent.offloader = offloader
        call = ToolCallBlock(id="call-1", name="lookup", input="{}")
        await self.agent._report_tool(
            "reply",
            call,
            "x" * 500,
        )
        local = self.agent.state.context[-1].content[-1]
        assert isinstance(local, ToolResultBlock)
        self.assertEqual(local, self.model.tool_results[-1])
        self.assertIn("truncated", local.output)
        self.assertIn("workspace://tool", local.output)
        self.assertEqual(
            offloader.offload_tool_result.call_args.args[1].output,
            "x" * 500,
        )

    async def test_compressed_prefix_is_offloaded(self) -> None:
        """The original prefix remains available after a checkpoint."""
        self._seed_context()
        offloader = SimpleNamespace(
            offload_context=AsyncMock(return_value="workspace://context"),
        )
        self.agent.offloader = offloader
        await self.agent._compress_context()
        self.assertIn("workspace://context", self.agent.state.summary)
        self.assertEqual(
            len(offloader.offload_context.call_args.args[1]),
            3,
        )

    async def test_rollover_waits_for_audio_playout(self) -> None:
        """A complete response may still have audio in the speaker queue."""
        self.agent._connected = True
        self.agent._last_audio_item = "reply"
        self.agent._last_audio_ms = 100
        position = PlayoutPosition(item_id="reply", played_ms=20)
        self.agent._transport = SimpleNamespace(playout=lambda: position)
        self.assertFalse(self.agent._safe_to_rollover())
        position.played_ms = 100
        self.assertTrue(self.agent._safe_to_rollover())

    async def test_rollover_waits_for_pending_response(self) -> None:
        """Speech end alone is not a safe boundary for a new session."""
        await self.agent.connect()
        await self.agent._on_model_event(me.SpeechStartedEvent(item_id="u"))
        await self.agent._on_model_event(me.SpeechEndedEvent(item_id="u"))
        self.agent._rollover_pending = True
        self.agent._maybe_schedule_rollover()
        await asyncio.sleep(0)
        self.assertEqual(self.model.connections, 1)
        self.assertFalse(self.agent._safe_to_rollover())
        await self.agent._on_model_event(me.ResponseCreatedEvent(item_id="r"))
        await self.agent._on_model_event(me.ResponseDoneEvent(item_id="r"))
        self.model.allow_second.set()
        task = self.agent._rollover_task
        assert task is not None
        await asyncio.wait_for(task, 1)
        self.assertEqual(self.model.connections, 2)

    async def test_late_response_done_does_not_clear_new_turn(self) -> None:
        """A cancelled reply cannot make the next user turn look idle."""
        self.agent._finished_item = "old"
        self.agent._awaiting_response = True
        await self.agent._on_model_event(me.ResponseDoneEvent(item_id="old"))
        self.assertTrue(self.agent._awaiting_response)

    async def test_failed_rollover_keeps_audio_for_retry(self) -> None:
        """A failed new session leaves buffered input available to retry."""
        await self.agent.connect()
        self.model.fail_second = True
        self.agent._rollover_pending = True
        self.agent._maybe_schedule_rollover()
        await asyncio.wait_for(self.model.second_started.wait(), 1)
        await self.agent._on_audio(me_audio(b"a"))
        self.model.allow_second.set()
        task = self.agent._rollover_task
        assert task is not None
        await asyncio.wait_for(task, 1)
        self.assertFalse(self.agent._connected)
        self.assertEqual(len(self.agent._backlog), 1)
        self.assertTrue(await self.agent._ensure_connected())
        self.assertEqual(self.model.connections, 3)
        self.assertEqual(self.model.audio, [b"a"])
        self.assertEqual(len(self.agent._backlog), 0)

    async def test_backlog_overflow_is_explicit(self) -> None:
        """A full backlog reports an error without discarding older audio."""
        self.config.max_audio_backlog_s = 0.0001
        self.agent._buffer_audio(b"a", None)
        with self.assertRaises(BufferError):
            self.agent._buffer_audio(b"b" * 10, None)
        self.assertEqual(list(self.agent._backlog), [(b"a", None)])

    async def test_reconnect_keeps_vad_state_for_buffered_speech(self) -> None:
        """The next live frame continues the same local VAD window."""
        vad = CountingVAD()
        self.agent.vad = vad
        self.agent._rotating = True
        await self.agent._on_audio(me_audio(b"a"))
        self.assertEqual(vad.seen, 1)
        self.agent._rotating = False
        await self.agent.connect()
        self.assertEqual(vad.resets, 0)
        self.assertEqual(vad.seen, 1)


def me_audio(pcm: bytes) -> AudioFrame:
    """Wrap one PCM chunk as a transport audio frame."""
    return AudioFrame(pcm=pcm)
