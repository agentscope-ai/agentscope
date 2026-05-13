# -*- coding: utf-8 -*-
"""Unittests for the tracing functionality in AgentScope."""
import asyncio
from typing import (
    AsyncGenerator,
    Generator,
    Any,
)
from unittest import IsolatedAsyncioTestCase

from agentscope import _config
from agentscope.agent import AgentBase
from agentscope.embedding import EmbeddingModelBase
from agentscope.formatter import FormatterBase
from agentscope.message import (
    TextBlock,
    Msg,
    ToolUseBlock,
)
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import Toolkit, ToolResponse
from agentscope.tracing import (
    trace,
    trace_llm,
    trace_reply,
    trace_format,
    trace_embedding,
)
from agentscope.tracing._trace import _check_tracing_enabled


class TracingTest(IsolatedAsyncioTestCase):
    """Test cases for tracing functionality"""

    async def asyncSetUp(self) -> None:
        """Set up the environment"""
        _config.trace_enabled = True

    async def test_trace(self) -> None:
        """Test the basic tracing functionality"""

        @trace(name="test_func")
        async def test_func(x: int) -> int:
            """Test async function""" ""
            return x * 2

        result = await test_func(5)
        self.assertEqual(result, 10)

        @trace(name="test_gen")
        async def test_gen() -> AsyncGenerator[str, None]:
            """Test async generator"""
            for i in range(3):
                yield f"chunk_{i}"

        results = [_ async for _ in test_gen()]
        self.assertListEqual(results, ["chunk_0", "chunk_1", "chunk_2"])

        @trace(name="test_func_return_with_sync_gen")
        async def test_func_return_with_sync_gen() -> Generator[
            str,
            None,
            None,
        ]:
            """Test async func returning sync generator"""

            def sync_gen() -> Generator[str, None, None]:
                """sync generator"""
                for i in range(3):
                    yield f"sync_chunk_{i}"

            return sync_gen()

        results = list(await test_func_return_with_sync_gen())
        self.assertListEqual(
            results,
            ["sync_chunk_0", "sync_chunk_1", "sync_chunk_2"],
        )

        @trace(name="sync_func")
        def sync_func(x: int) -> int:
            """Test synchronous function"""
            return x + 3

        result = sync_func(4)
        self.assertEqual(result, 7)

        @trace(name="sync_gen")
        def sync_gen() -> Generator[str, None, None]:
            """Test synchronous generator"""
            for i in range(3):
                yield f"sync_chunk_{i}"

        results = list(sync_gen())
        self.assertListEqual(
            results,
            ["sync_chunk_0", "sync_chunk_1", "sync_chunk_2"],
        )

        @trace(name="sync_func_return_with_async_gen")
        def sync_func_return_with_async_gen() -> AsyncGenerator[str, None]:
            """Test sync func returning async generator"""

            async def async_gen() -> AsyncGenerator[str, None]:
                """async generator"""
                for i in range(3):
                    yield f"chunk_{i}"

            return async_gen()

        results = [_ async for _ in sync_func_return_with_async_gen()]
        self.assertListEqual(results, ["chunk_0", "chunk_1", "chunk_2"])

        # Error handling
        @trace(name="error_sync_func")
        def error_sync_func() -> int:
            """Test error handling in sync function"""
            raise ValueError("Negative value not allowed")

        with self.assertRaises(ValueError):
            error_sync_func()

        @trace(name="error_async_func")
        async def error_async_func() -> int:
            """Test error handling in async function"""
            raise ValueError("Negative value not allowed")

        with self.assertRaises(ValueError):
            await error_async_func()

    async def test_trace_llm(self) -> None:
        """Test tracing LLM"""

        class LLM(ChatModelBase):
            """Test LLM class"""

            def __init__(self, stream: bool, raise_error: bool) -> None:
                """Initialize LLM"""
                super().__init__("test", stream)
                self.raise_error = raise_error

            @trace_llm
            async def __call__(
                self,
                messages: list[dict],
                **kwargs: Any,
            ) -> AsyncGenerator[ChatResponse, None] | ChatResponse:
                """Simulate LLM call"""

                if self.raise_error:
                    raise ValueError("Simulated error in LLM call")

                if self.stream:

                    async def generator() -> AsyncGenerator[
                        ChatResponse,
                        None,
                    ]:
                        for i in range(3):
                            yield ChatResponse(
                                id=f"msg_{i}",
                                content=[
                                    TextBlock(
                                        type="text",
                                        text="x" * (i + 1),
                                    ),
                                ],
                            )

                    return generator()
                return ChatResponse(
                    id="msg_0",
                    content=[
                        TextBlock(
                            type="text",
                            text="Hello, world!",
                        ),
                    ],
                )

        stream_llm = LLM(True, False)
        res = [_.content async for _ in await stream_llm([])]
        self.assertListEqual(
            res,
            [
                [TextBlock(type="text", text="x")],
                [TextBlock(type="text", text="xx")],
                [TextBlock(type="text", text="xxx")],
            ],
        )

        non_stream_llm = LLM(False, False)
        res = await non_stream_llm([])
        self.assertListEqual(
            res.content,
            [
                TextBlock(type="text", text="Hello, world!"),
            ],
        )

        error_llm = LLM(False, True)
        with self.assertRaises(ValueError):
            await error_llm([])

    async def test_trace_reply(self) -> None:
        """Test tracing reply"""

        class Agent(AgentBase):
            """Test Agent class"""

            @trace_reply
            async def reply(self, raise_error: bool = False) -> Msg:
                """Simulate agent reply"""
                if raise_error:
                    raise ValueError("Simulated error in reply")
                return Msg(
                    "assistant",
                    [TextBlock(type="text", text="Hello, world!")],
                    "assistant",
                )

            async def observe(self, msg: Msg) -> None:
                raise NotImplementedError()

            async def handle_interrupt(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> Msg:
                """Handle interrupt"""
                raise NotImplementedError()

        agent = Agent()
        res = await agent()
        self.assertListEqual(
            res.content,
            [TextBlock(type="text", text="Hello, world!")],
        )

        with self.assertRaises(ValueError):
            await agent.reply(raise_error=True)

    async def test_trace_format(self) -> None:
        """Test tracing formatter"""

        class Formatter(FormatterBase):
            """Test Formatter class"""

            @trace_format
            async def format(self, raise_error: bool = False) -> list[dict]:
                """Simulate formatting"""
                if raise_error:
                    raise ValueError("Simulated error in formatting")
                return [{"role": "user", "content": "Hello, world!"}]

        formatter = Formatter()
        res = await formatter.format()
        self.assertListEqual(
            res,
            [{"role": "user", "content": "Hello, world!"}],
        )

        with self.assertRaises(ValueError):
            await formatter.format(raise_error=True)

    async def test_trace_toolkit(self) -> None:
        """Test tracing toolkit"""
        toolkit = Toolkit()

        def func(raise_error: bool) -> ToolResponse:
            """Test tool function"""
            if raise_error:
                raise ValueError("Simulated error in tool function")
            return ToolResponse(
                content=[
                    TextBlock(type="text", text="Tool executed successfully"),
                ],
            )

        toolkit.register_tool_function(func)
        res = await toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="xxx",
                name="func",
                input={"raise_error": False},
            ),
        )
        async for chunk in res:
            self.assertListEqual(
                chunk.content,
                [TextBlock(type="text", text="Tool executed successfully")],
            )
        res = await toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="xxx",
                name="func",
                input={"raise_error": True},
            ),
        )
        async for chunk in res:
            self.assertListEqual(
                chunk.content,
                [
                    TextBlock(
                        type="text",
                        text="Error: Simulated error in tool function",
                    ),
                ],
            )

        async def gen_func(
            raise_error: bool,
        ) -> AsyncGenerator[ToolResponse, None]:
            """Test async generator tool function"""
            yield ToolResponse(
                content=[TextBlock(type="text", text="Chunk 0")],
            )
            if raise_error:
                raise ValueError(
                    "Simulated error in async generator tool function",
                )
            yield ToolResponse(
                content=[TextBlock(type="text", text="Chunk 1")],
            )

        toolkit.register_tool_function(gen_func)
        res = await toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="xxx",
                name="gen_func",
                input={"raise_error": False},
            ),
        )
        index = 0
        async for chunk in res:
            self.assertListEqual(
                chunk.content,
                [TextBlock(type="text", text=f"Chunk {index}")],
            )
            index += 1

        res = await toolkit.call_tool_function(
            ToolUseBlock(
                type="tool_use",
                id="xxx",
                name="gen_func",
                input={"raise_error": True},
            ),
        )
        with self.assertRaises(ValueError):
            async for _ in res:
                pass

    async def test_trace_embedding(self) -> None:
        """Test tracing embedding"""

        class EmbeddingModel(EmbeddingModelBase):
            """Test embedding model class"""

            def __init__(self) -> None:
                """Initialize embedding model"""
                super().__init__("test_embedding", 3)

            @trace_embedding
            async def __call__(self, raise_error: bool) -> list[list[float]]:
                """Simulate embedding call"""
                if raise_error:
                    raise ValueError("Simulated error in embedding call")
                return [[0, 1, 2]]

        model = EmbeddingModel()
        res = await model(False)
        self.assertListEqual(res, [[0, 1, 2]])

        with self.assertRaises(ValueError):
            await model(True)

    async def asyncTearDown(self) -> None:
        """Tear down the environment"""
        _config.trace_enabled = True


