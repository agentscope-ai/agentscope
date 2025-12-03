# -*- coding: utf-8 -*-
# flake8: noqa: E501
# pylint: disable=C0301,W0212
"""Unit tests for ReMeMemory classes (Personal, Tool, Task)."""
import os
import sys
import unittest
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch, AsyncMock, MagicMock

# Check Python version before importing reme dependencies
PYTHON_VERSION = sys.version_info
SKIP_REME_TESTS = PYTHON_VERSION < (3, 12)

if not SKIP_REME_TESTS:
    from agentscope.embedding import DashScopeTextEmbedding
    from agentscope.memory import (
        ReMePersonalLongTermMemory,
        ReMeToolLongTermMemory,
        ReMeTaskLongTermMemory,
    )
    from agentscope.message import Msg
    from agentscope.model import DashScopeChatModel
    from agentscope.tool import ToolResponse

# Get memory type from environment variable or command line argument
# Options: "personal", "tool", "task"
MEMORY_TYPE = os.environ.get("REME_MEMORY_TYPE", "personal").lower()
if not SKIP_REME_TESTS:
    print(f"MEMORY_TYPE: {MEMORY_TYPE}")
else:
    print(
        f"Skipping ReMeMemory tests: Python {PYTHON_VERSION.major}.{PYTHON_VERSION.minor} < 3.12",
    )


