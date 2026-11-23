# -*- coding: utf-8 -*-
"""Unit tests for TTSMiddleware."""
import asyncio
import base64
from typing import Any, AsyncGenerator
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agentscope import set_id_factory
from agentscope.event import (
    CustomEvent,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
)
from agentscope.message import Base64Source, DataBlock
from agentscope.middleware import TTSMiddleware
from agentscope.tts import TTSModelBase, TTSResponse

_EXCLUDE = {"id", "created_at", "metadata"}


def _dump(evt: Any) -> dict:
    """Dump an event to a dict excluding auto-generated fields."""
    return evt.model_dump(exclude=_EXCLUDE)


def _make_tts_response(
    data: str,
    media_type: str = "audio/wav",
) -> TTSResponse:
    return TTSResponse(
        content=DataBlock(
            source=Base64Source(data=data, media_type=media_type),
        ),
    )


def _make_agent_stub(
    reply_id: str = "reply-1",
    name: str = "agent",
) -> MagicMock:
    agent = MagicMock()
    agent.name = name
    agent.state.reply_id = reply_id
    return agent


class TestTTSMiddlewareNonRealtime(IsolatedAsyncioTestCase):
    """Tests for non-realtime (non-streaming-input) TTS path."""

    async def test_synthesize_on_text_block_end(self) -> None:
        """After TextBlockEnd, synthesize() is called with accumulated text
        and DATA_BLOCK_START + DATA_BLOCK_DELTA + DATA_BLOCK_END are emitted.
        """
        audio_b64 = base64.b64encode(b"\x00\x01\x02").decode()

        tts = MagicMock(spec=TTSModelBase)
        tts.realtime = False
        tts.__aenter__ = AsyncMock(return_value=tts)
        tts.__aexit__ = AsyncMock(return_value=None)
        tts.synthesize = AsyncMock(
            return_value=_make_tts_response(audio_b64),
        )

        middleware = TTSMiddleware(tts_model=tts)
        agent = _make_agent_stub()

        upstream_events = [
            TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="blk-1",
                delta="Hello ",
            ),
            TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="blk-1",
                delta="world",
            ),
            TextBlockEndEvent(reply_id="reply-1", block_id="blk-1"),
        ]

        async def next_handler(**_kwargs: Any) -> AsyncGenerator:
            for evt in upstream_events:
                yield evt

        emitted = []
        async for evt in middleware.on_reply(agent, {}, next_handler):
            emitted.append(evt)

        # Upstream events are passed through
        self.assertEqual(
            [_dump(e) for e in emitted[:3]],
            [_dump(e) for e in upstream_events],
        )

        # After TextBlockEnd: START + DELTA + END
        block_id = emitted[3].block_id
        self.assertEqual(
            [_dump(e) for e in emitted[3:]],
            [
                {
                    "type": "DATA_BLOCK_START",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "name": None,
                },
                {
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "data": audio_b64,
                    "url": None,
                },
                {
                    "type": "DATA_BLOCK_END",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                },
            ],
        )

        # synthesize was called with the full text
        tts.synthesize.assert_called_once_with("Hello world")

    async def test_empty_text_skips_synthesize(self) -> None:
        """When accumulated text is whitespace-only, synthesize is not called
        and no DATA_BLOCK events are emitted."""
        tts = MagicMock(spec=TTSModelBase)
        tts.realtime = False
        tts.__aenter__ = AsyncMock(return_value=tts)
        tts.__aexit__ = AsyncMock(return_value=None)
        tts.synthesize = AsyncMock()

        middleware = TTSMiddleware(tts_model=tts)
        agent = _make_agent_stub()

        upstream_events = [
            TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="blk-1",
                delta="   ",
            ),
            TextBlockEndEvent(reply_id="reply-1", block_id="blk-1"),
        ]

        async def next_handler(**_kwargs: Any) -> AsyncGenerator:
            for evt in upstream_events:
                yield evt

        emitted = []
        async for evt in middleware.on_reply(agent, {}, next_handler):
            emitted.append(evt)

        # Only the upstream events are passed through
        self.assertEqual(
            [_dump(e) for e in emitted],
            [_dump(e) for e in upstream_events],
        )
        tts.synthesize.assert_not_called()

    async def test_audio_block_id_uses_configured_id_factory(self) -> None:
        """TTS audio DATA_BLOCK events use the configured ID factory."""
        import agentscope._utils._common as common

        # pylint: disable=protected-access
        saved_factory = common._id_factory
        self.addCleanup(setattr, common, "_id_factory", saved_factory)
        set_id_factory(lambda: "custom-audio-id")

        tts = MagicMock(spec=TTSModelBase)
        tts.realtime = False
        tts.__aenter__ = AsyncMock(return_value=tts)
        tts.__aexit__ = AsyncMock(return_value=None)
        tts.synthesize = AsyncMock(
            return_value=_make_tts_response("AAAA"),
        )

        middleware = TTSMiddleware(tts_model=tts)
        agent = _make_agent_stub()

        async def next_handler(**_kwargs: Any) -> AsyncGenerator:
            yield TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="blk-1",
                delta="hi",
            )
            yield TextBlockEndEvent(reply_id="reply-1", block_id="blk-1")

        emitted = [
            evt async for evt in middleware.on_reply(agent, {}, next_handler)
        ]

        self.assertEqual(
            [evt.block_id for evt in emitted[2:]],
            ["custom-audio-id"] * 3,
        )

    async def test_streaming_output_multiple_chunks(self) -> None:
        """When synthesize() returns an async generator, each chunk produces
        a DATA_BLOCK_DELTA under the same block_id."""
        chunk1 = _make_tts_response("AAAA")
        chunk2 = _make_tts_response("BBBB")

        async def synth_gen(*_args: Any, **_kwargs: Any) -> AsyncGenerator:
            yield chunk1
            yield chunk2

        tts = MagicMock(spec=TTSModelBase)
        tts.realtime = False
        tts.__aenter__ = AsyncMock(return_value=tts)
        tts.__aexit__ = AsyncMock(return_value=None)
        tts.synthesize = AsyncMock(return_value=synth_gen())

        middleware = TTSMiddleware(tts_model=tts)
        agent = _make_agent_stub()

        upstream_events = [
            TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="blk-1",
                delta="hi",
            ),
            TextBlockEndEvent(reply_id="reply-1", block_id="blk-1"),
        ]

        async def next_handler(**_kwargs: Any) -> AsyncGenerator:
            for evt in upstream_events:
                yield evt

        emitted = []
        async for evt in middleware.on_reply(agent, {}, next_handler):
            emitted.append(evt)

        # Upstream pass-through + START + 2x DELTA + END
        data_events = emitted[2:]
        block_id = data_events[0].block_id
        self.assertEqual(
            [_dump(e) for e in data_events],
            [
                {
                    "type": "DATA_BLOCK_START",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "name": None,
                },
                {
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "data": "AAAA",
                    "url": None,
                },
                {
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "data": "BBBB",
                    "url": None,
                },
                {
                    "type": "DATA_BLOCK_END",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                },
            ],
        )