class AsyncContextLossTest(IsolatedAsyncioTestCase):
    """Test cases for async context loss issue (#1208) - Global tracing mode"""

    async def asyncSetUp(self) -> None:
        """Set up the environment for async context loss tests"""
        # Reset tracing settings to default
        _config.trace_enabled = False
        _config.global_trace_enabled = False
        # Clean up any existing TracerProvider
        from opentelemetry import trace as trace_api
        trace_api.set_tracer_provider(trace_api.NoOpTracerProvider())

    async def test_check_tracing_enabled_context_var_mode(self) -> None:
        """Test _check_tracing_enabled with ContextVar mode (default)"""
        # Initially, tracing is disabled
        self.assertFalse(_check_tracing_enabled())

        # Enable tracing via ContextVar
        _config.trace_enabled = True
        self.assertTrue(_check_tracing_enabled())

        # Disable tracing via ContextVar
        _config.trace_enabled = False
        self.assertFalse(_check_tracing_enabled())

    async def test_check_tracing_enabled_global_mode_without_tracer_provider(self) -> None:
        """Test global tracing mode without TracerProvider configured"""
        # Enable global tracing but no TracerProvider exists
        _config.trace_enabled = False
        _config.global_trace_enabled = True
        # Should return False because no TracerProvider is configured
        self.assertFalse(_check_tracing_enabled())

    async def test_async_task_tracing_with_global_enabled(self) -> None:
        """Test that tracing works in async tasks when global_trace_enabled is True"""

        @trace(name="async_task_function")
        async def async_task_function(x: int) -> int:
            """Function that will be traced in async task"""
            return x * 2

        # Test without global tracing (ContextVar might be lost in tasks)
        _config.trace_enabled = True
        _config.global_trace_enabled = False

        # Direct call should work
        result = await async_task_function(5)
        self.assertEqual(result, 10)

        # In async task, ContextVar might be lost, but function still executes
        task = asyncio.create_task(async_task_function(10))
        result = await task
        self.assertEqual(result, 20)

        # Now enable global tracing
        _config.global_trace_enabled = True

        # Both direct call and task should work reliably
        result = await async_task_function(15)
        self.assertEqual(result, 30)

        task = asyncio.create_task(async_task_function(20))
        result = await task
        self.assertEqual(result, 40)

        # Reset for other tests
        _config.global_trace_enabled = False

    async def test_async_task_tracing_with_global_enabled(self) -> None:
        """Test that tracing works in async tasks when global_trace_enabled is True"""

        @trace(name="async_task_function")
        async def async_task_function(x: int) -> int:
            """Function that will be traced in async task"""
            return x * 2

        # Test without global tracing (ContextVar might be lost in tasks)
        _config.trace_enabled = True
        _config.global_trace_enabled = False

        # Direct call should work
        result = await async_task_function(5)
        self.assertEqual(result, 10)

        # In async task, ContextVar might be lost, but function should still execute
        task = asyncio.create_task(async_task_function(10))
        result = await task
        self.assertEqual(result, 20)

        # Now enable global tracing
        _config.global_trace_enabled = True

        # Both direct call and task should work
        result = await async_task_function(15)
        self.assertEqual(result, 30)

        task = asyncio.create_task(async_task_function(20))
        result = await task
        self.assertEqual(result, 40)

        # Reset
        _config.global_trace_enabled = False
        _config.global_trace_enabled = False
        _config.trace_enabled = False

    async def test_async_context_loss_with_global_tracing(self) -> None:
        """Test that global tracing fixes async context loss"""

        @trace(name="async_function_in_task")
        async def async_function_in_task(x: int) -> int:
            """Function that might lose context in async task"""
            await asyncio.sleep(0.01)
            return x * 2

        # Test 1: Without global tracing (ContextVar mode)
        _config.trace_enabled = True
        _config.global_trace_enabled = False

        # Direct call should work
        result = await async_function_in_task(5)
        self.assertEqual(result, 10)

        # But in async task, ContextVar might be lost
        # (This simulates the issue #1208)
        task = asyncio.create_task(async_function_in_task(10))
        result = await task
        self.assertEqual(result, 20)

        # Test 2: With global tracing enabled
        _config.global_trace_enabled = True

        # Set up TracerProvider to simulate real tracing setup
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider
        trace_api.set_tracer_provider(TracerProvider())

        # Now both direct call and task should work
        result = await async_function_in_task(15)
        self.assertEqual(result, 30)

        task = asyncio.create_task(async_function_in_task(20))
        result = await task
        self.assertEqual(result, 40)

        # Clean up
        trace_api.set_tracer_provider(trace_api.NoOpTracerProvider())

    async def test_global_trace_enabled_override(self) -> None:
        """Test that global_trace_enabled can override ContextVar settings"""

        @trace(name="controlled_function")
        async def controlled_function() -> str:
            """Function to test control"""
            return "traced"

        # Set ContextVar to False
        _config.trace_enabled = False

        # Test 1: global_trace_enabled = False (default)
        _config.global_trace_enabled = False
        # Should not be traced (ContextVar is False)
        self.assertFalse(_check_tracing_enabled())

        # Test 2: global_trace_enabled = True but no TracerProvider
        _config.global_trace_enabled = True
        # Should return False (no TracerProvider exists)
        self.assertFalse(_check_tracing_enabled())

        # Test 3: With trace_enabled=True (normal ContextVar mode)
        _config.global_trace_enabled = False
        _config.trace_enabled = True
        self.assertTrue(_check_tracing_enabled())

        # Reset
        _config.trace_enabled = False
        _config.global_trace_enabled = False

    async def test_setup_tracing_with_global_enabled(self) -> None:
        """Test setup_tracing with global_trace_enabled parameter"""
        from agentscope.tracing import setup_tracing

        # Test setup_tracing with global_trace_enabled=True
        setup_tracing(
            endpoint="http://localhost:4318/v1/traces",
            global_trace_enabled=True
        )

        # Should enable global tracing
        self.assertTrue(_config.global_trace_enabled)

        # Without TracerProvider, should return False
        self.assertFalse(_check_tracing_enabled())

        # Clean up
        _config.global_trace_enabled = False
