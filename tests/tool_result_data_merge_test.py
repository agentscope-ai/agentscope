# -*- coding: utf-8 -*-
"""Regression tests for tool data-block identity on event replay (#2549)."""
import base64
import unittest
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import (
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
)
from agentscope.message import AssistantMsg
from agentscope.tool import Toolkit


def _data_block(block_id: str, data_b64: str) -> dict[str, Any]:
    """Expected tool-result output entry for a base64 DataBlock."""
    return {
        "type": "data",
        "id": block_id,
        "source": {
            "type": "base64",
            "data": data_b64,
            "media_type": "audio/wav",
        },
        "name": None,
        "created_at": AnyString(),
        "finished_at": None,
    }


class ToolResultDataDeltaMergeTest(unittest.TestCase):
    """Msg.append_event groups same-id Base64 deltas into one DataBlock."""

    def test_same_block_id_deltas_merge_into_one_block(self) -> None:
        """Same-id deltas merge; the payload decodes to concatenated bytes."""
        msg = AssistantMsg(name="assistant", content=[])
        msg.append_event(
            ToolResultStartEvent(
                reply_id=msg.id,
                tool_call_id="tc-1",
                tool_call_name="audio",
            ),
        )
        for payload in (b"hello", b"world"):
            msg.append_event(
                ToolResultDataDeltaEvent(
                    reply_id=msg.id,
                    tool_call_id="tc-1",
                    block_id="audio-1",
                    media_type="audio/wav",
                    data=base64.b64encode(payload).decode("ascii"),
                ),
            )
        msg.append_event(
            ToolResultEndEvent(
                reply_id=msg.id,
                tool_call_id="tc-1",
                state="success",
            ),
        )

        tool_blocks = [b for b in msg.content if b.type == "tool_result"]
        self.assertEqual(len(tool_blocks), 1)
        # b"hello" + b"world" -> base64("helloworld") = "aGVsbG93b3JsZA=="
        self.assertListEqual(
            [b.model_dump() for b in tool_blocks[0].output],
            [_data_block("audio-1", "aGVsbG93b3JsZA==")],
        )

    def test_different_block_ids_stay_separate(self) -> None:
        """Different block ids still produce separate DataBlocks."""
        msg = AssistantMsg(name="assistant", content=[])
        msg.append_event(
            ToolResultStartEvent(
                reply_id=msg.id,
                tool_call_id="tc-1",
                tool_call_name="audio",
            ),
        )
        for block_id, payload in (("a", b"hello"), ("b", b"world")):
            msg.append_event(
                ToolResultDataDeltaEvent(
                    reply_id=msg.id,
                    tool_call_id="tc-1",
                    block_id=block_id,
                    media_type="audio/wav",
                    data=base64.b64encode(payload).decode("ascii"),
                ),
            )
        msg.append_event(
            ToolResultEndEvent(
                reply_id=msg.id,
                tool_call_id="tc-1",
                state="success",
            ),
        )

        tool_blocks = [b for b in msg.content if b.type == "tool_result"]
        self.assertEqual(len(tool_blocks), 1)
        self.assertListEqual(
            [b.model_dump() for b in tool_blocks[0].output],
            [
                _data_block("a", base64.b64encode(b"hello").decode("ascii")),
                _data_block("b", base64.b64encode(b"world").decode("ascii")),
            ],
        )


class ConvertToolChunkIdentityTest(IsolatedAsyncioTestCase):
    """_convert_tool_chunk_to_event preserves the chunk's block id."""

    async def test_event_block_id_matches_data_block_id(self) -> None:
        """Base64 and URL DataBlocks keep their id on the emitted event."""
        from agentscope.message import DataBlock, Base64Source, URLSource

        agent = Agent(
            name="test",
            system_prompt="",
            model=MockModel(),
            toolkit=Toolkit(),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        chunks = [
            DataBlock(
                id="b64-block",
                source=Base64Source(
                    data=base64.b64encode(b"hello").decode("ascii"),
                    media_type="audio/wav",
                ),
            ),
            DataBlock(
                id="url-block",
                source=URLSource(
                    url="https://example.com/a.wav",
                    media_type="audio/wav",
                ),
            ),
        ]
        # pylint: disable=protected-access
        events = [
            event
            async for event in agent._convert_tool_chunk_to_event(
                "tc-1",
                chunks,
            )
        ]
        self.assertListEqual(
            [(type(e).__name__, e.block_id) for e in events],
            [
                ("ToolResultDataDeltaEvent", "b64-block"),
                ("ToolResultDataDeltaEvent", "url-block"),
            ],
        )
