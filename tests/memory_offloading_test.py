# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittest for memory offloading."""
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.memory import InMemoryMemoryOffloading
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
        """Initialize the mock chat model."""
        super().__init__(model_name=model_name, stream=stream)
        self.call_count = 0
        self.received_messages: list[list[dict]] = []

    async def __call__(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> ChatResponse:
        """Mock the model's response."""
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
        """Mock the formatting of messages."""
        return [{"name": _.name, "content": _.content} for _ in msgs]


class InMemoryMemoryOffloadingTest(IsolatedAsyncioTestCase):
    """Unit tests for InMemoryMemoryOffloading."""

    async def asyncSetUp(self) -> None:
        """Set up the storage for testing."""
        self.storage = InMemoryMemoryOffloading()

    async def test_store_and_search(self) -> None:
        """Test storing and searching memory chunks."""
        msgs = [
            Msg("user", "I like traveling to Japan.", "user"),
            Msg("assistant", "Japan is a great destination!", "assistant"),
        ]
        await self.storage.store(msgs, "User discussed travel to Japan")

        msgs2 = [
            Msg("user", "My favorite food is sushi.", "user"),
            Msg("assistant", "Sushi is delicious!", "assistant"),
        ]
        await self.storage.store(msgs2, "User's food preference is sushi")

        # Search should find travel-related chunk
        response = await self.storage.search_memory("travel")
        text = response.content[0]["text"]
        self.assertIn("Japan", text)

        # Search should find food-related chunk
        response = await self.storage.search_memory("sushi")
        text = response.content[0]["text"]
        self.assertIn("sushi", text)

        # Search in message content (not just summary)
        response = await self.storage.search_memory("destination")
        text = response.content[0]["text"]
        self.assertIn("Memory Chunk", text)

    async def test_search_case_insensitive(self) -> None:
        """Test that search is case-insensitive."""
        msgs = [Msg("user", "Python programming", "user")]
        await self.storage.store(msgs, "Discussion about Python")

        response = await self.storage.search_memory("python")
        text = response.content[0]["text"]
        self.assertIn("Python", text)

        response = await self.storage.search_memory("PYTHON")
        text = response.content[0]["text"]
        self.assertIn("Python", text)

    async def test_search_limit(self) -> None:
        """Test that search respects the limit parameter."""
        for i in range(10):
            msgs = [Msg("user", f"Topic {i} about testing", "user")]
            await self.storage.store(msgs, f"Summary about testing topic {i}")

        response = await self.storage.search_memory("testing", limit=3)
        text = response.content[0]["text"]
        # Should contain exactly 3 chunks
        self.assertEqual(text.count("Memory Chunk"), 3)

    async def test_search_most_recent_first(self) -> None:
        """Test that search results are returned most recent first."""
        msgs1 = [Msg("user", "First topic about AI", "user")]
        await self.storage.store(msgs1, "First AI discussion")

        msgs2 = [Msg("user", "Second topic about AI", "user")]
        await self.storage.store(msgs2, "Second AI discussion")

        response = await self.storage.search_memory("AI", limit=2)
        text = response.content[0]["text"]
        # Most recent should come first
        second_pos = text.index("Second")
        first_pos = text.index("First")
        self.assertLess(second_pos, first_pos)

    async def test_search_no_results(self) -> None:
        """Test search with no matching results."""
        msgs = [Msg("user", "Hello", "user")]
        await self.storage.store(msgs, "Greeting")

        response = await self.storage.search_memory("quantum_physics_xyz")
        text = response.content[0]["text"]
        self.assertIn("No offloaded memories found", text)

    async def test_clear(self) -> None:
        """Test clearing all offloaded data."""
        msgs = [Msg("user", "test message", "user")]
        await self.storage.store(msgs, "test summary")
        self.assertEqual(await self.storage.size(), 1)

        await self.storage.clear()
        self.assertEqual(await self.storage.size(), 0)

        response = await self.storage.search_memory("test")
        text = response.content[0]["text"]
        self.assertIn("No offloaded memories found", text)

    async def test_size(self) -> None:
        """Test the size method."""
        self.assertEqual(await self.storage.size(), 0)

        for i in range(5):
            msgs = [Msg("user", f"msg {i}", "user")]
            await self.storage.store(msgs, f"summary {i}")

        self.assertEqual(await self.storage.size(), 5)

    async def test_list_tools(self) -> None:
        """Test that list_tools returns the search_memory tool."""
        tools = self.storage.list_tools()
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].__name__, "search_memory")

    async def test_state_dict_and_load(self) -> None:
        """Test serialization and deserialization."""
        msgs = [
            Msg("user", "test", "user"),
            Msg("assistant", "response", "assistant"),
        ]
        await self.storage.store(msgs, "test summary")

        # Serialize
        state = self.storage.state_dict()
        self.assertIn("_chunks", state)
        self.assertEqual(len(state["_chunks"]), 1)

        # Deserialize into a new instance
        new_storage = InMemoryMemoryOffloading()
        new_storage.load_state_dict(state)

        self.assertEqual(await new_storage.size(), 1)
        response = await new_storage.search_memory("test")
        text = response.content[0]["text"]
        self.assertIn("test summary", text)


