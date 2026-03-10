# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittest for memory compression."""
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.message import Msg, TextBlock
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.token import CharTokenCounter


class MockChatModel(ChatModelBase):
    """A mock chat model for testing purposes."""

    def __init__(
        self,
        model_name: str,
        stream: bool = False,
    ) -> None:
        """Initialize the mock chat model.

        Args:
            model_name (`str`):
                The name of the model.
            stream (`bool`, optional):
                Whether to use streaming mode.
        """
        super().__init__(model_name=model_name, stream=stream)
        self.call_count = 0
        self.received_messages: list[list[dict]] = []

    async def __call__(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Mock the model's response.

        Args:
            messages (`list[dict]`):
                The messages to process.

        Returns:
            `ChatResponse`:
                The mocked response.
        """
        self.call_count += 1
        self.received_messages.append(messages)

        return ChatResponse(
            content=[
                TextBlock(
                    type="text",
                    text="This is a test response.",
                ),
            ],
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
        return [{"name": _.name, "content": _.content} for _ in msgs]


class MemoryCompressionTest(IsolatedAsyncioTestCase):
    """The unittest for memory compression."""

    async def test_no_compression_below_threshold(self) -> None:
        """Test that compression is NOT triggered when memory is below
        threshold.

        This test verifies that:
        1. When memory token count is below the trigger threshold, compression
           is not activated
        2. The agent's memory does not contain a compressed summary
        3. The model receives the full, uncompressed conversation history
        """
        model = MockChatModel(model_name="mock-model", stream=False)
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant.",
            model=model,
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=10000,  # High threshold to avoid compression
                agent_token_counter=CharTokenCounter(),
                keep_recent=1,
            ),
        )

        # Create a user message that won't trigger compression
        user_msg = Msg("user", "Hello, this is a short message.", "user")

        # Call the agent
        await agent(user_msg)

        # Verify that compression was NOT triggered (no compressed summary)
        self.assertEqual(
            agent.memory._compressed_summary,
            "",
        )

        # Verify the exact messages received by the model
        self.assertListEqual(
            model.received_messages,
            [
                [
                    {
                        "content": "You are a helpful assistant.",
                        "name": "system",
                    },
                    {
                        "content": "Hello, this is a short message.",
                        "name": "user",
                    },
                ],
            ],
        )

    async def test_compression_above_threshold(self) -> None:
        """Test that compression IS triggered when memory exceeds threshold and
        the model receives compressed prompts.

        This test verifies that:
        1. When memory token count exceeds the trigger threshold, compression
           is activated
        2. The agent's memory contains a properly formatted compressed summary
        3. After compression, the model receives prompts that include the
           compressed summary instead of the full conversation history
        4. The compression summary follows the expected format and contains
           the mock summary content

        This is the key test ensuring that compression not only happens, but
        also that the compressed format is actually used in subsequent model
        calls.
        """
        model = MockChatModel(model_name="mock-model", stream=False)
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You are a helpful assistant.",
            model=model,
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=100,  # Low threshold to trigger compression
                agent_token_counter=CharTokenCounter(),
                keep_recent=1,
            ),
        )

        # Create messages that will trigger compression
        # First message - should not trigger compression
        msgs = [
            Msg(
                "user",
                "1",
                "user",
            ),
            Msg(
                "user",
                "This is a long message " * 100,  # Make it long
                "user",
            ),
            Msg(
                "user",
                "2",
                "user",
            ),
        ]
        await agent(msgs)

        # Verify that compression was triggered
        summary = """<system-info>Here is a summary of your previous work
# Task Overview
This is a compressed summary.

# Current State
In progress

# Important Discoveries
N/A

# Next Steps
N/A

# Context to Preserve
N/A</system-info>"""
        self.assertEqual(
            agent.memory._compressed_summary,
            summary,
        )

        # Verify the exact messages received by the model after clearing
        # First call: compression call
        # Second call: agent response with compressed summary
        expected_received_messages = [
            [
                {"name": "system", "content": "You are a helpful assistant."},
                {"name": "user", "content": "1"},
                {
                    "name": "user",
                    "content": "This is a long message " * 100,
                },
                {
                    "name": "user",
                    "content": (
                        "<system-hint>You have been working on the task "
                        "described above but have not yet completed it. "
                        "Now write a continuation summary that will allow "
                        "you to resume work efficiently in a future context "
                        "window where the conversation history will be "
                        "replaced with this summary. Your summary should "
                        "be structured, concise, and actionable."
                        "</system-hint>"
                    ),
                },
            ],
            [
                {"name": "system", "content": "You are a helpful assistant."},
                {
                    "name": "user",
                    "content": summary,
                },
                {"name": "user", "content": "2"},
            ],
        ]

        self.assertListEqual(
            model.received_messages,
            expected_received_messages,
        )
