# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittest for memory compression."""
from typing import Any
from unittest import IsolatedAsyncioTestCase

from pydantic import BaseModel, Field
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.memory import AsyncSQLAlchemyMemory
from agentscope.message import Msg, TextBlock, ToolUseBlock
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


class StructuredOutputRetryModel(ChatModelBase):
    """A test model that exercises compression and hint-mark retries."""

    def __init__(self) -> None:
        """Initialize the test model."""
        super().__init__(model_name="mock-structured-model", stream=False)
        self.reasoning_calls = 0
        self.compression_calls = 0

    async def __call__(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Return deterministic responses for compression and reasoning."""
        structured_model = kwargs.get("structured_model")
        if structured_model is not None and kwargs.get("tools") is None:
            self.compression_calls += 1
            return ChatResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Compression summary generated.",
                    ),
                ],
                metadata={
                    "task_overview": "Summarized task.",
                    "current_state": "Compressed existing messages.",
                    "important_discoveries": "External sessions may disable autoflush.",
                    "next_steps": "Continue the structured reply.",
                    "context_to_preserve": "Keep the latest message verbatim.",
                },
            )

        self.reasoning_calls += 1
        if self.reasoning_calls == 1:
            return ChatResponse(
                content=[
                    TextBlock(
                        type="text",
                        text="Need one more structured-output pass.",
                    ),
                ],
            )

        return ChatResponse(
            content=[
                TextBlock(
                    type="text",
                    text="Structured response is ready.",
                ),
                ToolUseBlock(
                    type="tool_use",
                    name="generate_response",
                    id="generate-response-1",
                    input={"result": "done"},
                ),
            ],
        )


class MemoryCompressionTest(IsolatedAsyncioTestCase):
    """The unittest for memory compression."""

    @staticmethod
    def _create_foreign_key_engine():
        """Create an async SQLite engine with foreign key checks enabled."""
        engine = create_async_engine(
            url="sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _connection_record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        return engine

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

    async def test_compression_with_external_no_autoflush_memory_session(
        self,
    ) -> None:
        """Compression and structured-output retries should work with external sessions.

        This covers the production path from the issue report:
        1. Existing messages trigger compression.
        2. Compression marks older messages as compressed.
        3. The first reasoning pass adds a hint message with a mark.
        4. The caller-provided AsyncSession disables autoflush.

        Without flushing the pending message rows before bulk-inserting marks,
        the hint-message insertion can violate the message_mark foreign key.
        """

        class StructuredResult(BaseModel):
            """Structured output schema used by the regression test."""

            result: str = Field(description="The final structured result.")

        engine = self._create_foreign_key_engine()
        session_factory = async_sessionmaker(
            bind=engine,
            expire_on_commit=False,
            autoflush=False,
        )

        async with session_factory() as session:
            memory = AsyncSQLAlchemyMemory(
                session_id="compression-session",
                user_id="compression-user",
                engine_or_session=session,
            )
            model = StructuredOutputRetryModel()
            agent = ReActAgent(
                name="Friday",
                sys_prompt="You are a helpful assistant.",
                model=model,
                formatter=MockFormatter(),
                memory=memory,
                compression_config=ReActAgent.CompressionConfig(
                    enable=True,
                    trigger_threshold=100,
                    agent_token_counter=CharTokenCounter(),
                    keep_recent=1,
                ),
            )

            msgs = [
                Msg("user", "Task kickoff", "user"),
                Msg("assistant", "This is a long assistant note " * 60, "assistant"),
                Msg("user", "Please continue from here.", "user"),
            ]
            for idx, msg in enumerate(msgs):
                msg.id = f"seed-{idx}"

            reply = await agent(msgs, structured_model=StructuredResult)

            self.assertEqual(reply.metadata, {"result": "done"})
            self.assertTrue(agent.memory._compressed_summary)
            self.assertGreaterEqual(model.compression_calls, 1)
            self.assertEqual(model.reasoning_calls, 2)

            compressed_msgs = await memory.get_memory(
                mark="compressed",
                prepend_summary=False,
            )
            hint_msgs = await memory.get_memory(
                mark="hint",
                prepend_summary=False,
            )
            persisted_msgs = await memory.get_memory(prepend_summary=False)

            self.assertTrue(
                {"seed-0", "seed-1", "seed-2"}.issubset(
                    {_.id for _ in compressed_msgs},
                ),
            )
            self.assertEqual(hint_msgs, [])
            self.assertGreaterEqual(len(persisted_msgs), 6)
            self.assertEqual(
                [_.id for _ in persisted_msgs[:3]],
                ["seed-0", "seed-1", "seed-2"],
            )

        await engine.dispose()
