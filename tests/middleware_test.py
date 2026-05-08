# -*- coding: utf-8 -*-
# pylint: disable=abstract-method
"""Unit tests for middleware system."""
from unittest.async_case import IsolatedAsyncioTestCase
from typing import Any, AsyncGenerator, Callable, Union

from utils import MockModel

from agentscope.event import AgentEvent
from agentscope.agent import Agent
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.message import TextBlock, UserMsg, Msg
from agentscope.tool import Toolkit


class TestMiddleware(IsolatedAsyncioTestCase):
    """Test cases for middleware system."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.mock_model = MockModel()
        self.toolkit = Toolkit()
        self.execution_log = []

    async def test_on_reply_middleware_pre_post_yield(self) -> None:
        """Test on_reply middleware pre, post and yield positions."""

        class ReplyMiddleware(MiddlewareBase):
            """Middleware for testing on_reply hook."""

            def __init__(self, log: list, name: str) -> None:
                """Initialize the reply middleware.

                Args:
                    log: The execution log list.
                    name: The middleware name.
                """
                self.log = log
                self.name = name

            async def on_reply(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[[], AsyncGenerator],
            ) -> AsyncGenerator:
                """The on_reply middleware logic."""
                self.log.append(f"{self.name}_pre")
                async for item in next_handler():
                    if isinstance(item, AgentEvent):
                        self.log.append(f"{self.name}_{item.type}")
                    elif isinstance(item, Msg):
                        self.log.append(f"{self.name}_msg")
                    yield item
                self.log.append(f"{self.name}_post")

        middleware1 = ReplyMiddleware(self.execution_log, "mw1")
        middleware2 = ReplyMiddleware(self.execution_log, "mw2")

        self.mock_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="test response")],
                    is_last=True,
                ),
            ],
        )

        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[middleware1, middleware2],
        )

        await agent.reply(UserMsg("user", "test message"))

        # Verify execution order
        expected = [
            "mw1_pre",
            "mw2_pre",
            "mw2_REPLY_START",
            "mw1_REPLY_START",
            "mw2_MODEL_CALL_START",
            "mw1_MODEL_CALL_START",
            "mw2_TEXT_BLOCK_START",
            "mw1_TEXT_BLOCK_START",
            "mw2_TEXT_BLOCK_DELTA",
            "mw1_TEXT_BLOCK_DELTA",
            "mw2_TEXT_BLOCK_END",
            "mw1_TEXT_BLOCK_END",
            "mw2_MODEL_CALL_END",
            "mw1_MODEL_CALL_END",
            "mw2_REPLY_END",
            "mw1_REPLY_END",
            "mw2_msg",
            "mw1_msg",
            "mw2_post",
            "mw1_post",
        ]
        self.assertListEqual(self.execution_log, expected)

    async def test_on_reasoning_middleware_pre_yield(self) -> None:
        """Test on_reasoning middleware pre and yield positions."""

        class ReasoningMiddleware(MiddlewareBase):
            """Middleware for testing on_reasoning hook."""

            def __init__(self, log: list, name: str) -> None:
                """Initialize the reasoning middleware.

                Args:
                    log: The execution log list.
                    name: The middleware name.
                """
                self.log = log
                self.name = name

            async def on_reasoning(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[[], AsyncGenerator],
            ) -> AsyncGenerator:
                """The on_reasoning middleware logic."""
                self.log.append(f"{self.name}_pre")
                async for item in next_handler():
                    if isinstance(item, AgentEvent):
                        self.log.append(f"{self.name}_{item.type}")
                    elif isinstance(item, Msg):
                        self.log.append(f"{self.name}_msg")
                    yield item
                self.log.append(f"{self.name}_post")

        middleware1 = ReasoningMiddleware(self.execution_log, "mw1")
        middleware2 = ReasoningMiddleware(self.execution_log, "mw2")

        self.mock_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="test response")],
                    is_last=True,
                ),
            ],
        )

        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[middleware1, middleware2],
        )

        await agent.reply(UserMsg("user", "test message"))

        # Verify execution order
        expected = [
            "mw1_pre",
            "mw2_pre",
            "mw2_MODEL_CALL_START",
            "mw1_MODEL_CALL_START",
            "mw2_TEXT_BLOCK_START",
            "mw1_TEXT_BLOCK_START",
            "mw2_TEXT_BLOCK_DELTA",
            "mw1_TEXT_BLOCK_DELTA",
            "mw2_TEXT_BLOCK_END",
            "mw1_TEXT_BLOCK_END",
            "mw2_MODEL_CALL_END",
            "mw1_MODEL_CALL_END",
            "mw2_msg",
            "mw1_msg",
        ]
        self.assertListEqual(self.execution_log, expected)

    async def test_on_model_call_middleware_non_streaming(self) -> None:
        """Test on_model_call middleware for non-streaming model."""

        class ModelCallMiddleware(MiddlewareBase):
            """Middleware for testing on_model_call hook."""

            def __init__(self, log: list, name: str) -> None:
                """Initialize the model call middleware.

                Args:
                    log: The execution log list.
                    name: The middleware name.
                """
                self.log = log
                self.name = name

            async def on_model_call(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
                """The on_model_call middleware logic."""
                self.log.append(f"{self.name}_pre")
                result = await next_handler()
                self.log.append(f"{self.name}_post")
                return result

        middleware1 = ModelCallMiddleware(self.execution_log, "mw1")
        middleware2 = ModelCallMiddleware(self.execution_log, "mw2")

        self.mock_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="test response")],
                    is_last=True,
                ),
            ],
        )

        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[middleware1, middleware2],
        )

        await agent.reply(UserMsg("user", "test message"))

        # Verify execution order: mw1_pre -> mw2_pre -> mw2_post -> mw1_post
        expected = ["mw1_pre", "mw2_pre", "mw2_post", "mw1_post"]
        self.assertListEqual(self.execution_log, expected)

    async def test_on_model_call_middleware_streaming(self) -> None:
        """Test on_model_call middleware for streaming model."""

        class ModelCallMiddleware(MiddlewareBase):
            """Middleware for testing on_model_call hook with streaming."""

            def __init__(self, log: list, name: str) -> None:
                """Initialize the model call middleware.

                Args:
                    log: The execution log list.
                    name: The middleware name.
                """
                self.log = log
                self.name = name

            async def on_model_call(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
                """The on_model_call middleware logic for streaming."""
                self.log.append(f"{self.name}_pre")
                result = await next_handler()

                async def wrapped_generator() -> AsyncGenerator[
                    ChatResponse,
                    None,
                ]:
                    """Wrap the generator to log yields."""
                    async for chunk in result:
                        self.log.append(f"{self.name}_chunk")
                        yield chunk
                    self.log.append(f"{self.name}_post")

                return wrapped_generator()

        middleware1 = ModelCallMiddleware(self.execution_log, "mw1")
        middleware2 = ModelCallMiddleware(self.execution_log, "mw2")

        self.mock_model.set_responses(
            [
                [
                    ChatResponse(
                        content=[TextBlock(text="chunk1")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="chunk2")],
                        is_last=False,
                    ),
                    ChatResponse(
                        content=[TextBlock(text="chunk3")],
                        is_last=True,
                    ),
                ],
            ],
        )

        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[middleware1, middleware2],
        )

        await agent.reply(UserMsg("user", "test message"))

        # Verify execution order
        expected = [
            "mw1_pre",
            "mw2_pre",
            "mw2_chunk",
            "mw1_chunk",
            "mw2_chunk",
            "mw1_chunk",
            "mw2_chunk",
            "mw1_chunk",
        ]
        self.assertListEqual(self.execution_log, expected)

    async def test_on_system_prompt_middleware(self) -> None:
        """Test on_system_prompt middleware (transformer pattern)."""

        class SystemPromptMiddleware(MiddlewareBase):
            """Middleware for testing on_system_prompt hook."""

            def __init__(self, log: list, name: str, suffix: str) -> None:
                """Initialize the system prompt middleware.

                Args:
                    log: The execution log list.
                    name: The middleware name.
                    suffix: The suffix to append to the prompt.
                """
                self.log = log
                self.name = name
                self.suffix = suffix

            async def on_system_prompt(
                self,
                agent: Agent,
                current_prompt: str,
            ) -> str:
                """The on_system_prompt middleware logic."""
                self.log.append(f"{self.name}_executed")
                return f"{current_prompt} {self.suffix}"

        middleware1 = SystemPromptMiddleware(
            self.execution_log,
            "mw1",
            "[MW1]",
        )
        middleware2 = SystemPromptMiddleware(
            self.execution_log,
            "mw2",
            "[MW2]",
        )

        self.mock_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="test response")],
                    is_last=True,
                ),
            ],
        )

        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[middleware1, middleware2],
        )

        await agent.reply(UserMsg("user", "test message"))

        # Verify execution order: mw1 -> mw2 (sequential transformer pattern)
        # Note: system_prompt is called twice (once for initial setup, once
        # during reasoning)
        expected = [
            "mw1_executed",
            "mw2_executed",  # First call
            "mw1_executed",
            "mw2_executed",  # Second call during reasoning
        ]
        self.assertListEqual(self.execution_log, expected)

    async def test_multiple_middleware_types(self) -> None:
        """Test multiple middleware types working together."""

        class MultiMiddleware(MiddlewareBase):
            """Middleware implementing multiple hooks."""

            def __init__(self, log: list, name: str) -> None:
                """Initialize the multi middleware.

                Args:
                    log: The execution log list.
                    name: The middleware name.
                """
                self.log = log
                self.name = name

            async def on_reply(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[[], AsyncGenerator],
            ) -> AsyncGenerator:
                """The on_reply middleware logic."""
                self.log.append(f"{self.name}_reply_pre")
                async for item in next_handler():
                    yield item
                self.log.append(f"{self.name}_reply_post")

            async def on_reasoning(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[[], AsyncGenerator],
            ) -> AsyncGenerator:
                """The on_reasoning middleware logic."""
                self.log.append(f"{self.name}_reasoning_pre")
                async for item in next_handler():
                    yield item
                self.log.append(f"{self.name}_reasoning_post")

            async def on_model_call(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable,
            ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
                """The on_model_call middleware logic."""
                self.log.append(f"{self.name}_model_call_pre")
                result = await next_handler()
                self.log.append(f"{self.name}_model_call_post")
                return result

            async def on_system_prompt(
                self,
                agent: Agent,
                current_prompt: str,
            ) -> str:
                """The on_system_prompt middleware logic."""
                self.log.append(f"{self.name}_system_prompt")
                return current_prompt

        middleware = MultiMiddleware(self.execution_log, "multi")

        self.mock_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="test response")],
                    is_last=True,
                ),
            ],
        )

        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        await agent.reply(UserMsg("user", "test message"))

        # Verify all middleware hooks were called
        expected = [
            "multi_reply_pre",
            "multi_system_prompt",
            "multi_reasoning_pre",
            "multi_system_prompt",
            "multi_model_call_pre",
            "multi_model_call_post",
            "multi_reply_post",
        ]
        self.assertListEqual(self.execution_log, expected)

    async def test_on_reply_middleware_modify_input(self) -> None:
        """Test that on_reply middleware can modify msgs input."""

        class ModifyMsgsMiddleware(MiddlewareBase):
            """Middleware that modifies the msgs input."""

            async def on_reply(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                """Modify msgs before passing to next handler."""
                # Modify the message content
                msgs = input_kwargs["msgs"]
                if isinstance(msgs, Msg):
                    modified_msg = UserMsg(
                        name=msgs.name,
                        content="MODIFIED: " + msgs.content,
                    )
                    async for item in next_handler(
                        msgs=modified_msg,
                        event=input_kwargs.get("event"),
                    ):
                        yield item
                else:
                    async for item in next_handler(**input_kwargs):
                        yield item

        middleware = ModifyMsgsMiddleware()

        # Track what message the model receives
        received_messages = []

        class TrackingModel(MockModel):
            """Model that tracks received messages."""

            async def _call_api(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> ChatResponse:
                """Track the messages and return mock response."""
                messages = kwargs.get("messages", [])
                received_messages.extend(messages)
                return await super()._call_api(*args, **kwargs)

        tracking_model = TrackingModel()
        tracking_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="response")],
                    is_last=True,
                ),
            ],
        )

        agent_instance = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=tracking_model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        await agent_instance.reply(UserMsg("user", "original message"))

        # Verify the model received the modified message
        user_messages = [m for m in received_messages if m.role == "user"]
        self.assertTrue(len(user_messages) > 0)
        self.assertIn("MODIFIED: original message", user_messages[-1].content)

    async def test_on_reasoning_middleware_modify_input(self) -> None:
        """Test that on_reasoning middleware can modify tool_choice input."""

        class ModifyToolChoiceMiddleware(MiddlewareBase):
            """Middleware that modifies the tool_choice input."""

            async def on_reasoning(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                """Force tool_choice to 'none' to prevent tool calls."""
                # Override tool_choice to 'none'
                async for item in next_handler(tool_choice="none"):
                    yield item

        middleware = ModifyToolChoiceMiddleware()

        # Track what tool_choice the model receives
        received_tool_choices = []

        class TrackingModel(MockModel):
            """Model that tracks received tool_choice."""

            async def _call_api(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> ChatResponse:
                """Track the tool_choice and return mock response."""
                tool_choice = kwargs.get("tool_choice")
                received_tool_choices.append(tool_choice)
                return await super()._call_api(*args, **kwargs)

        tracking_model = TrackingModel()
        tracking_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="response without tools")],
                    is_last=True,
                ),
            ],
        )

        agent_instance = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=tracking_model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        await agent_instance.reply(UserMsg("user", "test message"))

        # Verify the model received tool_choice='none'
        self.assertIn("none", received_tool_choices)

    async def test_on_acting_middleware_modify_input(self) -> None:
        """Test that on_acting middleware can modify tool_call input."""
        from agentscope.message import ToolCallBlock

        # Track what tool_call the implementation receives
        received_tool_calls = []

        class ModifyToolCallMiddleware(MiddlewareBase):
            """Middleware that modifies the tool_call input."""

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                """Modify tool_call.input before execution."""
                tool_call = input_kwargs["tool_call"]
                # Create a modified tool call with different input
                modified_tool_call = ToolCallBlock(
                    id=tool_call.id,
                    name=tool_call.name,
                    input='{"value": "MODIFIED"}',
                    state=tool_call.state,
                )
                # Track what we're passing to next handler
                received_tool_calls.append(modified_tool_call.input)
                async for item in next_handler(tool_call=modified_tool_call):
                    yield item

        middleware = ModifyToolCallMiddleware()

        # Create a mock agent with the middleware
        agent_instance = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        # Directly call _execute_tool_call to test the middleware chain
        original_tool_call = ToolCallBlock(
            id="call_1",
            name="test_tool",
            input='{"value": "ORIGINAL"}',
        )

        # Collect events from the middleware chain
        events = []
        try:
            # pylint: disable=protected-access
            async for evt in agent_instance._execute_tool_call(
                original_tool_call,
            ):
                events.append(evt)
        except Exception:
            # Expected to fail since tool doesn't exist, but we can still
            # verify the middleware modified the input
            pass

        # Verify the middleware modified the tool_call.input
        self.assertEqual(len(received_tool_calls), 1)
        self.assertIn("MODIFIED", received_tool_calls[0])

    async def test_on_model_call_middleware_modify_input(self) -> None:
        """Test that on_model_call middleware can modify messages and model."""

        class ModifyMessagesMiddleware(MiddlewareBase):
            """Middleware that modifies messages input."""

            async def on_model_call(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., Any],
            ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
                """Prepend a system message to the messages list."""
                messages = input_kwargs["messages"]
                modified_messages = [
                    Msg(
                        name="system",
                        content="INJECTED SYSTEM MESSAGE",
                        role="system",
                    ),
                ] + messages

                # Pass modified messages to next handler
                return await next_handler(
                    current_model=input_kwargs["current_model"],
                    messages=modified_messages,
                    tools=input_kwargs["tools"],
                    tool_choice=input_kwargs["tool_choice"],
                )

        middleware = ModifyMessagesMiddleware()

        # Track what messages the model receives
        received_messages = []

        class TrackingModel(MockModel):
            """Model that tracks received messages."""

            async def _call_api(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> ChatResponse:
                """Track the messages and return mock response."""
                messages = kwargs.get("messages", [])
                received_messages.extend(messages)
                return await super()._call_api(*args, **kwargs)

        tracking_model = TrackingModel()
        tracking_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="response")],
                    is_last=True,
                ),
            ],
        )

        agent_instance = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=tracking_model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        await agent_instance.reply(UserMsg("user", "test message"))

        # Verify the injected system message is present
        system_messages = [m for m in received_messages if m.role == "system"]
        self.assertTrue(
            any(
                "INJECTED SYSTEM MESSAGE" in m.content for m in system_messages
            ),
        )

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.execution_log.clear()