@unittest.skipIf(
    SKIP_REME_TESTS,
    f"ReMeMemory requires Python 3.12+, current version is {PYTHON_VERSION.major}.{PYTHON_VERSION.minor}",
)
class TestReMeMemory(IsolatedAsyncioTestCase):
    """Test cases for ReMeMemory (dynamically tests Personal, Tool, or Task memory)."""

    def setUp(self) -> None:
        """Set up test fixtures."""
        # Mock the model and embedding model to pass isinstance checks
        self.mock_model = MagicMock(spec=DashScopeChatModel)
        self.mock_model.model_name = "qwen3-max"
        self.mock_model.api_key = "test_api_key"

        self.mock_embedding_model = MagicMock(spec=DashScopeTextEmbedding)
        self.mock_embedding_model.model_name = "text-embedding-v4"
        self.mock_embedding_model.api_key = "test_embedding_key"
        self.mock_embedding_model.dimensions = 1024

        # Set the memory class based on MEMORY_TYPE
        self.memory_type = MEMORY_TYPE
        if self.memory_type == "tool":
            self.memory_class = ReMeToolLongTermMemory
            self.summary_operation = "add_tool_call_result"
        elif self.memory_type == "task":
            self.memory_class = ReMeTaskLongTermMemory
            self.summary_operation = "summary_task_memory"
        else:  # default to personal
            self.memory_class = ReMePersonalLongTermMemory
            self.summary_operation = "summary_personal_memory"

        print(f"\n=== Testing {self.memory_class.__name__} ===")

    def _create_memory_instance(self) -> Any:
        """Create a ReMeMemory instance with mocked dependencies."""
        with patch("reme_ai.ReMeApp"):
            memory = self.memory_class(
                agent_name="TestAgent",
                user_name="test_user",
                model=self.mock_model,
                embedding_model=self.mock_embedding_model,
            )
            # Mock the app attribute
            memory.app = AsyncMock()
            memory._app_started = True
            memory.workspace_id = "test_workspace_123"
            return memory

    async def test_init_with_default_params(self) -> None:
        """Test initialization with default parameters."""
        with patch("reme_ai.ReMeApp"):
            memory = self.memory_class(
                agent_name="Friday",
                user_name="user_123",
                model=self.mock_model,
                embedding_model=self.mock_embedding_model,
            )
            self.assertEqual(memory.agent_name, "Friday")
            self.assertEqual(memory.workspace_id, "user_123")
            self.assertIsNotNone(memory.app)

    async def test_record_to_memory_success(self) -> None:
        """Test successful memory recording via record_to_memory tool."""
        memory = self._create_memory_instance()

        # Prepare test data based on memory type
        mock_result: dict = {}
        if self.memory_type == "tool":
            # Tool memory expects JSON strings with tool_call_result format
            import json

            content = [
                json.dumps(
                    {
                        "create_time": "2025-01-01T12:00:00",
                        "tool_name": "search_web",
                        "input": {"query": "Hangzhou travel"},
                        "output": "Found 10 results",
                        "token_cost": 100,
                        "success": True,
                        "time_cost": 1.5,
                    },
                ),
                json.dumps(
                    {
                        "create_time": "2025-01-01T12:01:00",
                        "tool_name": "book_hotel",
                        "input": {"location": "Hangzhou"},
                        "output": "Booking confirmed",
                        "token_cost": 150,
                        "success": True,
                        "time_cost": 2.0,
                    },
                ),
            ]
            expected_count = 2
            mock_result = {"status": "success"}
        elif self.memory_type == "task":
            # Task memory expects task execution information
            content = [
                "Task: Plan Hangzhou trip",
                "Step 1: Research destinations",
                "Step 2: Book accommodations",
            ]
            expected_count = 3
            mock_result = {"status": "success"}
        else:  # personal
            # Personal memory expects natural language content
            content = [
                "I prefer to stay in homestays when traveling to Hangzhou",
                "I like to visit the West Lake in the morning",
                "I enjoy drinking Longjing tea",
            ]
            expected_count = 3
            mock_result = {
                "metadata": {
                    "memory_list": [
                        {"content": "Prefer homestays in Hangzhou"},
                        {"content": "Visit West Lake in morning"},
                        {"content": "Enjoy Longjing tea"},
                    ],
                },
            }

        memory.app.async_execute = AsyncMock(return_value=mock_result)

        # Test recording
        result = await memory.record_to_memory(
            thinking="Recording important information",
            content=content,
        )

        # Verify result
        self.assertIsInstance(result, ToolResponse)
        self.assertGreater(len(result.content), 0)
        text_content = result.content[0].get("text", "")

        # Verify success message contains the expected count
        if self.memory_type == "tool":
            self.assertIn(
                "Successfully recorded 2 tool execution",
                text_content,
            )
        else:
            self.assertIn(
                f"Successfully recorded {expected_count}",
                text_content,
            )

        # Verify app.async_execute was called
        memory.app.async_execute.assert_called()
        self.assertEqual(
            memory.app.async_execute.call_args[1]["workspace_id"],
            "test_workspace_123",
        )

    async def test_record_to_memory_app_not_started(self) -> None:
        """Test record_to_memory when app context is not started."""
        memory = self._create_memory_instance()
        memory._app_started = False

        # Should raise RuntimeError when app is not started
        with self.assertRaises(RuntimeError) as context:
            await memory.record_to_memory(
                thinking="Test thinking",
                content=["Test content"],
            )

        self.assertIn("ReMeApp context not started", str(context.exception))

    async def test_record_to_memory_error_handling(self) -> None:
        """Test error handling in record_to_memory."""
        memory = self._create_memory_instance()

        # Tool memory has different behavior - it validates JSON first
        if self.memory_type == "tool":
            # For tool memory, test with invalid JSON that triggers the "No valid tool call results" path
            result = await memory.record_to_memory(
                thinking="Test thinking",
                content=["Test content"],  # Invalid JSON
            )

            self.assertIsInstance(result, ToolResponse)
            text_content = result.content[0].get("text", "")
            self.assertIn("No valid tool call results to record", text_content)
        else:
            # For task and personal memory, test with connection error
            memory.app.async_execute = AsyncMock(
                side_effect=Exception("Connection error"),
            )

            result = await memory.record_to_memory(
                thinking="Test thinking",
                content=["Test content"],
            )

            self.assertIsInstance(result, ToolResponse)
            text_content = result.content[0].get("text", "")

            # Different memory types have different error messages
            if self.memory_type == "task":
                self.assertIn("Error recording task memory", text_content)
            else:  # personal
                self.assertIn("Error recording memory", text_content)

            self.assertIn("Connection error", text_content)

    async def test_retrieve_from_memory_success(self) -> None:
        """Test successful memory retrieval via retrieve_from_memory tool."""
        memory = self._create_memory_instance()

        # Mock the app.async_execute response based on memory type
        if self.memory_type == "tool":
            # Tool memory expects tool_names parameter and returns tool guidelines
            def mock_retrieve(**kwargs: Any) -> dict:
                tool_names = kwargs.get("tool_names", "")
                if "search_web" in tool_names or "book_hotel" in tool_names:
                    return {
                        "answer": "Tool usage guidelines for search_web and book_hotel.",
                    }
                return {"answer": ""}

            memory.app.async_execute = AsyncMock(side_effect=mock_retrieve)

            # Test retrieval with tool names
            result = await memory.retrieve_from_memory(
                keywords=["search_web", "book_hotel"],
            )

            # Verify result
            self.assertIsInstance(result, ToolResponse)
            text_content = result.content[0].get("text", "")
            self.assertIn("Tool usage guidelines", text_content)

            # Tool memory combines all keywords into one call
            self.assertEqual(memory.app.async_execute.call_count, 1)

        elif self.memory_type == "task":
            # Task memory expects query parameter and returns task experiences
            def mock_retrieve(**kwargs: Any) -> dict:
                query = kwargs.get("query", "")
                if "Hangzhou" in query:
                    return {
                        "answer": "Task experience: Planning a trip to Hangzhou requires research and booking.",
                    }
                elif "travel" in query:
                    return {
                        "answer": "Task experience: Travel planning involves multiple steps.",
                    }
                return {"answer": ""}

            memory.app.async_execute = AsyncMock(side_effect=mock_retrieve)

            # Test retrieval
            result = await memory.retrieve_from_memory(
                keywords=["Hangzhou trip", "travel planning"],
            )

            # Verify result
            self.assertIsInstance(result, ToolResponse)
            text_content = result.content[0].get("text", "")
            self.assertIn("Keyword 'Hangzhou trip'", text_content)
            self.assertIn("Task experience", text_content)

            # Task memory calls once per keyword
            self.assertEqual(memory.app.async_execute.call_count, 2)

        else:  # personal
            # Personal memory expects query parameter and returns personal preferences
            def mock_retrieve(**kwargs: Any) -> dict:
                keyword = kwargs.get("query", "")
                if "Hangzhou" in keyword:
                    return {
                        "answer": "User prefers homestays in Hangzhou and visits West Lake in the morning.",
                    }
                elif "tea" in keyword:
                    return {
                        "answer": "User enjoys drinking Longjing tea.",
                    }
                return {"answer": ""}

            memory.app.async_execute = AsyncMock(side_effect=mock_retrieve)

            # Test retrieval
            result = await memory.retrieve_from_memory(
                keywords=["Hangzhou travel", "tea preference"],
            )

            # Verify result
            self.assertIsInstance(result, ToolResponse)
            text_content = result.content[0].get("text", "")
            self.assertIn("Keyword 'Hangzhou travel'", text_content)
            self.assertIn("homestays", text_content)
            self.assertIn("Keyword 'tea preference'", text_content)
            self.assertIn("Longjing tea", text_content)

            # Personal memory calls once per keyword
            self.assertEqual(memory.app.async_execute.call_count, 2)

    async def test_retrieve_from_memory_no_results(self) -> None:
        """Test retrieve_from_memory when no memories are found."""
        memory = self._create_memory_instance()

        # Mock empty response
        memory.app.async_execute = AsyncMock(return_value={"answer": ""})

        result = await memory.retrieve_from_memory(
            keywords=["nonexistent keyword"],
        )

        self.assertIsInstance(result, ToolResponse)
        text_content = result.content[0].get("text", "")

        # Different memory types have different "not found" messages
        if self.memory_type == "tool":
            self.assertIn("No tool guidelines found", text_content)
        elif self.memory_type == "task":
            self.assertIn("No task experiences found", text_content)
        else:  # personal
            self.assertIn("No memories found", text_content)

    async def test_retrieve_from_memory_app_not_started(self) -> None:
        """Test retrieve_from_memory when app context is not started."""
        memory = self._create_memory_instance()
        memory._app_started = False

        # Should raise RuntimeError when app is not started
        with self.assertRaises(RuntimeError) as context:
            await memory.retrieve_from_memory(
                keywords=["test"],
            )

        self.assertIn("ReMeApp context not started", str(context.exception))

    async def test_record_direct_method_success(self) -> None:
        """Test direct record method with message list."""
        memory = self._create_memory_instance()

        # Mock successful recording
        memory.app.async_execute = AsyncMock(
            return_value={"status": "success"},
        )

        # Prepare messages based on memory type
        if self.memory_type == "tool":
            # Tool memory expects JSON strings with tool call results
            import json

            msgs = [
                Msg(
                    role="user",
                    content=json.dumps(
                        {
                            "create_time": "2025-01-01T12:00:00",
                            "tool_name": "search",
                            "input": {"query": "test"},
                            "output": "result",
                            "token_cost": 100,
                            "success": True,
                            "time_cost": 1.0,
                        },
                    ),
                    name="user",
                ),
            ]
        else:
            # Task and Personal memory work with regular messages
            msgs = [
                Msg(
                    role="user",
                    content="I work as a software engineer",
                    name="user",
                ),
                Msg(
                    role="assistant",
                    content="Understood!",
                    name="assistant",
                ),
                Msg(
                    role="user",
                    content="I prefer remote work",
                    name="user",
                ),
            ]

        # Should not raise any exception
        await memory.record(msgs)

        # Verify app.async_execute was called
        memory.app.async_execute.assert_called()
        call_args = memory.app.async_execute.call_args[1]
        self.assertEqual(call_args["workspace_id"], "test_workspace_123")

    async def test_record_direct_with_single_message(self) -> None:
        """Test direct record method with a single message."""
        memory = self._create_memory_instance()

        memory.app.async_execute = AsyncMock(
            return_value={"status": "success"},
        )

        # Tool memory requires JSON-formatted tool call results
        if self.memory_type == "tool":
            import json

            msg = Msg(
                role="user",
                content=json.dumps(
                    {
                        "create_time": "2025-01-01T12:00:00",
                        "tool_name": "test_tool",
                        "input": {"param": "value"},
                        "output": "result",
                        "token_cost": 10,
                        "success": True,
                        "time_cost": 0.1,
                    },
                ),
                name="user",
            )
        else:
            msg = Msg(
                role="user",
                content="Single message test",
                name="user",
            )

        # Should handle single message
        await memory.record(msg)

        # Tool memory calls async_execute twice (add + summarize)
        if self.memory_type == "tool":
            self.assertEqual(memory.app.async_execute.call_count, 2)
        else:
            memory.app.async_execute.assert_called_once()

    async def test_record_direct_with_empty_list(self) -> None:
        """Test direct record method with empty message list."""
        memory = self._create_memory_instance()

        memory.app.async_execute = AsyncMock()

        # Should return early without calling app
        await memory.record([])

        memory.app.async_execute.assert_not_called()

    async def test_record_direct_filters_none_messages(self) -> None:
        """Test that record method filters out None messages."""
        memory = self._create_memory_instance()

        memory.app.async_execute = AsyncMock(
            return_value={"status": "success"},
        )

        # Tool memory requires JSON-formatted tool call results
        if self.memory_type == "tool":
            import json

            msgs = [
                Msg(
                    role="user",
                    content=json.dumps(
                        {
                            "create_time": "2025-01-01T12:00:00",
                            "tool_name": "tool1",
                            "input": {},
                            "output": "result1",
                            "token_cost": 10,
                            "success": True,
                            "time_cost": 0.1,
                        },
                    ),
                    name="user",
                ),
                None,
                Msg(
                    role="assistant",
                    content=json.dumps(
                        {
                            "create_time": "2025-01-01T12:01:00",
                            "tool_name": "tool2",
                            "input": {},
                            "output": "result2",
                            "token_cost": 20,
                            "success": True,
                            "time_cost": 0.2,
                        },
                    ),
                    name="assistant",
                ),
                None,
            ]
        else:
            msgs = [
                Msg(role="user", content="Valid message", name="user"),
                None,
                Msg(
                    role="assistant",
                    content="Another valid",
                    name="assistant",
                ),
                None,
            ]

        await memory.record(msgs)

        # Tool memory calls async_execute twice (add + summarize)
        if self.memory_type == "tool":
            self.assertEqual(memory.app.async_execute.call_count, 2)
        else:
            # Should still be called with filtered messages
            memory.app.async_execute.assert_called_once()

    async def test_record_direct_app_not_started(self) -> None:
        """Test record method when app is not started."""
        memory = self._create_memory_instance()
        memory._app_started = False

        msgs = [Msg(role="user", content="Test", name="user")]

        # Should raise RuntimeError when app is not started
        with self.assertRaises(RuntimeError) as context:
            await memory.record(msgs)

        self.assertIn("ReMeApp context not started", str(context.exception))

    async def test_retrieve_direct_method_success(self) -> None:
        """Test direct retrieve method with message."""
        memory = self._create_memory_instance()

        # Prepare test data based on memory type
        if self.memory_type == "tool":
            mock_response = {
                "answer": "Tool guidelines for search and analysis tools.",
            }
            expected_content = "Tool guidelines"
            expected_operation = "retrieve_tool_memory"
        elif self.memory_type == "task":
            mock_response = {
                "answer": "Task experience with work-related projects.",
            }
            expected_content = "Task experience"
            expected_operation = "retrieve_task_memory"
        else:  # personal
            mock_response = {
                "answer": "You are a software engineer who prefers remote work.",
            }
            expected_content = "software engineer"
            expected_operation = "retrieve_personal_memory"

        # Mock the retrieval response
        memory.app.async_execute = AsyncMock(return_value=mock_response)

        msg = Msg(
            role="user",
            content="What do you know about my work preferences?",
            name="user",
        )

        result = await memory.retrieve(msg)

        # Verify result
        self.assertIsInstance(result, str)
        self.assertIn(expected_content, result)

        # Verify app.async_execute was called
        memory.app.async_execute.assert_called_once()
        call_args = memory.app.async_execute.call_args[1]
        self.assertEqual(call_args["name"], expected_operation)

    async def test_retrieve_direct_with_message_list(self) -> None:
        """Test direct retrieve method with list of messages."""
        memory = self._create_memory_instance()

        memory.app.async_execute = AsyncMock(
            return_value={"answer": "Test answer"},
        )

        msgs = [
            Msg(role="user", content="First message", name="user"),
            Msg(role="user", content="Last message for query", name="user"),
        ]

        result = await memory.retrieve(msgs)

        self.assertIsInstance(result, str)
        # Should use the last message's content
        call_args = memory.app.async_execute.call_args[1]

        # Tool memory uses tool_names parameter, others use query
        if self.memory_type == "tool":
            # Tool memory extracts tool names from content
            self.assertIn("tool_names", call_args)
        else:
            self.assertIn("Last message for query", call_args["query"])

    async def test_retrieve_direct_with_none_message(self) -> None:
        """Test direct retrieve method with None message."""
        memory = self._create_memory_instance()

        result = await memory.retrieve(None)

        # Should return empty string
        self.assertEqual(result, "")

    async def test_retrieve_direct_invalid_input(self) -> None:
        """Test direct retrieve method with invalid input."""
        memory = self._create_memory_instance()

        # Should raise TypeError for invalid input
        with self.assertRaises(TypeError) as context:
            await memory.retrieve("invalid string input")

        self.assertIn("must be a Msg or a list of Msg", str(context.exception))

    async def test_retrieve_direct_app_not_started(self) -> None:
        """Test retrieve method when app is not started."""
        memory = self._create_memory_instance()
        memory._app_started = False

        msg = Msg(role="user", content="Test", name="user")

        # Should raise RuntimeError
        with self.assertRaises(RuntimeError) as context:
            await memory.retrieve(msg)

        self.assertIn("ReMeApp context not started", str(context.exception))

    async def test_context_manager_usage(self) -> None:
        """Test using ReMeMemory as async context manager."""
        with patch("reme_ai.ReMeApp") as MockReMeApp:
            mock_app = AsyncMock()
            mock_app.__aenter__ = AsyncMock(return_value=mock_app)
            mock_app.__aexit__ = AsyncMock(return_value=None)
            MockReMeApp.return_value = mock_app

            memory = self.memory_class(
                agent_name="TestAgent",
                user_name="test_user",
                model=self.mock_model,
                embedding_model=self.mock_embedding_model,
            )

            # Use as context manager
            async with memory as mem:
                self.assertIsNotNone(mem)
                # The app should be started
                self.assertTrue(hasattr(mem, "app"))

    async def test_integration_record_and_retrieve(self) -> None:
        """Test integration of recording and retrieving memories."""
        memory = self._create_memory_instance()

        # Prepare test data based on memory type
        if self.memory_type == "tool":
            import json

            content = [
                json.dumps(
                    {
                        "create_time": "2025-01-01T12:00:00",
                        "tool_name": "python_executor",
                        "input": {"code": "print('hello')"},
                        "output": "hello",
                        "token_cost": 50,
                        "success": True,
                        "time_cost": 0.5,
                    },
                ),
            ]
            keywords = ["python_executor"]
            expected_text = "Tool usage guidelines"
        elif self.memory_type == "task":
            content = ["Task: Execute Python code successfully"]
            keywords = ["Python execution"]
            expected_text = "Task experience"
        else:  # personal
            content = ["I like Python programming"]
            keywords = ["programming preferences"]
            expected_text = "Python programming"

        # Mock record response
        memory.app.async_execute = AsyncMock(
            return_value={
                "metadata": {"memory_list": [{"content": "test"}]},
            },
        )

        # Record some memories
        record_result = await memory.record_to_memory(
            thinking="Recording preferences",
            content=content,
        )

        self.assertIn(
            "Successfully recorded",
            record_result.content[0]["text"],
        )

        # Mock retrieve response
        if self.memory_type == "tool":
            memory.app.async_execute = AsyncMock(
                return_value={
                    "answer": "Tool usage guidelines for python_executor.",
                },
            )
        elif self.memory_type == "task":
            memory.app.async_execute = AsyncMock(
                return_value={
                    "answer": "Task experience: Execute Python code successfully.",
                },
            )
        else:  # personal
            memory.app.async_execute = AsyncMock(
                return_value={
                    "answer": "You like Python programming.",
                },
            )

        # Retrieve the memories
        retrieve_result = await memory.retrieve_from_memory(
            keywords=keywords,
        )

        self.assertIn(expected_text, retrieve_result.content[0]["text"])


if __name__ == "__main__":
    unittest.main()