class TestTTSMiddlewareRealtime(IsolatedAsyncioTestCase):
    """Tests for realtime (streaming-input) TTS path."""

    async def test_push_on_delta_and_drain_on_end(self) -> None:
        """In realtime mode, push() is called on each TextBlockDelta and
        synthesize() drains on TextBlockEnd."""
        push_audio = _make_tts_response("PUSH1")
        drain_audio = _make_tts_response("DRAIN")

        tts = MagicMock(spec=TTSModelBase)
        tts.realtime = True
        tts.__aenter__ = AsyncMock(return_value=tts)
        tts.__aexit__ = AsyncMock(return_value=None)
        tts.push = AsyncMock(return_value=push_audio)
        tts.synthesize = AsyncMock(return_value=drain_audio)

        middleware = TTSMiddleware(tts_model=tts)
        agent = _make_agent_stub()

        upstream_events = [
            TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="blk-1",
                delta="Hello",
            ),
            TextBlockEndEvent(reply_id="reply-1", block_id="blk-1"),
        ]

        async def next_handler(**_kwargs: Any) -> AsyncGenerator:
            for evt in upstream_events:
                yield evt

        emitted = []
        async for evt in middleware.on_reply(agent, {}, next_handler):
            emitted.append(evt)

        # push() called with the delta text
        tts.push.assert_called_once_with("Hello")

        # synthesize() called to drain
        tts.synthesize.assert_called_once()

        data_events = [
            e
            for e in emitted
            if isinstance(
                e,
                (DataBlockStartEvent, DataBlockDeltaEvent, DataBlockEndEvent),
            )
        ]
        # START + DELTA(push) + DELTA(drain) + END
        block_id = data_events[0].block_id
        self.assertEqual(
            [_dump(e) for e in data_events],
            [
                {
                    "type": "DATA_BLOCK_START",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "name": None,
                },
                {
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "data": "PUSH1",
                    "url": None,
                },
                {
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "data": "DRAIN",
                    "url": None,
                },
                {
                    "type": "DATA_BLOCK_END",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                },
            ],
        )

    async def test_push_returns_none_no_audio_emitted(self) -> None:
        """When push() returns empty content, no DATA_BLOCK events are emitted
        until synthesize() drains."""
        empty_response = TTSResponse(content=None)
        drain_audio = _make_tts_response("FINAL")

        tts = MagicMock(spec=TTSModelBase)
        tts.realtime = True
        tts.__aenter__ = AsyncMock(return_value=tts)
        tts.__aexit__ = AsyncMock(return_value=None)
        tts.push = AsyncMock(return_value=empty_response)
        tts.synthesize = AsyncMock(return_value=drain_audio)

        middleware = TTSMiddleware(tts_model=tts)
        agent = _make_agent_stub()

        upstream_events = [
            TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="blk-1",
                delta="Hi",
            ),
            TextBlockDeltaEvent(
                reply_id="reply-1",
                block_id="blk-1",
                delta=" there",
            ),
            TextBlockEndEvent(reply_id="reply-1", block_id="blk-1"),
        ]

        async def next_handler(**_kwargs: Any) -> AsyncGenerator:
            for evt in upstream_events:
                yield evt

        emitted = []
        async for evt in middleware.on_reply(agent, {}, next_handler):
            emitted.append(evt)

        # push called twice but produced no audio
        self.assertEqual(tts.push.call_count, 2)

        data_events = [
            e
            for e in emitted
            if isinstance(
                e,
                (DataBlockStartEvent, DataBlockDeltaEvent, DataBlockEndEvent),
            )
        ]
        # Only drain produces: START + DELTA + END
        block_id = data_events[0].block_id
        self.assertEqual(
            [_dump(e) for e in data_events],
            [
                {
                    "type": "DATA_BLOCK_START",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "name": None,
                },
                {
                    "type": "DATA_BLOCK_DELTA",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                    "media_type": "audio/wav",
                    "data": "FINAL",
                    "url": None,
                },
                {
                    "type": "DATA_BLOCK_END",
                    "reply_id": "reply-1",
                    "block_id": block_id,
                },
            ],
        )


