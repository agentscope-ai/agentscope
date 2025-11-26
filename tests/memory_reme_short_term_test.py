# -*- coding: utf-8 -*-
# pylint: disable=W0212,R0904
"""Unit tests for ReMeShortTermMemory class."""
import unittest
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, AsyncMock, MagicMock

from agentscope.memory import ReMeShortTermMemory
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel, OpenAIChatModel


class TestReMeShortTermMemory(IsolatedAsyncioTestCase):
    """Test cases for ReMeShortTermMemory."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        # Mock the model to pass isinstance checks
        self.mock_dashscope_model = MagicMock(spec=DashScopeChatModel)
        self.mock_dashscope_model.model_name = "qwen3-max"
        self.mock_dashscope_model.api_key = "test_api_key"

        self.mock_openai_model = MagicMock(spec=OpenAIChatModel)
        self.mock_openai_model.model_name = "gpt-4"
        self.mock_openai_client = MagicMock()
        self.mock_openai_client.base_url = "https://api.openai.com/v1"
        self.mock_openai_client.api_key = "test_openai_key"
        self.mock_openai_model.client = self.mock_openai_client

        print("\n=== Testing ReMeShortTermMemory ===")

    def _create_memory_instance(
        self,
        model: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Create a ReMeShortTermMemory instance with mocked dependencies."""
        if model is None:
            model = self.mock_dashscope_model

        with patch("reme_ai.ReMeApp"):
            memory = ReMeShortTermMemory(
                model=model,
                **kwargs,
            )
            # Mock the app attribute
            memory.app = AsyncMock()
            memory._app_started = True
            return memory

    async def test_init_with_openai_model(self) -> None:
        """Test initialization with OpenAIChatModel."""
        with patch("reme_ai.ReMeApp") as MockReMeApp:
            memory = ReMeShortTermMemory(
                model=self.mock_openai_model,
            )
            self.assertIsNotNone(memory.app)
            self.assertIsNotNone(memory.formatter)
            MockReMeApp.assert_called_once()

    async def test_init_with_custom_params(self) -> None:
        """Test initialization with custom parameters."""
        with patch("reme_ai.ReMeApp"):
            memory = ReMeShortTermMemory(
                model=self.mock_dashscope_model,
                working_summary_mode="manual",
                compact_ratio_threshold=0.8,
                max_total_tokens=30000,
                max_tool_message_tokens=3000,
                group_token_threshold=1000,
                keep_recent_count=2,
                store_dir="custom_memory",
            )
            self.assertEqual(memory.working_summary_mode, "manual")
            self.assertEqual(memory.compact_ratio_threshold, 0.8)
            self.assertEqual(memory.max_total_tokens, 30000)
            self.assertEqual(memory.max_tool_message_tokens, 3000)
            self.assertEqual(memory.group_token_threshold, 1000)
            self.assertEqual(memory.keep_recent_count, 2)
            self.assertEqual(memory.store_dir, "custom_memory")

    async def test_init_with_invalid_model(self) -> None:
        """Test initialization with invalid model type."""
        with self.assertRaises(ValueError) as context:
            ReMeShortTermMemory(model=None)

        self.assertIn(
            "model must be a DashScopeChatModel or OpenAIChatModel instance",
            str(context.exception),
        )

    async def test_init_without_reme_ai(self) -> None:
        """Test initialization when reme_ai is not installed."""
        with patch.dict("sys.modules", {"reme_ai": None}):
            with self.assertRaises(ImportError) as context:
                ReMeShortTermMemory(model=self.mock_dashscope_model)

            self.assertIn("reme_ai", str(context.exception))

    async def test_context_manager_usage(self) -> None:
        """Test using ReMeShortTermMemory as async context manager."""
        with patch("reme_ai.ReMeApp") as MockReMeApp:
            mock_app = AsyncMock()
            mock_app.__aenter__ = AsyncMock(return_value=mock_app)
            mock_app.__aexit__ = AsyncMock(return_value=None)
            MockReMeApp.return_value = mock_app

            memory = ReMeShortTermMemory(
                model=self.mock_dashscope_model,
            )

            # Use as context manager
            async with memory as mem:
                self.assertIsNotNone(mem)
                self.assertTrue(mem._app_started)
                self.assertTrue(hasattr(mem, "app"))

            # After exit, app_started should be False
            self.assertFalse(memory._app_started)

    async def test_get_memory_success(self) -> None:
        """Test successful memory retrieval via get_memory."""
        memory = self._create_memory_instance()

        # Mock formatter.format response
        mock_messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        memory.formatter.format = AsyncMock(return_value=mock_messages)

        # Mock app.async_execute response
        mock_result = {
            "answer": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
            ],
            "metadata": {},
        }
        memory.app.async_execute = AsyncMock(return_value=mock_result)

        # Test get_memory
        result = await memory.get_memory()

        # Verify result
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[0], Msg)
        self.assertIsInstance(result[1], Msg)

        # Verify formatter.format was called
        memory.formatter.format.assert_called_once()

        # Verify app.async_execute was called with correct parameters
        memory.app.async_execute.assert_called_once()
        call_kwargs = memory.app.async_execute.call_args[1]
        self.assertEqual(call_kwargs["name"], "summary_working_memory_for_as")
        self.assertIn("chat_id", call_kwargs)

    async def test_get_memory_with_list_content(self) -> None:
        """Test get_memory when formatter returns content as list."""
        memory = self._create_memory_instance()

        # Mock formatter.format response with content as list
        mock_messages = [
            {"role": "user", "content": ["text1", "text2"]},
            {"role": "assistant", "content": "Normal content"},
        ]
        memory.formatter.format = AsyncMock(return_value=mock_messages)

        # Mock app.async_execute response
        mock_result = {
            "answer": [
                {"role": "user", "content": ""},
                {"role": "assistant", "content": "Normal content"},
            ],
            "metadata": {},
        }
        memory.app.async_execute = AsyncMock(return_value=mock_result)

        # Test get_memory
        result = await memory.get_memory()

        # Verify that content as list was replaced with empty string
        self.assertEqual(len(result), 2)

    async def test_get_memory_with_write_file_dict(self) -> None:
        """Test get_memory with write_file_dict in metadata."""
        memory = self._create_memory_instance()

        # Mock formatter.format response
        mock_messages = [{"role": "user", "content": "Test"}]
        memory.formatter.format = AsyncMock(return_value=mock_messages)

        # Mock app.async_execute response with write_file_dict
        test_file_path = "/tmp/test_working_memory/test_file.txt"
        test_content = "Test file content"
        mock_result = {
            "answer": [{"role": "user", "content": "Test"}],
            "metadata": {
                "write_file_dict": {
                    test_file_path: test_content,
                },
            },
        }
        memory.app.async_execute = AsyncMock(return_value=mock_result)

        # Mock write_text_file
        with patch(
            "agentscope.memory._reme._reme_short_term_message.write_text_file",
        ) as mock_write:
            mock_write.return_value = AsyncMock()

            # Test get_memory
            await memory.get_memory()

            # Verify write_text_file was called
            mock_write.assert_called_once_with(test_file_path, test_content)

    async def test_list_to_msg_with_user_message(self) -> None:
        """Test list_to_msg static method with user message."""
        messages = [
            {
                "role": "user",
                "content": "Hello, how are you?",
            },
        ]

        result = ReMeShortTermMemory.list_to_msg(messages)

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Msg)
        self.assertEqual(result[0].role, "user")
        self.assertIsInstance(result[0].content, list)
        self.assertEqual(result[0].content[0]["type"], "text")
        self.assertEqual(result[0].content[0]["text"], "Hello, how are you?")

    async def test_list_to_msg_with_assistant_message(self) -> None:
        """Test list_to_msg static method with assistant message."""
        messages = [
            {
                "role": "assistant",
                "content": "I'm doing well, thank you!",
            },
        ]

        result = ReMeShortTermMemory.list_to_msg(messages)

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Msg)
        self.assertEqual(result[0].role, "assistant")
        self.assertEqual(result[0].content[0]["type"], "text")
        self.assertEqual(
            result[0].content[0]["text"],
            "I'm doing well, thank you!",
        )

    async def test_list_to_msg_with_tool_message(self) -> None:
        """Test list_to_msg static method with tool message."""
        messages = [
            {
                "role": "tool",
                "content": "Tool result content",
                "name": "test_tool",
                "tool_call_id": "call_123",
            },
        ]

        result = ReMeShortTermMemory.list_to_msg(messages)

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Msg)
        # Tool role is converted to system
        self.assertEqual(result[0].role, "system")
        self.assertEqual(result[0].content[0]["type"], "tool_result")
        self.assertEqual(result[0].content[0]["name"], "test_tool")
        self.assertEqual(result[0].content[0]["id"], "call_123")

    async def test_list_to_msg_with_tool_calls(self) -> None:
        """Test list_to_msg static method with tool_calls."""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_456",
                        "function": {
                            "name": "search",
                            "arguments": '{"query": "test"}',
                        },
                    },
                ],
            },
        ]

        result = ReMeShortTermMemory.list_to_msg(messages)

        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], Msg)
        self.assertEqual(result[0].role, "assistant")
        self.assertEqual(result[0].content[0]["type"], "tool_use")
        self.assertEqual(result[0].content[0]["name"], "search")
        self.assertEqual(result[0].content[0]["id"], "call_456")
        self.assertEqual(result[0].content[0]["input"], {"query": "test"})

    async def test_list_to_msg_with_complex_message(self) -> None:
        """Test list_to_msg with message containing both
        content and tool_calls."""
        messages = [
            {
                "role": "assistant",
                "content": "I'll search for that.",
                "tool_calls": [
                    {
                        "id": "call_789",
                        "function": {
                            "name": "search",
                            "arguments": '{"query": "python"}',
                        },
                    },
                ],
            },
        ]

        result = ReMeShortTermMemory.list_to_msg(messages)

        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0].content), 2)
        self.assertEqual(result[0].content[0]["type"], "text")
        self.assertEqual(result[0].content[1]["type"], "tool_use")

    async def test_list_to_msg_with_metadata(self) -> None:
        """Test list_to_msg preserves metadata."""
        messages = [
            {
                "role": "user",
                "content": "Test message",
                "metadata": {"timestamp": "2025-01-01T12:00:00"},
            },
        ]

        result = ReMeShortTermMemory.list_to_msg(messages)

        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].metadata,
            {"timestamp": "2025-01-01T12:00:00"},
        )

    async def test_list_to_msg_with_empty_content(self) -> None:
        """Test list_to_msg with empty or None content."""
        messages = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": None},
        ]

        result = ReMeShortTermMemory.list_to_msg(messages)

        self.assertEqual(len(result), 2)
        # Empty content should still create a message
        self.assertEqual(len(result[0].content), 0)
        self.assertEqual(len(result[1].content), 0)

    async def test_list_to_msg_with_invalid_tool_call_arguments(self) -> None:
        """Test list_to_msg handles invalid tool call arguments."""
        messages = [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_invalid",
                        "function": {
                            "name": "test_tool",
                            "arguments": "invalid json",
                        },
                    },
                ],
            },
        ]

        # Should raise ValueError when JSON cannot be parsed
        with self.assertRaises(ValueError) as context:
            ReMeShortTermMemory.list_to_msg(messages)

        self.assertIn("Failed to decode JSON string", str(context.exception))

    async def test_retrieve_not_implemented(self) -> None:
        """Test that retrieve method raises NotImplementedError."""
        memory = self._create_memory_instance()

        with self.assertRaises(NotImplementedError):
            await memory.retrieve()

    async def test_add_inherited_method(self) -> None:
        """Test inherited add method from InMemoryMemory."""
        memory = self._create_memory_instance()

        msg = Msg(role="user", content="Test message", name="user")

        await memory.add(msg)

        self.assertEqual(len(memory.content), 1)
        self.assertEqual(memory.content[0].role, "user")

    async def test_add_multiple_messages(self) -> None:
        """Test adding multiple messages."""
        memory = self._create_memory_instance()

        msgs = [
            Msg(role="user", content="Message 1", name="user"),
            Msg(role="assistant", content="Message 2", name="assistant"),
        ]

        await memory.add(msgs)

        self.assertEqual(len(memory.content), 2)

    async def test_clear_inherited_method(self) -> None:
        """Test inherited clear method from InMemoryMemory."""
        memory = self._create_memory_instance()

        await memory.add(Msg(role="user", content="Test", name="user"))
        self.assertEqual(len(memory.content), 1)

        await memory.clear()
        self.assertEqual(len(memory.content), 0)

    async def test_size_inherited_method(self) -> None:
        """Test inherited size method from InMemoryMemory."""
        memory = self._create_memory_instance()

        self.assertEqual(await memory.size(), 0)

        await memory.add(Msg(role="user", content="Test", name="user"))
        self.assertEqual(await memory.size(), 1)

    async def test_get_memory_app_not_started(self) -> None:
        """Test get_memory when app context is not started."""
        memory = self._create_memory_instance()
        memory._app_started = False

        # get_memory doesn't check _app_started, but it will fail when
        # calling app.async_execute. Let's test the behavior.
        memory.formatter.format = AsyncMock(return_value=[])
        memory.app.async_execute = AsyncMock(
            side_effect=RuntimeError("App not started"),
        )

        with self.assertRaises(RuntimeError):
            await memory.get_memory()

    async def test_get_memory_with_empty_messages(self) -> None:
        """Test get_memory with empty message list."""
        memory = self._create_memory_instance()

        memory.formatter.format = AsyncMock(return_value=[])
        memory.app.async_execute = AsyncMock(
            return_value={"answer": [], "metadata": {}},
        )

        result = await memory.get_memory()

        self.assertEqual(len(result), 0)
        self.assertEqual(len(memory.content), 0)


if __name__ == "__main__":
    unittest.main()
