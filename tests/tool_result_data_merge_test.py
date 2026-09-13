# -*- coding: utf-8 -*-
"""Regression tests for tool data-block identity on event replay (#2549)."""
import base64
import unittest
from typing import Any, Generator
from unittest.async_case import IsolatedAsyncioTestCase

from utils import AnyString, MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.event import (
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
)
from agentscope.message import (
    AssistantMsg,
    Base64Source,
    DataBlock,
    TextBlock,
    ToolCallBlock,
)
from agentscope.state import AgentState
from agentscope.tool import FunctionTool, ToolChunk, Toolkit, ToolResponse


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

    def test_empty_media_type_does_not_clear_existing_type(self) -> None:
        """An empty media type does not replace a prior non-empty type."""
        msg = AssistantMsg(name="assistant", content=[])
        msg.append_event(
            ToolResultStartEvent(
                reply_id=msg.id,
                tool_call_id="tc-1",
                tool_call_name="audio",
            ),
        )
        for media_type, payload in (
            ("audio/wav", b"hello"),
            ("", b"world"),
        ):
            msg.append_event(
                ToolResultDataDeltaEvent(
                    reply_id=msg.id,
                    tool_call_id="tc-1",
                    block_id="audio-1",
                    media_type=media_type,
                    data=base64.b64encode(payload).decode("ascii"),
                ),
            )

        data_block = msg.content[0].output[0]
        self.assertIsInstance(data_block, DataBlock)
        self.assertEqual(data_block.source.media_type, "audio/wav")


class ToolResultReplayConsistencyTest(IsolatedAsyncioTestCase):
    """Toolkit accumulation and event replay keep data blocks consistent."""

    async def test_cross_type_id_collisions_are_normalized_before_yield(
        self,
    ) -> None:
        """Cross-type ID collisions have the same result after replay."""

        def stream_blocks() -> Generator[ToolChunk, None, None]:
            """Yield one text block followed by two colliding data blocks."""
            yield ToolChunk(content=[TextBlock(id="same", text="label")])
            for payload in (b"a", b"b"):
                yield ToolChunk(
                    content=[
                        DataBlock(
                            id="same",
                            source=Base64Source(
                                data=base64.b64encode(payload).decode("ascii"),
                                media_type="audio/wav",
                            ),
                        ),
                    ],
                )

        toolkit = Toolkit(tools=[FunctionTool(stream_blocks)])
        agent = Agent(
            name="test",
            system_prompt="",
            model=MockModel(),
            toolkit=toolkit,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        replay = AssistantMsg(name="assistant", content=[])
        agent.state.reply_id = replay.id
        replay.append_event(
            ToolResultStartEvent(
                reply_id=replay.id,
                tool_call_id="tc-1",
                tool_call_name="stream_blocks",
            ),
        )
        response = None
        async for item in toolkit.call_tool(
            ToolCallBlock(
                id="tc-1",
                name="stream_blocks",
                input="{}",
            ),
            AgentState(),
        ):
            if isinstance(item, ToolChunk):
                # pylint: disable=protected-access
                async for event in agent._convert_tool_chunk_to_event(
                    "tc-1",
                    item.content,
                ):
                    replay.append_event(event)
            elif isinstance(item, ToolResponse):
                response = item

        self.assertIsNotNone(response)
        replay_data = [
            block
            for block in replay.content[0].output
            if isinstance(block, DataBlock)
        ]
        response_data = [
            block for block in response.content if isinstance(block, DataBlock)
        ]
        self.assertEqual(len(response_data), 2)
        self.assertListEqual(
            [block.id for block in replay_data],
            [block.id for block in response_data],
        )
        self.assertListEqual(
            [base64.b64decode(block.source.data) for block in replay_data],
            [b"a", b"b"],
        )


class ConvertToolChunkIdentityTest(IsolatedAsyncioTestCase):
    """_convert_tool_chunk_to_event preserves the chunk's block id."""

    async def test_event_block_id_matches_data_block_id(self) -> None:
        """Base64 and URL DataBlocks keep their id on the emitted event."""
        from agentscope.message import URLSource

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
