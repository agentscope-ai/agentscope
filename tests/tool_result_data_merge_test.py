# -*- coding: utf-8 -*-
"""Regression tests for https://github.com/agentscope-ai/agentscope/issues/2549

A streaming tool can emit multiple Base64 DataBlock chunks that share one
block id (the documented ToolChunk contract: chunks of one multimodal payload
share the id so consumers can group them). Msg.append_event used to append
each delta as a separate DataBlock, splitting one resource into multiple
partial blocks on replay, while the canonical ToolResponse.append_chunk merges
same-id chunks. These tests pin the merged behavior for append_event.
"""
import base64
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.event import (
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
)
from agentscope.message import AssistantMsg, Base64Source, DataBlock

_TC = "tc-1"


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _drive(msg: AssistantMsg, deltas: list) -> None:
    """Apply start → deltas → end. Each delta is a (block_id, b64) tuple."""
    msg.append_event(
        ToolResultStartEvent(
            reply_id=msg.id,
            tool_call_id=_TC,
            tool_call_name="audio",
        ),
    )
    for bid, data in deltas:
        msg.append_event(
            ToolResultDataDeltaEvent(
                reply_id=msg.id,
                tool_call_id=_TC,
                block_id=bid,
                media_type="audio/wav",
                data=data,
            ),
        )
    msg.append_event(
        ToolResultEndEvent(reply_id=msg.id, tool_call_id=_TC, state="success"),
    )


class TestToolResultDataDeltaMerge(IsolatedAsyncioTestCase):
    def test_same_block_id_deltas_merge_into_one_block(self) -> None:
        msg = AssistantMsg(name="assistant", content=[])
        _drive(msg, [("audio-1", _b64(b"hello")), ("audio-1", _b64(b"world"))])

        tool_blocks = [b for b in msg.content if b.type == "tool_result"]
        self.assertEqual(len(tool_blocks), 1)
        data_blocks = [
            b for b in tool_blocks[0].output if isinstance(b, DataBlock)
        ]
        self.assertEqual(len(data_blocks), 1)
        src = data_blocks[0].source
        self.assertIsInstance(src, Base64Source)
        # The two independently-encoded chunks decode to concatenated bytes.
        self.assertEqual(base64.b64decode(src.data), b"helloworld")

    def test_different_block_ids_stay_separate(self) -> None:
        msg = AssistantMsg(name="assistant", content=[])
        _drive(msg, [("a", _b64(b"hello")), ("b", _b64(b"world"))])
        tool_blocks = [b for b in msg.content if b.type == "tool_result"]
        data_blocks = [
            b for b in tool_blocks[0].output if isinstance(b, DataBlock)
        ]
        self.assertEqual(len(data_blocks), 2)
