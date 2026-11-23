# -*- coding: utf-8 -*-
"""Tests that a tool-result DataBlock keeps its name when a reply is rebuilt.

A tool may return a ``DataBlock`` carrying a ``name`` (the built-in ``Read``
tool sets it to the source file name for images and PDFs). The value is kept
in ``agent.state.context`` by ``ToolResponse.append_chunk``, so the events
that stream the same chunk to reply consumers must carry it as well —
otherwise the replayed ``DataBlock`` ends up unnamed.
"""
import base64
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.agent import Agent
from agentscope.event import (
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
)
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    ToolResultState,
    URLSource,
)
from agentscope.tool import ToolChunk

REPLY_ID = "reply-1"
TOOL_CALL_ID = "tool-call-1"


class _ReplyState:
    """The only agent state the chunk-to-event converter reads."""

    reply_id = REPLY_ID


class _AgentStub:
    """An agent exposing only ``state.reply_id`` to the converter."""

    state = _ReplyState()


class ReplayToolDataBlockNameTest(IsolatedAsyncioTestCase):
    """The name of a tool-result data block survives the event round trip."""

    async def _convert(self, blocks: list[Any]) -> list[Any]:
        """Run a tool chunk through the agent's chunk-to-event converter."""
        # pylint: disable=protected-access
        return [
            event
            async for event in Agent._convert_tool_chunk_to_event(
                _AgentStub(),
                TOOL_CALL_ID,
                blocks,
            )
        ]

    async def _replay(self, chunks: list[ToolChunk]) -> list[DataBlock]:
        """Rebuild the tool result of a reply from the streamed events."""
        msg = AssistantMsg(id=REPLY_ID, name="agent", content=[])
        msg.append_event(
            ToolResultStartEvent(
                reply_id=REPLY_ID,
                tool_call_id=TOOL_CALL_ID,
                tool_call_name="Read",
            ),
        )
        for chunk in chunks:
            for event in await self._convert(chunk.content):
                msg.append_event(event)
        msg.append_event(
            ToolResultEndEvent(
                reply_id=REPLY_ID,
                tool_call_id=TOOL_CALL_ID,
                state=ToolResultState.SUCCESS,
            ),
        )
        tool_results = list(msg.get_content_blocks("tool_result"))
        self.assertEqual(len(tool_results), 1)
        output = tool_results[0].output
        self.assertIsInstance(output, list)
        return [block for block in output if isinstance(block, DataBlock)]

    @staticmethod
    def _base64_block(
        block_id: str,
        payload: bytes,
        media_type: str = "audio/wav",
        name: str | None = None,
    ) -> DataBlock:
        """Build a base64 data block with the given payload."""
        return DataBlock(
            id=block_id,
            source=Base64Source(
                data=base64.b64encode(payload).decode("ascii"),
                media_type=media_type,
            ),
            name=name,
        )

    async def test_event_carries_name_for_both_source_kinds(self) -> None:
        """The delta event reports the name of the block it streams."""
        url_block = DataBlock(
            id="url-block",
            source=URLSource(
                url="https://example.com/report.pdf",
                media_type="application/pdf",
            ),
            name="report.pdf",
        )
        b64_block = self._base64_block(
            "b64-block",
            b"PNG",
            media_type="image/png",
            name="diagram.png",
        )

        events = await self._convert([url_block, b64_block])

        self.assertListEqual(
            [event.name for event in events],
            ["report.pdf", "diagram.png"],
        )

    async def test_replayed_blocks_keep_their_name(self) -> None:
        """A replayed tool result is named like the original blocks."""
        url_block = DataBlock(
            id="url-block",
            source=URLSource(
                url="https://example.com/report.pdf",
                media_type="application/pdf",
            ),
            name="report.pdf",
        )
        b64_block = self._base64_block(
            "b64-block",
            b"PNG",
            media_type="image/png",
            name="diagram.png",
        )

        replayed = await self._replay(
            [ToolChunk(content=[url_block, b64_block])],
        )

        self.assertListEqual(
            [block.name for block in replayed],
            ["report.pdf", "diagram.png"],
        )
        self.assertListEqual(
            [block.source.media_type for block in replayed],
            ["application/pdf", "image/png"],
        )

    async def test_streamed_chunks_keep_the_name_of_the_first_delta(
        self,
    ) -> None:
        """A same-id stream keeps the name, only the payload is merged."""
        first = self._base64_block("stream", b"chunk-1", name="voice.wav")
        # Later deltas of one stream only carry payload bytes.
        second = self._base64_block("stream", b"chunk-2")

        replayed = await self._replay(
            [ToolChunk(content=[first]), ToolChunk(content=[second])],
        )

        self.assertEqual(len(replayed), 1)
        self.assertEqual(replayed[0].name, "voice.wav")
        self.assertEqual(
            replayed[0].source.data,
            base64.b64encode(b"chunk-1chunk-2").decode("ascii"),
        )

    async def test_later_delta_name_wins(self) -> None:
        """A name sent by a later delta replaces the value known so far."""
        unnamed = self._base64_block("late", b"x", media_type="image/png")
        named = self._base64_block(
            "late",
            b"y",
            media_type="image/png",
            name="late.png",
        )

        replayed = await self._replay(
            [ToolChunk(content=[unnamed]), ToolChunk(content=[named])],
        )

        self.assertEqual(replayed[0].name, "late.png")

    async def test_unnamed_block_stays_unnamed(self) -> None:
        """Blocks without a name are replayed without one."""
        # pylint: disable=protected-access
        events = await self._convert([self._base64_block("plain", b"x")])
        self.assertIsNone(events[0].name)

        replayed = await self._replay(
            [ToolChunk(content=[self._base64_block("plain", b"x")])],
        )
        self.assertIsNone(replayed[0].name)


class ToolResultDataDeltaEventNameTest(IsolatedAsyncioTestCase):
    """The event accepts the name as an optional, serialized field."""

    async def test_name_is_optional_and_serialized(self) -> None:
        """An event without a name serializes without one, a named one keeps
        it."""
        common = {
            "reply_id": REPLY_ID,
            "tool_call_id": TOOL_CALL_ID,
            "media_type": "application/pdf",
            "url": "https://example.com/report.pdf",
        }

        unnamed = ToolResultDataDeltaEvent(**common)
        self.assertIsNone(unnamed.name)
        self.assertNotIn("name", unnamed.model_dump(exclude_none=True))

        named = ToolResultDataDeltaEvent(**common, name="report.pdf")
        self.assertEqual(named.name, "report.pdf")
        self.assertEqual(
            ToolResultDataDeltaEvent.model_validate(
                named.model_dump(),
            ).name,
            "report.pdf",
        )
