# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""The unittest for memory offloading."""
from typing import Any
from unittest import IsolatedAsyncioTestCase

from agentscope.agent import ReActAgent
from agentscope.formatter import FormatterBase
from agentscope.memory import InMemorySearchableStorage
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


class InMemorySearchableStorageTest(IsolatedAsyncioTestCase):
    """Unit tests for InMemorySearchableStorage."""

    async def asyncSetUp(self) -> None:
        """Set up the storage for testing."""
        self.storage = InMemorySearchableStorage()

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
        results = await self.storage.search("travel")
        self.assertEqual(len(results), 1)
        self.assertIn("Japan", results[0]["summary"])

        # Search should find food-related chunk
        results = await self.storage.search("sushi")
        self.assertEqual(len(results), 1)
        self.assertIn("sushi", results[0]["summary"])

        # Search in message content (not just summary)
        results = await self.storage.search("destination")
        self.assertEqual(len(results), 1)

    async def test_search_case_insensitive(self) -> None:
        """Test that search is case-insensitive."""
        msgs = [Msg("user", "Python programming", "user")]
        await self.storage.store(msgs, "Discussion about Python")

        results = await self.storage.search("python")
        self.assertEqual(len(results), 1)

        results = await self.storage.search("PYTHON")
        self.assertEqual(len(results), 1)

    async def test_search_limit(self) -> None:
        """Test that search respects the limit parameter."""
        for i in range(10):
            msgs = [Msg("user", f"Topic {i} about testing", "user")]
            await self.storage.store(msgs, f"Summary about testing topic {i}")

        results = await self.storage.search("testing", limit=3)
        self.assertEqual(len(results), 3)

    async def test_search_most_recent_first(self) -> None:
        """Test that search results are returned most recent first."""
        msgs1 = [Msg("user", "First topic about AI", "user")]
        await self.storage.store(msgs1, "First AI discussion")

        msgs2 = [Msg("user", "Second topic about AI", "user")]
        await self.storage.store(msgs2, "Second AI discussion")

        results = await self.storage.search("AI", limit=2)
        self.assertEqual(len(results), 2)
        # Most recent should come first
        self.assertIn("Second", results[0]["summary"])
        self.assertIn("First", results[1]["summary"])

    async def test_search_no_results(self) -> None:
        """Test search with no matching results."""
        msgs = [Msg("user", "Hello", "user")]
        await self.storage.store(msgs, "Greeting")

        results = await self.storage.search("quantum_physics_xyz")
        self.assertEqual(len(results), 0)

    async def test_clear(self) -> None:
        """Test clearing all offloaded data."""
        msgs = [Msg("user", "test message", "user")]
        await self.storage.store(msgs, "test summary")
        self.assertEqual(await self.storage.size(), 1)

        await self.storage.clear()
        self.assertEqual(await self.storage.size(), 0)

        results = await self.storage.search("test")
        self.assertEqual(len(results), 0)

    async def test_size(self) -> None:
        """Test the size method."""
        self.assertEqual(await self.storage.size(), 0)

        for i in range(5):
            msgs = [Msg("user", f"msg {i}", "user")]
            await self.storage.store(msgs, f"summary {i}")

        self.assertEqual(await self.storage.size(), 5)

    async def test_result_dict_format(self) -> None:
        """Test that search results contain the expected keys."""
        msgs = [
            Msg("user", "test", "user"),
            Msg("assistant", "response", "assistant"),
        ]
        await self.storage.store(msgs, "test summary")

        results = await self.storage.search("test")
        self.assertEqual(len(results), 1)

        result = results[0]
        self.assertIn("summary", result)
        self.assertIn("timestamp", result)
        self.assertIn("num_messages", result)
        self.assertEqual(result["summary"], "test summary")
        self.assertEqual(result["num_messages"], 2)


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
        storage = InMemorySearchableStorage()
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
        results = await storage.search("compressed")
        self.assertEqual(len(results), 1)
        self.assertIn("compressed summary", results[0]["summary"])

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
        """Test that search_offloaded_memory tool is registered when
        offload_storage is configured."""
        storage = InMemorySearchableStorage()
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
            "search_offloaded_memory",
            agent.toolkit.tools,
            "search_offloaded_memory tool should be registered",
        )

    async def test_no_search_tool_without_offloading(self) -> None:
        """Test that search_offloaded_memory tool is NOT registered when
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
            "search_offloaded_memory",
            agent.toolkit.tools,
            "search_offloaded_memory tool should NOT be registered",
        )

    async def test_search_tool_returns_results(self) -> None:
        """Test the search_offloaded_memory tool function directly."""
        storage = InMemorySearchableStorage()
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

        # Manually store something in the offload storage
        msgs = [Msg("user", "I love Python programming", "user")]
        await storage.store(msgs, "User loves Python programming")

        # Call the search tool
        response = await agent.search_offloaded_memory("Python")
        text = response.content[0]["text"]
        self.assertIn("Python", text)
        self.assertIn("Memory Chunk 1", text)

    async def test_search_tool_no_results(self) -> None:
        """Test the search_offloaded_memory tool when no results match."""
        storage = InMemorySearchableStorage()
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

        response = await agent.search_offloaded_memory("nonexistent_topic")
        text = response.content[0]["text"]
        self.assertIn("No offloaded memories found", text)