class TestTTSFailureIsolation(IsolatedAsyncioTestCase):
    """TTS failures preserve text and never swallow upstream cancellation."""

    def setUp(self) -> None:
        self.tts = MagicMock(spec=TTSModelBase)
        self.tts.realtime = False
        self.tts.__aenter__ = AsyncMock(return_value=self.tts)
        self.tts.__aexit__ = AsyncMock(return_value=None)
        self.tts.synthesize = AsyncMock(return_value=None)
        self.tts.push = AsyncMock(return_value=None)
        self.events = [
            event
            for block in ("first", "second")
            for event in (
                TextBlockDeltaEvent(
                    reply_id="reply-1",
                    block_id=block,
                    delta=block,
                ),
                TextBlockEndEvent(reply_id="reply-1", block_id=block),
            )
        ]

    async def _text(self, **_kwargs: Any) -> AsyncGenerator:
        for event in self.events:
            yield event

    async def _collect(self, continue_on_error: bool | None = True) -> list:
        options = (
            {}
            if continue_on_error is None
            else {"continue_on_error": continue_on_error}
        )
        return [
            event
            async for event in TTSMiddleware(self.tts, **options).on_reply(
                _make_agent_stub(),
                {},
                self._text,
            )
        ]

    async def test_init_push_synthesis_and_cleanup_failures_preserve_text(
        self,
    ) -> None:
        """A provider failure disables only speech for the current reply."""
        for operation in ("__aenter__", "push", "synthesize", "__aexit__"):
            with self.subTest(operation=operation):
                self.tts.realtime = operation == "push"
                failing = getattr(self.tts, operation)
                failing.side_effect = RuntimeError("provider unavailable")
                stage, position = {
                    "__aenter__": ("initialization", 0),
                    "push": ("synthesis", 1),
                    "synthesize": ("synthesis", 2),
                    "__aexit__": ("cleanup", len(self.events)),
                }[operation]
                expected = list(self.events)
                expected.insert(
                    position,
                    CustomEvent(
                        name="tts_error",
                        value={"reply_id": "reply-1", "stage": stage},
                    ),
                )
                self.assertEqual(
                    [_dump(event) for event in await self._collect()],
                    [_dump(event) for event in expected],
                )
                failing.assert_awaited_once()
                failing.side_effect = None
                self.tts.reset_mock()

    async def test_stream_failure_closes_audio_block_and_continues_text(
        self,
    ) -> None:
        """Partially emitted audio is closed before the next text block."""

        async def audio() -> AsyncGenerator:
            yield _make_tts_response("AAAA")
            raise RuntimeError("stream disconnected")

        self.tts.synthesize.return_value = audio()
        result = await self._collect()
        self.assertEqual(
            [
                _dump(event)
                for event in result
                if isinstance(
                    event,
                    (TextBlockDeltaEvent, TextBlockEndEvent),
                )
            ],
            [_dump(event) for event in self.events],
        )
        block_id = result[2].block_id
        expected_audio = [
            DataBlockStartEvent(
                reply_id="reply-1",
                block_id=block_id,
                media_type="audio/wav",
            ),
            DataBlockDeltaEvent(
                reply_id="reply-1",
                block_id=block_id,
                media_type="audio/wav",
                data="AAAA",
            ),
            DataBlockEndEvent(reply_id="reply-1", block_id=block_id),
        ]
        self.assertEqual(
            [_dump(event) for event in result],
            [
                _dump(event)
                for event in self.events[:2]
                + expected_audio
                + [
                    CustomEvent(
                        name="tts_error",
                        value={"reply_id": "reply-1", "stage": "synthesis"},
                    ),
                ]
                + self.events[2:]
            ],
        )
        self.tts.synthesize.assert_awaited_once_with("first")
        self.tts.__aexit__.assert_awaited_once()

    async def test_upstream_errors_and_cancellation_propagate(self) -> None:
        """Text failures and cancellation bypass TTS exception handling."""
        for error in (
            RuntimeError("text model failed"),
            asyncio.CancelledError(),
        ):
            with self.subTest(error=type(error)):

                async def next_handler(
                    failure: BaseException = error,
                    **_kwargs: Any,
                ) -> AsyncGenerator:
                    yield self.events[0]
                    raise failure

                with self.assertRaises(type(error)):
                    async for _ in TTSMiddleware(self.tts).on_reply(
                        _make_agent_stub(),
                        {},
                        next_handler,
                    ):
                        pass
                self.tts.__aexit__.assert_awaited_once()
                self.tts.reset_mock()

        self.tts.synthesize.side_effect = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self._collect()
        self.tts.__aexit__.assert_awaited_once()

    async def test_sdk_default_propagates_provider_failures(self) -> None:
        """Existing SDK callers retain exceptions unless they opt in."""
        for operation in ("__aenter__", "push", "synthesize", "__aexit__"):
            with self.subTest(operation=operation):
                self.tts.realtime = operation == "push"
                failing = getattr(self.tts, operation)
                error = RuntimeError("provider failure")
                failing.side_effect = error
                try:
                    with self.assertRaises(RuntimeError) as ctx:
                        await self._collect(continue_on_error=None)
                    self.assertIs(ctx.exception, error)
                finally:
                    failing.side_effect = None
                    self.tts.reset_mock()

    async def test_consumer_athrow_propagates_and_closes_audio(self) -> None:
        """An exception injected at any audio yield is never a TTS failure."""
        for continue_on_error in (False, True):
            for target in (
                DataBlockStartEvent,
                DataBlockDeltaEvent,
                DataBlockEndEvent,
            ):
                with self.subTest(policy=continue_on_error, target=target):
                    closed = asyncio.Event()

                    async def audio(
                        cleanup: asyncio.Event = closed,
                    ) -> AsyncGenerator:
                        try:
                            yield _make_tts_response("AAAA")
                        finally:
                            cleanup.set()

                    self.tts.synthesize.return_value = audio()
                    stream = TTSMiddleware(
                        self.tts,
                        continue_on_error=continue_on_error,
                    ).on_reply(_make_agent_stub(), {}, self._text)
                    try:
                        async for event in stream:
                            if isinstance(event, target):
                                error = RuntimeError("consumer failure")
                                with self.assertRaises(RuntimeError) as ctx:
                                    await stream.athrow(error)
                                self.assertIs(ctx.exception, error)
                                break
                        else:
                            self.fail("Expected audio event was not emitted")
                        self.assertTrue(closed.is_set())
                        self.tts.__aexit__.assert_awaited_once()
                    finally:
                        await stream.aclose()
                        self.tts.reset_mock()

    async def test_notification_consumer_error_and_close_propagate(
        self,
    ) -> None:
        """Failure notifications are safe to close or inject errors into."""
        self.tts.__aenter__.side_effect = RuntimeError("initialization failed")
        for close in (False, True):
            with self.subTest(close=close):
                stream = TTSMiddleware(
                    self.tts,
                    continue_on_error=True,
                ).on_reply(_make_agent_stub(), {}, self._text)
                event = await anext(stream)
                self.assertIsInstance(event, CustomEvent)
                if close:
                    await stream.aclose()
                else:
                    with self.assertRaisesRegex(RuntimeError, "consumer"):
                        await stream.athrow(RuntimeError("consumer"))

    async def test_caller_except_does_not_hide_cleanup_failure(self) -> None:
        """A handled caller exception is not a propagating generator error."""
        for continue_on_error in (False, True):
            with self.subTest(continue_on_error=continue_on_error):
                cleanup_error = RuntimeError("cleanup failure")
                self.tts.__aexit__.side_effect = cleanup_error
                try:
                    raise ValueError("already handled by caller")
                except ValueError:
                    if continue_on_error:
                        events = await self._collect(continue_on_error=True)
                        self.assertEqual(
                            [_dump(event) for event in events],
                            [_dump(event) for event in self.events]
                            + [
                                _dump(
                                    CustomEvent(
                                        name="tts_error",
                                        value={
                                            "reply_id": "reply-1",
                                            "stage": "cleanup",
                                        },
                                    ),
                                ),
                            ],
                        )
                    else:
                        with self.assertRaises(RuntimeError) as ctx:
                            await self._collect(continue_on_error=None)
                        self.assertIs(ctx.exception, cleanup_error)
                self.assertEqual(
                    self.tts.__aexit__.call_args.args[-3:],
                    (None, None, None),
                )
                self.tts.reset_mock()

    async def test_cleanup_keeps_actual_propagating_error(self) -> None:
        """Cleanup failure cannot replace text errors or cancellation."""
        self.tts.__aexit__.side_effect = RuntimeError("cleanup failure")
        for error in (ValueError("text failure"), asyncio.CancelledError()):
            with self.subTest(error=type(error)):

                async def upstream(
                    failure: BaseException = error,
                    **_kwargs: Any,
                ) -> AsyncGenerator:
                    yield self.events[0]
                    raise failure

                with self.assertRaises(type(error)) as ctx:
                    async for _ in TTSMiddleware(self.tts).on_reply(
                        _make_agent_stub(),
                        {},
                        upstream,
                    ):
                        pass
                self.assertIs(ctx.exception, error)
                exit_args = self.tts.__aexit__.call_args.args[-3:]
                self.assertEqual(exit_args[:2], (type(error), error))
                self.assertIsNotNone(exit_args[2])
                self.tts.reset_mock()
