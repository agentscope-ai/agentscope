# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittest for memory compression."""
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.message import Msg, TextBlock, ToolUseBlock, ToolResultBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.token import CharTokenCounter


class MockChatModel(ChatModelBase):
    """A mock chat model for testing purposes."""

    async def __call__(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Mock the model's response."""
        if [_["id"] for _ in messages[1:-1]] != [str(_) for _ in range(3)]:
            raise ValueError("Unexpected messages input.")

        if messages[-1]["content"] != (
            "<system-hint>You have been working on the task described above "
            "but have not yet completed it. Now write a continuation summary "
            "that will allow you to resume work efficiently in a future "
            "context window where the conversation history will be replaced "
            "with this summary. Your summary should be structured, concise, "
            "and actionable.</system-hint>"
        ):
            raise ValueError("Unexpected final message content.")

        return ChatResponse(
            content=[],
            metadata={
                "task_overview": "This is a compressed summary.",
                "current_state": "In progress",
                "important_discoveries": "N/A",
                "next_steps": "N/A",
                "context_to_preserve": "N/A",
            },
        )


class MockFormatter(FormatterBase):
    """A mock formatter for testing purposes."""

    async def format(self, msgs: list[Msg], **kwargs: Any) -> list[dict]:
        """Mock the formatting of messages.

        Args:
            msgs (`list[Msg]`):
                The list of messages to format.

        Returns:
            `list[dict]`:
                The formatted messages.
        """
        return [_.to_dict() for _ in msgs]


class MemoryCompressionTest(IsolatedAsyncioTestCase):
    """The unittest for memory compression."""

    async def test_memory_compression(self) -> None:
        """Test the memory compression functionality."""
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant.",
            model=MockChatModel(model_name="mock-model", stream=False),
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=100,
                agent_token_counter=CharTokenCounter(),
                keep_recent=1,
            ),
        )

        msgs = [
            Msg("user", "This is a test message 1.", "user"),
            Msg("assistant", "This is a test message 2.", "assistant"),
            Msg("assistant", "This is a test message 3.", "assistant"),
            Msg(
                "assistant",
                [
                    TextBlock(
                        type="text",
                        text="This is a test message 3.",
                    ),
                    ToolUseBlock(
                        id="id1",
                        type="tool_use",
                        name="test_tool",
                        input={"param": "value"},
                    ),
                ],
                "assistant",
            ),
            Msg(
                "system",
                [
                    ToolResultBlock(
                        id="id1",
                        type="tool_result",
                        name="test_tool",
                        output="This is a test tool result.",
                    ),
                ],
                "system",
            ),
        ]
        for i, msg in enumerate(msgs):
            msg.id = str(i)

        # Add mock messages to the agent's memory
        await agent.memory.add(memories=msgs[0])

        # Manually trigger memory compression
        await agent._compress_memory_if_needed()

        # The memory shouldn't be compressed yet
        self.assertEqual(
            agent.memory._compressed_summary,
            "",
        )
        memory = await agent.memory.get_memory()
        self.assertListEqual(
            [_.id for _ in memory],
            ["0"],
        )

        # Add more messages to exceed the threshold
        await agent.memory.add(memories=msgs[1:])
        await agent._compress_memory_if_needed()

        self.assertEqual(
            agent.memory._compressed_summary,
            """<system-info>Here is a summary of your previous work
# Task Overview
This is a compressed summary.

# Current State
In progress

# Important Discoveries
N/A

# Next Steps
N/A

# Context to Preserve
N/A</system-info>""",
        )

        # Get all the memory including compressed
        memory = await agent.memory.get_memory()
        self.assertEqual(
            [_.id for _ in memory[1:]],
            [str(_) for _ in range(5)],
        )

        # Test get the uncompressed memory
        memory = await agent.memory.get_memory(
            exclude_mark="compressed",
        )
        self.assertEqual(
            [_.id for _ in memory[1:]],
            ["3", "4"],
        )

        # Test the prepend_summary=False option
        memory = await agent.memory.get_memory(
            exclude_mark="compressed",
            prepend_summary=False,
        )
        self.assertListEqual(
            [_.id for _ in memory],
            ["3", "4"],
        )
