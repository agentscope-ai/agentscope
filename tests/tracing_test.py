# -*- coding: utf-8 -*-
"""Unittests for the tracing functionality in AgentScope."""
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