class OffloadingIntegrationTest(IsolatedAsyncioTestCase):
    """Integration tests for offloading during compression."""

    async def test_offloading_during_compression(self) -> None:
        """Test that compressed messages are offloaded to storage when
        offload_storage is configured.

        This test verifies that:
        1. When compression is triggered, messages are offloaded
        2. The offloaded data is searchable
        3. The compressed summary and offloaded summary are consistent
        """
        storage = InMemoryMemoryOffloading()
        model = MockChatModel(model_name="mock-model", stream=False)
        agent = ReActAgent(
            name="TestAgent",
            sys_prompt="You are a helpful assistant.",
            model=model,
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=50,  # Low threshold to trigger compression
                agent_token_counter=CharTokenCounter(),
                keep_recent=1,
                offload_storage=storage,
            ),
        )

        # Add enough messages to trigger compression
        long_content = "x" * 100  # Content that will exceed threshold
        user_msg = Msg("user", long_content, "user")
        await agent.memory.add(user_msg)
        assistant_msg = Msg("assistant", long_content, "assistant")
        await agent.memory.add(assistant_msg)

        # Trigger compression
        await agent._compress_memory_if_needed()

        # Verify offloading occurred
        self.assertEqual(
            await storage.size(),
            1,
            "Should have offloaded one chunk",
        )

        # Verify searchability
        response = await storage.search_memory("compressed")
        text = response.content[0]["text"]
        self.assertIn("compressed summary", text)

    async def test_no_offloading_without_config(self) -> None:
        """Test that compression works normally without offload_storage.

        This ensures backward compatibility — the compression flow
        should work identically when offload_storage is not configured.
        """
        model = MockChatModel(model_name="mock-model", stream=False)
        agent = ReActAgent(
            name="TestAgent",
            sys_prompt="You are a helpful assistant.",
            model=model,
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=50,
                agent_token_counter=CharTokenCounter(),
                keep_recent=1,
                # No offload_storage
            ),
        )

        # Add enough messages to trigger compression
        long_content = "x" * 100
        user_msg = Msg("user", long_content, "user")
        await agent.memory.add(user_msg)
        assistant_msg = Msg("assistant", long_content, "assistant")
        await agent.memory.add(assistant_msg)

        # Trigger compression — should not raise
        await agent._compress_memory_if_needed()

        # Verify compression happened
        memory = await agent.memory.get_memory(prepend_summary=True)
        self.assertIn("summary", memory[0].content.lower())

        compressed_msgs = await agent.memory.get_memory(
            mark="compressed",
            prepend_summary=False,
        )
        self.assertTrue(
            len(compressed_msgs) > 0,
            "Compressed messages should be marked",
        )

        self.assertTrue(
            model.call_count >= 1,
            "Model should have been called for compression",
        )

    async def test_search_tool_registration(self) -> None:
        """Test that search_memory tool is registered when
        offload_storage is configured."""
        storage = InMemoryMemoryOffloading()
        model = MockChatModel(model_name="mock-model", stream=False)
        agent = ReActAgent(
            name="TestAgent",
            sys_prompt="You are a helpful assistant.",
            model=model,
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=10000,
                agent_token_counter=CharTokenCounter(),
                offload_storage=storage,
            ),
        )

        # The search tool should be registered
        self.assertIn(
            "search_memory",
            agent.toolkit.tools,
            "search_memory tool should be registered",
        )

    async def test_no_search_tool_without_offloading(self) -> None:
        """Test that search_memory tool is NOT registered when
        offload_storage is not configured."""
        model = MockChatModel(model_name="mock-model", stream=False)
        agent = ReActAgent(
            name="TestAgent",
            sys_prompt="You are a helpful assistant.",
            model=model,
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=10000,
                agent_token_counter=CharTokenCounter(),
                # No offload_storage
            ),
        )

        self.assertNotIn(
            "search_memory",
            agent.toolkit.tools,
            "search_memory tool should NOT be registered",
        )

    async def test_search_tool_returns_results(self) -> None:
        """Test the search_memory tool function directly."""
        storage = InMemoryMemoryOffloading()
        model = MockChatModel(model_name="mock-model", stream=False)
        ReActAgent(
            name="TestAgent",
            sys_prompt="You are a helpful assistant.",
            model=model,
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=10000,
                agent_token_counter=CharTokenCounter(),
                offload_storage=storage,
            ),
        )

        # Manually store something in the offload storage
        msgs = [Msg("user", "I love Python programming", "user")]
        await storage.store(msgs, "User loves Python programming")

        # Call the search tool directly on the storage
        response = await storage.search_memory("Python")
        text = response.content[0]["text"]
        self.assertIn("Python", text)
        self.assertIn("Memory Chunk 1", text)

    async def test_search_tool_no_results(self) -> None:
        """Test the search_memory tool when no results match."""
        storage = InMemoryMemoryOffloading()
        model = MockChatModel(model_name="mock-model", stream=False)
        ReActAgent(
            name="TestAgent",
            sys_prompt="You are a helpful assistant.",
            model=model,
            formatter=MockFormatter(),
            compression_config=ReActAgent.CompressionConfig(
                enable=True,
                trigger_threshold=10000,
                agent_token_counter=CharTokenCounter(),
                offload_storage=storage,
            ),
        )

        response = await storage.search_memory("nonexistent_topic")
        text = response.content[0]["text"]
        self.assertIn("No offloaded memories found", text)
