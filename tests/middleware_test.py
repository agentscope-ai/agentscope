# -*- coding: utf-8 -*-
# pylint: disable=abstract-method
"""Unit tests for middleware system."""
import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from typing import Any, AsyncGenerator, Callable, Union

from utils import MockModel
from pydantic import BaseModel
from agentscope.event import (
    AgentEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    ConfirmResult,
)
from agentscope.agent import Agent, ContextConfig
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.message import (
    TextBlock,
    HintBlock,
    UserMsg,
    AssistantMsg,
    SystemMsg,
    Msg,
    ToolCallBlock,
    ToolCallState,
    ToolResultState,
)
from agentscope.state import AgentState
from agentscope.tool import Toolkit, ToolBase, ToolChunk, Bash
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
    PermissionMode,
    PermissionRule,
    PermissionResolution,
)


class TestMiddleware(IsolatedAsyncioTestCase):
    """Test cases for middleware system."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.mock_model = MockModel()
        self.toolkit = Toolkit()
        self.execution_log = []

    def test_permission_confirmation_hook_is_optional(self) -> None:
        """Expose an optional confirmation notification contract."""
        assert hasattr(MiddlewareBase, "on_permission_confirmation")
        assert (
            MiddlewareBase().is_implemented("on_permission_confirmation")
            is False
        )

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
            "mw2_msg",
            "mw1_msg",
            "mw2_REPLY_END",
            "mw1_REPLY_END",
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
            "mw2_post",
            "mw1_post",
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
                """Modify inputs before passing to next handler."""
                # Modify the message content
                inputs = input_kwargs["inputs"]
                if isinstance(inputs, Msg):
                    modified_msg = UserMsg(
                        name=inputs.name,
                        content="MODIFIED: " + inputs.get_text_content(),
                    )
                    async for item in next_handler(inputs=modified_msg):
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
        self.assertIn(
            "MODIFIED: original message",
            user_messages[-1].get_text_content(),
        )

    async def test_on_reply_keeps_outer_input_when_inner_omits_kwargs(
        self,
    ) -> None:
        """Argumentless next_handler() keeps outer reply input changes."""

        class ModifyInputMiddleware(MiddlewareBase):
            """Middleware that replaces the reply input."""

            async def on_reply(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                inputs = input_kwargs["inputs"]
                modified = UserMsg(
                    name=inputs.name,
                    content="MODIFIED by outer middleware",
                )
                async for item in next_handler(inputs=modified):
                    yield item

        class TransparentMiddleware(MiddlewareBase):
            """Middleware that calls next_handler() without passing kwargs."""

            async def on_reply(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                assert (
                    "MODIFIED by outer middleware"
                    in input_kwargs["inputs"].get_text_content()
                )
                async for item in next_handler():
                    yield item

        received_messages = []

        class TrackingModel(MockModel):
            """Model that tracks received messages."""

            async def _call_api(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> ChatResponse:
                received_messages.extend(kwargs.get("messages", []))
                return await super()._call_api(*args, **kwargs)

        tracking_model = TrackingModel()
        tracking_model.set_responses(
            [ChatResponse(content=[TextBlock(text="response")], is_last=True)],
        )

        agent_instance = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=tracking_model,
            toolkit=self.toolkit,
            middlewares=[
                ModifyInputMiddleware(),
                TransparentMiddleware(),
            ],
        )

        await agent_instance.reply(UserMsg("user", "original message"))

        user_messages = [m for m in received_messages if m.role == "user"]
        self.assertIn(
            "MODIFIED by outer middleware",
            user_messages[-1].get_text_content(),
        )

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

    async def test_on_acting_middleware_intercepts_tool_execution(
        self,
    ) -> None:
        """Test that on_acting middleware intercepts raw tool execution.

        After the refactor, ``on_acting`` wraps only ``_acting_impl``
        (i.e. ``toolkit.call_tool``).  Permission checking and context
        writes are handled by ``_execute_tool_call`` *outside* the hook.
        This test verifies that the middleware can observe and modify the
        ``tool_call`` passed to the actual tool function.
        """

        # ------------------------------------------------------------------ #
        # A minimal tool that records the raw input it receives.              #
        # ------------------------------------------------------------------ #
        received_inputs: list[str] = []

        class _EchoParams(BaseModel):
            value: str

        class EchoTool(ToolBase):
            """Tool that echoes its input and records it."""

            name: str = "echo"
            description: str = "Echo the value."
            input_schema: dict = _EchoParams.model_json_schema()
            is_concurrency_safe: bool = True
            is_read_only: bool = True
            is_state_injected: bool = False
            is_external_tool: bool = False
            is_mcp: bool = False
            mcp_name: str | None = None

            async def check_permissions(
                self,
                tool_input: dict[str, Any],
                context: PermissionContext,
            ) -> PermissionDecision:
                """Always allow."""
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="allowed",
                )

            async def __call__(
                self,
                value: str,
            ) -> ToolChunk:
                """Record the value and return it."""
                received_inputs.append(value)
                return ToolChunk(
                    content=[TextBlock(text=f"echo:{value}")],
                )

        toolkit_with_tool = Toolkit(tools=[EchoTool()])

        # ------------------------------------------------------------------ #
        # Middleware that renames the tool_call.input before forwarding.      #
        # ------------------------------------------------------------------ #
        intercepted_tool_calls: list[str] = []

        class ObserveActingMiddleware(MiddlewareBase):
            """Middleware that records the tool_call seen at acting level."""

            async def on_acting(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., AsyncGenerator],
            ) -> AsyncGenerator:
                """Record the tool_call and forward to next handler."""
                tool_call = input_kwargs["tool_call"]
                intercepted_tool_calls.append(tool_call.input)
                # Modify the input before execution
                import json

                modified = ToolCallBlock(
                    id=tool_call.id,
                    name=tool_call.name,
                    input=json.dumps({"value": "MODIFIED"}),
                    state=tool_call.state,
                )
                async for chunk in next_handler(tool_call=modified):
                    yield chunk

        middleware = ObserveActingMiddleware()
        self.mock_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="done")],
                    is_last=True,
                ),
            ],
        )

        agent_instance = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=toolkit_with_tool,
            middlewares=[middleware],
        )

        # Call _execute_tool_call with a valid tool call.
        tool_call = ToolCallBlock(
            id="call_1",
            name="echo",
            input='{"value": "ORIGINAL"}',
        )
        events = []
        # pylint: disable=protected-access
        async for evt in agent_instance._execute_tool_call(tool_call):
            events.append(evt)

        # Middleware intercepted the tool call at execution level
        self.assertEqual(len(intercepted_tool_calls), 1)
        self.assertIn("ORIGINAL", intercepted_tool_calls[0])

        # The tool actually received the MODIFIED value
        self.assertEqual(len(received_inputs), 1)
        self.assertEqual(received_inputs[0], "MODIFIED")

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
                    SystemMsg(
                        name="system",
                        content="INJECTED SYSTEM MESSAGE",
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
                "INJECTED SYSTEM MESSAGE" in m.get_text_content()
                for m in system_messages
            ),
        )

    async def test_on_model_call_keeps_outer_messages_when_inner_omits_kwargs(
        self,
    ) -> None:
        """Transparent inner model-call middleware keeps outer messages."""

        class ModifyMessagesMiddleware(MiddlewareBase):
            """Middleware that changes the messages."""

            async def on_model_call(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., Any],
            ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
                modified_messages = [
                    SystemMsg(
                        name="system",
                        content="INJECTED SYSTEM MESSAGE",
                    ),
                ] + input_kwargs["messages"]
                return await next_handler(messages=modified_messages)

        class TransparentMiddleware(MiddlewareBase):
            """Middleware that calls next_handler() without passing kwargs."""

            async def on_model_call(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., Any],
            ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
                system_messages = [
                    m for m in input_kwargs["messages"] if m.role == "system"
                ]
                assert any(
                    "INJECTED SYSTEM MESSAGE" in m.get_text_content()
                    for m in system_messages
                )
                return await next_handler()

        received_messages = []

        class TrackingModel(MockModel):
            """Model that tracks received messages."""

            async def _call_api(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> ChatResponse:
                received_messages.extend(kwargs.get("messages", []))
                return await super()._call_api(*args, **kwargs)

        tracking_model = TrackingModel()
        tracking_model.set_responses(
            [ChatResponse(content=[TextBlock(text="response")], is_last=True)],
        )

        agent_instance = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=tracking_model,
            toolkit=self.toolkit,
            middlewares=[
                ModifyMessagesMiddleware(),
                TransparentMiddleware(),
            ],
        )

        await agent_instance.reply(UserMsg("user", "test message"))

        system_messages = [m for m in received_messages if m.role == "system"]
        self.assertTrue(
            any(
                "INJECTED SYSTEM MESSAGE" in m.get_text_content()
                for m in system_messages
            ),
        )

    async def test_on_model_call_keeps_outer_messages_with_partial_kwargs(
        self,
    ) -> None:
        """Partial next_handler kwargs keep outer model-call changes."""
        injected_text = "INJECTED SYSTEM MESSAGE FROM OUTER MIDDLEWARE"
        inner_saw_injected_message = False

        class ModifyMessagesMiddleware(MiddlewareBase):
            """Middleware that changes the messages."""

            async def on_model_call(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., Any],
            ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
                modified_messages = [
                    SystemMsg(
                        name="system",
                        content=injected_text,
                    ),
                ] + input_kwargs["messages"]
                return await next_handler(messages=modified_messages)

        class PartialForwardMiddleware(MiddlewareBase):
            """Middleware that forwards only one of the current kwargs."""

            async def on_model_call(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., Any],
            ) -> Union[ChatResponse, AsyncGenerator[ChatResponse, None]]:
                nonlocal inner_saw_injected_message
                inner_saw_injected_message = any(
                    injected_text in m.get_text_content()
                    for m in input_kwargs["messages"]
                )
                return await next_handler(
                    tool_choice=input_kwargs["tool_choice"],
                )

        received_messages = []

        class TrackingModel(MockModel):
            """Model that tracks received messages."""

            async def _call_api(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> ChatResponse:
                received_messages.extend(kwargs.get("messages", []))
                return await super()._call_api(*args, **kwargs)

        tracking_model = TrackingModel()
        tracking_model.set_responses(
            [ChatResponse(content=[TextBlock(text="response")], is_last=True)],
        )

        agent_instance = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=tracking_model,
            toolkit=self.toolkit,
            middlewares=[
                ModifyMessagesMiddleware(),
                PartialForwardMiddleware(),
            ],
        )

        await agent_instance.reply(UserMsg("user", "test message"))

        self.assertTrue(inner_saw_injected_message)
        self.assertTrue(
            any(
                injected_text in m.get_text_content()
                for m in received_messages
            ),
        )

    async def test_on_compress_context_middleware(self) -> None:
        """Test on_compress_context middleware follows the onion chain pattern.

        Verifies that:
        - Multiple middlewares are chained in onion order (mw1 wraps mw2).
        - ``input_kwargs`` carries the correct ``context_config`` and
          ``instructions``.
        - The ``next_handler`` ultimately calls ``_compress_context_impl``.
        - A middleware can short-circuit and skip the actual implementation.
        """
        seen_instructions = []

        # ------------------------------------------------------------------ #
        # Middleware that records pre/post and forwards to next_handler.      #
        # ------------------------------------------------------------------ #
        class CompressContextMiddleware(MiddlewareBase):
            """Middleware for testing on_compress_context hook."""

            def __init__(self, log: list, name: str) -> None:
                """Initialize the compress context middleware.

                Args:
                    log (`list`):
                        The execution log list.
                    name (`str`):
                        The middleware name.
                """
                self.log = log
                self.name = name

            async def on_compress_context(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., Any],
            ) -> None:
                """Forward to next handler, recording pre and post."""
                self.log.append(f"{self.name}_pre")
                seen_instructions.append(input_kwargs.get("instructions"))
                await next_handler(**input_kwargs)
                self.log.append(f"{self.name}_post")

        middleware1 = CompressContextMiddleware(self.execution_log, "mw1")
        middleware2 = CompressContextMiddleware(self.execution_log, "mw2")

        self.mock_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="test response")],
                    is_last=True,
                ),
            ],
        )

        context_config = ContextConfig(trigger_ratio=0.8, reserve_ratio=0.1)
        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[middleware1, middleware2],
            context_config=context_config,
        )

        # Patch _compress_context_impl to avoid real token counting.
        instructions = HintBlock(
            hint="Keep user requirements while compressing.",
            source="user",
        )
        with patch.object(
            agent,
            "_compress_context_impl",
            new_callable=AsyncMock,
        ) as mock_impl:
            await agent.compress_context(
                context_config=context_config,
                instructions=instructions,
            )

            # _compress_context_impl must have been called exactly once.
            mock_impl.assert_awaited_once_with(
                context_config=context_config,
                instructions=instructions,
            )

        # Verify onion execution order: mw1_pre -> mw2_pre -> mw2_post ->
        # mw1_post
        expected = ["mw1_pre", "mw2_pre", "mw2_post", "mw1_post"]
        self.assertListEqual(self.execution_log, expected)
        self.assertListEqual(seen_instructions, [instructions, instructions])

    async def test_on_compress_context_middleware_modify_instructions(
        self,
    ) -> None:
        """Test that middleware can replace compress_context instructions."""

        class ReplaceInstructionsMiddleware(MiddlewareBase):
            """Middleware that replaces the compression instructions."""

            def __init__(self, replacement: HintBlock) -> None:
                """Initialize the middleware with replacement instructions."""
                self.replacement = replacement

            async def on_compress_context(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., Any],
            ) -> None:
                """Replace instructions before forwarding."""
                input_kwargs["instructions"] = self.replacement
                await next_handler(**input_kwargs)

        original = HintBlock(
            hint="Keep all requirements.",
            source="user",
        )
        replacement = HintBlock(
            hint="Keep only unresolved requirements.",
            source="middleware",
        )
        context_config = ContextConfig(trigger_ratio=0.8, reserve_ratio=0.1)
        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            middlewares=[ReplaceInstructionsMiddleware(replacement)],
            context_config=context_config,
        )

        with patch.object(
            agent,
            "_compress_context_impl",
            new_callable=AsyncMock,
        ) as mock_impl:
            await agent.compress_context(
                context_config=context_config,
                instructions=original,
            )

            mock_impl.assert_awaited_once_with(
                context_config=context_config,
                instructions=replacement,
            )

    async def test_on_compress_context_middleware_short_circuit(
        self,
    ) -> None:
        """Test that a middleware can skip _compress_context_impl entirely."""

        class SkipCompressMiddleware(MiddlewareBase):
            """Middleware that skips the actual compress_context call."""

            def __init__(self, log: list) -> None:
                """Initialize the skip compress middleware.

                Args:
                    log (`list`):
                        The execution log list.
                """
                self.log = log

            async def on_compress_context(
                self,
                agent: Agent,
                input_kwargs: dict,
                next_handler: Callable[..., Any],
            ) -> None:
                """Record the call and skip forwarding to next_handler."""
                self.log.append("skipped")
                # Intentionally NOT calling next_handler.

        middleware = SkipCompressMiddleware(self.execution_log)

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

        with patch.object(
            agent,
            "_compress_context_impl",
            new_callable=AsyncMock,
        ) as mock_impl:
            await agent.compress_context()

            # _compress_context_impl should NOT have been called.
            mock_impl.assert_not_awaited()

        self.assertListEqual(self.execution_log, ["skipped"])

    async def test_on_compress_context_no_middleware(self) -> None:
        """Test that compress_context calls _compress_context_impl directly
        when no middleware is registered."""

        self.mock_model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="test response")],
                    is_last=True,
                ),
            ],
        )

        context_config = ContextConfig(trigger_ratio=0.8, reserve_ratio=0.1)
        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=self.mock_model,
            toolkit=self.toolkit,
            # No middlewares registered.
            context_config=context_config,
        )

        with patch.object(
            agent,
            "_compress_context_impl",
            new_callable=AsyncMock,
        ) as mock_impl:
            await agent.compress_context(context_config=context_config)
            mock_impl.assert_awaited_once_with(
                context_config=context_config,
                instructions=None,
            )

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.execution_log.clear()


# ---------------------------------------------------------------------------
# on_permission_decision integration tests
# ---------------------------------------------------------------------------


def _build_agent_with_bash(
    middlewares: list,
    mode: PermissionMode = PermissionMode.DEFAULT,
    deny_rules: list[PermissionRule] | None = None,
    allow_rules: list[PermissionRule] | None = None,
    ask_rules: list[PermissionRule] | None = None,
    tools: list[ToolBase] | None = None,
) -> Agent:
    """Build an Agent with a Bash tool and a configurable permission mode.

    Uses MockModel; the caller sets responses on the returned agent's
    model via ``agent.model.set_responses(...)``. Pass ``tools`` to
    register different tools (rule dicts still key on ``"Bash"``).
    """
    deny_rules = deny_rules or []
    allow_rules = allow_rules or []
    ask_rules = ask_rules or []
    context = PermissionContext(
        mode=mode,
        deny_rules={"Bash": deny_rules} if deny_rules else {},
        allow_rules={"Bash": allow_rules} if allow_rules else {},
        ask_rules={"Bash": ask_rules} if ask_rules else {},
    )
    state = AgentState(permission_context=context)
    model = MockModel(context_size=10000)
    agent = Agent(
        name="test_agent",
        system_prompt="test prompt",
        model=model,
        toolkit=Toolkit(tools=tools if tools is not None else [Bash()]),
        middlewares=middlewares,
        state=state,
    )
    return agent


class _ExternalAllowTool(ToolBase):
    """Read-only external tool that always allows execution."""

    name = "external_allow"
    description = "External tool used by permission observer tests."
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    is_concurrency_safe = False
    is_read_only = True
    is_external_tool = True

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Allow the external invocation."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message="external tool allowed",
        )


class _SafetyAskTool(ToolBase):
    """External tool that emits a bypass-immune safety ASK.

    Used to exercise BYPASS's ASK suppression without executing a real
    destructive command (e.g. ``rm -rf /``). Marked external so an ALLOW
    reaches ``RequireExternalExecutionEvent`` and never local execution,
    keeping the test side-effect-free across environments.
    """

    name = "safety_ask_demo"
    description = "Demo tool emitting a bypass-immune safety ASK."
    input_schema = {
        "type": "object",
        "properties": {"label": {"type": "string"}},
        "required": ["label"],
    }
    is_concurrency_safe = False
    is_read_only = False
    is_external_tool = True

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Emit a bypass-immune safety ASK."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Safety check: destructive operation",
            decision_reason="Safety check: bypass-immune ASK (demo)",
            bypass_immune=True,
        )


class _ConfirmationAskTool(ToolBase):
    """Side-effect-free tool that always requests confirmation."""

    name = "confirmation_ask"
    description = "Request confirmation without external side effects."
    input_schema = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    is_concurrency_safe = False
    is_read_only = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Request user confirmation."""
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Confirm the demonstration operation",
            decision_reason="Demonstration tool requires confirmation",
        )

    async def __call__(self, value: str) -> ToolChunk:
        """Return a side-effect-free result."""
        return ToolChunk(content=[TextBlock(text=f"confirmed: {value}")])


class PermissionDecisionObserverTest(IsolatedAsyncioTestCase):
    """on_permission_decision fires once per tool call, before on_acting."""

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_allow_fires_once_before_acting(self) -> None:
        """ALLOW is observed exactly once before local execution."""
        records: list[str] = []

        class Recorder(MiddlewareBase):
            """Record permission and acting hook order."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                records.append(
                    f"decision:{evaluation.effective_decision.behavior.value}",
                )

            async def on_acting(
                self,
                agent,
                input_kwargs,
                next_handler,
            ):
                records.append("acting:before")
                async for item in next_handler():
                    yield item
                records.append("acting:after")

        agent = _build_agent_with_bash(
            middlewares=[Recorder()],
            mode=PermissionMode.DEFAULT,
            allow_rules=[
                PermissionRule(
                    tool_name="Bash",
                    rule_content="ls",
                    behavior=PermissionBehavior.ALLOW,
                    source="test",
                ),
            ],
        )
        tool_call = ToolCallBlock(
            id="call_1",
            name="Bash",
            input='{"command": "ls"}',
        )
        agent.model.set_responses(
            [
                ChatResponse(content=[tool_call], is_last=False),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        async for _ in agent.reply_stream(UserMsg("user", "run ls")):
            pass

        assert "decision:allow" in records
        assert records.index("decision:allow") < records.index("acting:before")
        assert records.count("decision:allow") == 1

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_deny_fires_without_acting(self) -> None:
        """DENY is observed without invoking the acting hook."""
        records: list[str] = []

        class Recorder(MiddlewareBase):
            """Record permission and acting hook calls."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                records.append(
                    f"decision:{evaluation.effective_decision.behavior.value}",
                )

            async def on_acting(self, agent, input_kwargs, next_handler):
                records.append("acting")
                async for item in next_handler():
                    yield item

        agent = _build_agent_with_bash(
            middlewares=[Recorder()],
            mode=PermissionMode.DEFAULT,
            deny_rules=[
                PermissionRule(
                    tool_name="Bash",
                    rule_content="*",
                    behavior=PermissionBehavior.DENY,
                    source="test",
                ),
            ],
        )
        tool_call = ToolCallBlock(
            id="call_1",
            name="Bash",
            input='{"command": "echo hi"}',
        )
        agent.model.set_responses(
            [
                ChatResponse(content=[tool_call], is_last=False),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        async for _ in agent.reply_stream(UserMsg("user", "run echo")):
            pass

        assert "decision:deny" in records
        assert "acting" not in records

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_observer_cannot_mutate_effective_decision(self) -> None:
        """A read-only observer cannot alter the consumed permission result.

        Scenario:
            A deny rule produces DENY. The observer attempts to mutate
            ``evaluation.effective_decision.behavior`` to ALLOW inside
            the hook.

        Expected:
            The agent still treats the call as DENY — ``on_acting`` is
            never invoked. The observer received a deep copy, so its
            mutation never reached the agent's decision.
        """
        records: list[str] = []

        class MutatingObserver(MiddlewareBase):
            """Attempt to escalate DENY to ALLOW."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                evaluation.effective_decision.behavior = (
                    PermissionBehavior.ALLOW
                )
                records.append("observed")

            async def on_acting(self, agent, input_kwargs, next_handler):
                records.append("acting")
                async for item in next_handler():
                    yield item

        agent = _build_agent_with_bash(
            middlewares=[MutatingObserver()],
            mode=PermissionMode.DEFAULT,
            deny_rules=[
                PermissionRule(
                    tool_name="Bash",
                    rule_content="*",
                    behavior=PermissionBehavior.DENY,
                    source="test",
                ),
            ],
        )
        tool_call = ToolCallBlock(
            id="call_1",
            name="Bash",
            input='{"command": "echo hi"}',
        )
        agent.model.set_responses(
            [
                ChatResponse(content=[tool_call], is_last=False),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        async for _ in agent.reply_stream(UserMsg("user", "run echo hi")):
            pass

        assert "observed" in records
        assert (
            "acting" not in records
        )  # DENY held — mutation did not propagate

    async def test_observer_cannot_mutate_tool_call(self) -> None:
        """A read-only observer cannot alter the executed tool call.

        Scenario:
            An ALLOW tool call proceeds to execution. The observer
            attempts to rewrite ``tool_call.input`` to a tampered value
            inside the hook.

        Expected:
            The tool receives the original input. The observer received
            a deep copy of ``tool_call``, so its mutation never reached
            the agent's execution path.
        """
        received: list[str] = []

        class RecordingTool(ToolBase):
            """Local tool that records the value it executes with."""

            name = "recording"
            description = "Record the executed input value."
            input_schema = {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            }
            is_concurrency_safe = True
            is_read_only = True
            is_external_tool = False

            async def check_permissions(
                self,
                tool_input: dict[str, Any],
                context: PermissionContext,
            ) -> PermissionDecision:
                return PermissionDecision(
                    behavior=PermissionBehavior.ALLOW,
                    message="allow",
                )

            async def __call__(self, value: str) -> ToolChunk:
                received.append(value)
                return ToolChunk(content=[TextBlock(text=f"got:{value}")])

        class MutatingObserver(MiddlewareBase):
            """Attempt to rewrite the tool call input."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                tool_call.input = '{"value": "TAMPERED"}'

        agent = _build_agent_with_bash(
            middlewares=[MutatingObserver()],
            mode=PermissionMode.DEFAULT,
            tools=[RecordingTool()],
        )
        tool_call = ToolCallBlock(
            id="call_1",
            name="recording",
            input='{"value": "ORIGINAL"}',
        )
        agent.model.set_responses(
            [
                ChatResponse(content=[tool_call], is_last=False),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        async for _ in agent.reply_stream(UserMsg("user", "run recording")):
            pass

        assert received == ["ORIGINAL"]  # tool ran with original input

    async def test_ask_fires_without_acting(self) -> None:
        """ASK is observed once and never reaches tool execution."""
        records: list[str] = []

        class Recorder(MiddlewareBase):
            """Record decision and acting hook calls."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                records.append(
                    f"decision:{evaluation.effective_decision.behavior.value}",
                )

            async def on_acting(self, agent, input_kwargs, next_handler):
                records.append("acting")
                async for item in next_handler():
                    yield item

        agent = _build_agent_with_bash(
            middlewares=[Recorder()],
            tools=[_SafetyAskTool()],
        )
        agent.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_ask",
                            name="safety_ask_demo",
                            input='{"label": "destructive op"}',
                        ),
                    ],
                    is_last=False,
                ),
            ],
        )

        events = [
            event
            async for event in agent.reply_stream(
                UserMsg("user", "run dangerous command"),
            )
        ]

        assert records == ["decision:ask"]
        assert "acting" not in records
        assert any(
            isinstance(event, RequireUserConfirmEvent) for event in events
        )

    async def test_external_allow_fires_once(self) -> None:
        """An allowed external tool is observed before handoff."""
        evaluations: list = []

        class Recorder(MiddlewareBase):
            """Record permission evaluations."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                evaluations.append(evaluation)

        model = MockModel(context_size=10000)
        agent = Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=model,
            toolkit=Toolkit(tools=[_ExternalAllowTool()]),
            middlewares=[Recorder()],
            state=AgentState(
                permission_context=PermissionContext(
                    mode=PermissionMode.DEFAULT,
                ),
            ),
        )
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_external",
                            name="external_allow",
                            input='{"value": "hello"}',
                        ),
                    ],
                    is_last=False,
                ),
            ],
        )

        events = [
            event
            async for event in agent.reply_stream(
                UserMsg("user", "run external"),
            )
        ]

        assert len(evaluations) == 1
        assert (
            evaluations[0].effective_decision.behavior
            == PermissionBehavior.ALLOW
        )
        assert any(
            isinstance(event, RequireExternalExecutionEvent)
            for event in events
        )

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_multiple_tool_calls_each_fire_once(self) -> None:
        """Every tool call in one model response gets one notification."""
        observed_ids: list[str] = []

        class Recorder(MiddlewareBase):
            """Record observed tool-call identifiers."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                observed_ids.append(tool_call.id)

        agent = _build_agent_with_bash(middlewares=[Recorder()])
        agent.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call_ls",
                            name="Bash",
                            input='{"command": "ls"}',
                        ),
                        ToolCallBlock(
                            id="call_pwd",
                            name="Bash",
                            input='{"command": "pwd"}',
                        ),
                    ],
                    is_last=False,
                ),
                ChatResponse(
                    content=[TextBlock(text="done")],
                    is_last=True,
                ),
            ],
        )

        async for _ in agent.reply_stream(UserMsg("user", "inspect")):
            pass

        assert sorted(observed_ids) == ["call_ls", "call_pwd"]
        assert len(observed_ids) == 2

    async def test_bypass_suppressed_ask_recorded(self) -> None:
        """Deliver a BYPASS-suppressed safety ASK to middleware.

        Scenario:
            An agent in BYPASS invokes a tool whose ``check_permissions``
            emits a bypass-immune safety ASK. BYPASS suppresses it into
            ALLOW. A demo tool is used instead of a real destructive
            command so the test is side-effect-free on every platform.

        Expected evaluation:
            The hook receives candidate=ASK(bypass_immune=True),
            effective=ALLOW, and resolution=BYPASS_ASK_SUPPRESSED.

        Audit significance:
            An application-level audit sink can record the suppressed warning
            before the operation reaches tool execution.
        """
        records: list = []

        class Recorder(MiddlewareBase):
            """Record permission evaluations."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                records.append(evaluation)

        agent = _build_agent_with_bash(
            middlewares=[Recorder()],
            mode=PermissionMode.BYPASS,
            tools=[_SafetyAskTool()],
        )
        tool_call = ToolCallBlock(
            id="call_1",
            name="safety_ask_demo",
            input='{"label": "destructive op"}',
        )
        agent.model.set_responses(
            [
                ChatResponse(content=[tool_call], is_last=False),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        async for _ in agent.reply_stream(
            UserMsg("user", "run the destructive demo"),
        ):
            pass

        assert len(records) == 1
        ev = records[0]
        assert ev.effective_decision.behavior == PermissionBehavior.ALLOW
        assert ev.resolution == PermissionResolution.BYPASS_ASK_SUPPRESSED
        assert ev.candidate_decision is not None
        assert ev.candidate_decision.behavior == PermissionBehavior.ASK
        assert ev.candidate_decision.bypass_immune is True

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_no_observer_no_behavior_change(self) -> None:
        """A middleware without the new hook preserves existing behavior."""

        # An agent with a middleware that does NOT implement
        # on_permission_decision behaves exactly like one with no
        # middleware for permission purposes.
        class PlainActingRecorder(MiddlewareBase):
            """Record whether tool execution occurred."""

            def __init__(self) -> None:
                self.acted = False

            async def on_acting(self, agent, input_kwargs, next_handler):
                self.acted = True
                async for item in next_handler():
                    yield item

        rec = PlainActingRecorder()
        agent = _build_agent_with_bash(
            middlewares=[rec],
            mode=PermissionMode.DEFAULT,
            allow_rules=[
                PermissionRule(
                    tool_name="Bash",
                    rule_content="ls",
                    behavior=PermissionBehavior.ALLOW,
                    source="test",
                ),
            ],
        )
        tool_call = ToolCallBlock(
            id="call_1",
            name="Bash",
            input='{"command": "ls"}',
        )
        agent.model.set_responses(
            [
                ChatResponse(content=[tool_call], is_last=False),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        async for _ in agent.reply_stream(UserMsg("user", "run ls")):
            pass
        assert rec.acted is True

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_observer_exception_aborts_tool_call(self) -> None:
        """Observer exceptions propagate before tool execution."""

        class FailingObserver(MiddlewareBase):
            """Raise from the permission notification hook."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                raise RuntimeError("observer failed")

        agent = _build_agent_with_bash(
            middlewares=[FailingObserver()],
            mode=PermissionMode.DEFAULT,
            allow_rules=[
                PermissionRule(
                    tool_name="Bash",
                    rule_content="ls",
                    behavior=PermissionBehavior.ALLOW,
                    source="test",
                ),
            ],
        )
        tool_call = ToolCallBlock(
            id="call_1",
            name="Bash",
            input='{"command": "ls"}',
        )
        agent.model.set_responses(
            [
                ChatResponse(content=[tool_call], is_last=False),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        with self.assertRaises(RuntimeError):
            _ = [
                e async for e in agent.reply_stream(UserMsg("user", "run ls"))
            ]

    async def test_user_confirmed_reuse_recorded(self) -> None:
        """Deliver reused user authorization as USER_CONFIRMED.

        Scenario:
            A user confirms a tool call whose ``check_permissions`` emits
            a bypass-immune safety ASK, and Agent resumes the same call
            without evaluating it through PermissionEngine again. A demo
            tool is used instead of a real destructive command so the
            test is side-effect-free on every platform.

        Expected evaluation:
            The resumed call emits effective=ALLOW, candidate=None, and
            resolution=USER_CONFIRMED.

        Audit significance:
            The execution is attributable to explicit user authorization
            rather than appearing to have bypassed permission checking.
        """
        records: list = []

        class Recorder(MiddlewareBase):
            """Record permission evaluations."""

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                records.append(evaluation)

        # DEFAULT mode. The demo tool emits a bypass-immune safety ASK on
        # the first reply; accepting the suggested rules lets the second
        # reply reuse authorization (USER_CONFIRMED resolution).
        agent = _build_agent_with_bash(
            middlewares=[Recorder()],
            mode=PermissionMode.DEFAULT,
            tools=[_SafetyAskTool()],
        )
        tool_call = ToolCallBlock(
            id="call_1",
            name="safety_ask_demo",
            input='{"label": "destructive op"}',
        )
        agent.model.set_responses(
            [
                ChatResponse(content=[tool_call], is_last=False),
                ChatResponse(content=[TextBlock(text="done")], is_last=True),
            ],
        )
        first_events = [
            e
            async for e in agent.reply_stream(
                UserMsg("user", "run the destructive demo"),
            )
        ]

        # First reply: safety ASK observed, recorded as ASK (DIRECT).
        assert len(records) == 1
        assert records[0].effective_decision.behavior == PermissionBehavior.ASK
        assert records[0].resolution == PermissionResolution.DIRECT

        # Locate the RequireUserConfirmEvent and accept its suggested rules.
        confirm_event = next(
            e for e in first_events if isinstance(e, RequireUserConfirmEvent)
        )
        confirmed_tool_call = confirm_event.tool_calls[0]
        accepted_rules = list(confirmed_tool_call.suggested_rules or [])

        # Second reply: feed the UserConfirmResultEvent accepting the rules.
        agent.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="all done")], is_last=True
                ),
            ],
        )
        async for _ in agent.reply_stream(
            UserConfirmResultEvent(
                reply_id=agent.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=confirmed_tool_call,
                        rules=accepted_rules,
                    ),
                ],
            ),
        ):
            pass

        # Second reply recorded the reused authorization as USER_CONFIRMED.
        assert any(
            r.resolution == PermissionResolution.USER_CONFIRMED
            for r in records
        ), f"expected USER_CONFIRMED in {records}"
        user_confirmed = next(
            r
            for r in records
            if r.resolution == PermissionResolution.USER_CONFIRMED
        )
        assert (
            user_confirmed.effective_decision.behavior
            == PermissionBehavior.ALLOW
        )
        assert user_confirmed.candidate_decision is None


class PermissionConfirmationObserverTest(IsolatedAsyncioTestCase):
    """Observe user approval and rejection of pending permission requests."""

    @staticmethod
    def _build_agent(middlewares: list[MiddlewareBase]) -> Agent:
        """Build an agent with the deterministic confirmation tool."""
        return Agent(
            name="test_agent",
            system_prompt="test prompt",
            model=MockModel(context_size=10000),
            toolkit=Toolkit(tools=[_ConfirmationAskTool()]),
            middlewares=middlewares,
            state=AgentState(
                permission_context=PermissionContext(
                    mode=PermissionMode.DEFAULT,
                ),
            ),
        )

    @staticmethod
    async def _request_confirmation(agent: Agent) -> ToolCallBlock:
        """Run the first turn and return its pending tool call."""
        agent.model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="confirm_call",
                            name=_ConfirmationAskTool.name,
                            input='{"value": "demo"}',
                        ),
                    ],
                    is_last=False,
                ),
            ],
        )
        events = [
            event
            async for event in agent.reply_stream(
                UserMsg("user", "run confirmation demo"),
            )
        ]
        confirmation_event = next(
            event
            for event in events
            if isinstance(event, RequireUserConfirmEvent)
        )
        return confirmation_event.tool_calls[0]

    async def test_approval_notifies_before_rules_and_execution(self) -> None:
        """Observe approval before Agent applies it.

        Scenario:
            A user approves a pending tool call and submits a reusable allow
            rule for the same tool.

        Expected observation:
            The hook receives confirmed=True, the submitted rule, and the
            tool call before the rule is present in PermissionContext. The
            resumed call later emits USER_CONFIRMED and executes.

        Audit significance:
            Audit consumers can attribute both the immediate execution and
            the new reusable authorization to the user's explicit choice.
        """
        confirmations: list[tuple[bool, list[PermissionRule], bool]] = []
        decisions: list[PermissionResolution] = []
        acting: list[str] = []

        class Recorder(MiddlewareBase):
            """Record confirmation ordering and resumed execution."""

            async def on_permission_confirmation(
                self,
                agent,
                tool_call,
                confirmed,
                rules,
            ) -> None:
                rule_already_applied = bool(
                    agent.state.permission_context.allow_rules.get(
                        tool_call.name,
                    ),
                )
                confirmations.append(
                    (confirmed, list(rules), rule_already_applied),
                )

            async def on_permission_decision(
                self,
                agent,
                tool_call,
                tool,
                tool_input,
                evaluation,
            ) -> None:
                decisions.append(evaluation.resolution)

            async def on_acting(self, agent, input_kwargs, next_handler):
                acting.append(input_kwargs["tool_call"].id)
                async for item in next_handler():
                    yield item

        agent = self._build_agent([Recorder()])
        pending_call = await self._request_confirmation(agent)
        accepted_rule = PermissionRule(
            tool_name=_ConfirmationAskTool.name,
            rule_content=None,
            behavior=PermissionBehavior.ALLOW,
            source="user",
        )
        agent.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="done")],
                    is_last=True,
                ),
            ],
        )

        async for _ in agent.reply_stream(
            UserConfirmResultEvent(
                reply_id=agent.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=pending_call,
                        rules=[accepted_rule],
                    ),
                ],
            ),
        ):
            pass

        assert confirmations == [(True, [accepted_rule], False)]
        assert (
            accepted_rule
            in agent.state.permission_context.allow_rules[
                _ConfirmationAskTool.name
            ]
        )
        assert PermissionResolution.USER_CONFIRMED in decisions
        assert acting == ["confirm_call"]

    async def test_observer_cannot_mutate_confirmation_rules(self) -> None:
        """A read-only observer cannot alter the confirmed rules/tool call.

        Scenario:
            A user approves a pending tool call and submits a reusable
            allow rule. The observer attempts to clear the rules list
            and rewrite ``tool_call.input`` inside the hook.

        Expected:
            The agent still applies the original rule via ``add_rule``.
            The observer received deep copies, so its mutations never
            reached the agent's confirmation consumption.
        """

        class MutatingObserver(MiddlewareBase):
            """Attempt to strip rules and rewrite the tool call."""

            async def on_permission_confirmation(
                self,
                agent,
                tool_call,
                confirmed,
                rules,
            ) -> None:
                rules.clear()
                tool_call.input = '{"value": "TAMPERED"}'

        agent = self._build_agent([MutatingObserver()])
        pending_call = await self._request_confirmation(agent)
        accepted_rule = PermissionRule(
            tool_name=_ConfirmationAskTool.name,
            rule_content=None,
            behavior=PermissionBehavior.ALLOW,
            source="user",
        )
        agent.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="done")],
                    is_last=True,
                ),
            ],
        )

        async for _ in agent.reply_stream(
            UserConfirmResultEvent(
                reply_id=agent.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=True,
                        tool_call=pending_call,
                        rules=[accepted_rule],
                    ),
                ],
            ),
        ):
            pass

        assert (
            accepted_rule
            in agent.state.permission_context.allow_rules[
                _ConfirmationAskTool.name
            ]
        )

    async def test_rejection_notifies_without_execution(self) -> None:
        """Observe explicit rejection without executing the tool.

        Scenario:
            A user rejects a pending confirmation request.

        Expected observation:
            The hook receives confirmed=False exactly once and Agent emits a
            denied tool result without entering ``on_acting``.

        Audit significance:
            Consumers can distinguish explicit rejection from an unanswered,
            interrupted, timed-out, or automatically denied ASK.
        """
        confirmations: list[tuple[str, bool]] = []
        acting: list[str] = []

        class Recorder(MiddlewareBase):
            """Record rejection and any attempted execution."""

            async def on_permission_confirmation(
                self,
                agent,
                tool_call,
                confirmed,
                rules,
            ) -> None:
                confirmations.append((tool_call.id, confirmed))

            async def on_acting(self, agent, input_kwargs, next_handler):
                acting.append(input_kwargs["tool_call"].id)
                async for item in next_handler():
                    yield item

        agent = self._build_agent([Recorder()])
        pending_call = await self._request_confirmation(agent)
        agent.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="denial acknowledged")],
                    is_last=True,
                ),
            ],
        )

        events = [
            event
            async for event in agent.reply_stream(
                UserConfirmResultEvent(
                    reply_id=agent.state.reply_id,
                    confirm_results=[
                        ConfirmResult(
                            confirmed=False,
                            tool_call=pending_call,
                            rules=[],
                        ),
                    ],
                ),
            )
        ]

        assert confirmations == [("confirm_call", False)]
        assert not acting
        assert any(
            getattr(event, "state", None) == ToolResultState.DENIED
            for event in events
        )

    async def test_observer_exception_prevents_approval_consumption(
        self,
    ) -> None:
        """Fail closed before an approval changes state.

        Scenario:
            A required audit sink fails while processing user approval.

        Expected observation:
            The exception propagates while the pending call remains ASKING;
            no rule is applied and the tool is not executed.

        Audit significance:
            Required-audit deployments never execute an approved operation
            whose confirmation record could not be persisted.
        """
        acting: list[str] = []

        class FailingObserver(MiddlewareBase):
            """Fail confirmation recording and detect attempted execution."""

            async def on_permission_confirmation(
                self,
                agent,
                tool_call,
                confirmed,
                rules,
            ) -> None:
                raise RuntimeError("confirmation audit failed")

            async def on_acting(self, agent, input_kwargs, next_handler):
                acting.append(input_kwargs["tool_call"].id)
                async for item in next_handler():
                    yield item

        agent = self._build_agent([FailingObserver()])
        pending_call = await self._request_confirmation(agent)
        accepted_rule = PermissionRule(
            tool_name=_ConfirmationAskTool.name,
            rule_content=None,
            behavior=PermissionBehavior.ALLOW,
            source="user",
        )

        with self.assertRaisesRegex(
            RuntimeError,
            "confirmation audit failed",
        ):
            async for _ in agent.reply_stream(
                UserConfirmResultEvent(
                    reply_id=agent.state.reply_id,
                    confirm_results=[
                        ConfirmResult(
                            confirmed=True,
                            tool_call=pending_call,
                            rules=[accepted_rule],
                        ),
                    ],
                ),
            ):
                pass

        assert pending_call.state == ToolCallState.ASKING
        assert (
            _ConfirmationAskTool.name
            not in agent.state.permission_context.allow_rules
        )
        assert not acting

    async def test_multiple_results_each_notify_once(self) -> None:
        """Notify once for every confirmation result in one event.

        Scenario:
            A user rejects two pending tool calls in a single confirmation
            response.

        Expected observation:
            The hook receives each tool-call ID exactly once with
            confirmed=False.

        Audit significance:
            Batched UI responses cannot silently omit or duplicate audit
            records for individual permission requests.
        """
        observed: list[tuple[str, bool]] = []

        class Recorder(MiddlewareBase):
            """Record each consumed confirmation result."""

            async def on_permission_confirmation(
                self,
                agent,
                tool_call,
                confirmed,
                rules,
            ) -> None:
                observed.append((tool_call.id, confirmed))

        first = ToolCallBlock(
            id="confirm_1",
            name=_ConfirmationAskTool.name,
            input='{"value": "one"}',
            state=ToolCallState.ASKING,
        )
        second = ToolCallBlock(
            id="confirm_2",
            name=_ConfirmationAskTool.name,
            input='{"value": "two"}',
            state=ToolCallState.ASKING,
        )
        agent = self._build_agent([Recorder()])
        agent.state.context = [
            AssistantMsg(
                name=agent.name,
                content=[first, second],
            ),
        ]

        # Exercise the confirmation-consumption boundary directly.
        # pylint: disable-next=protected-access
        async for _ in agent._handle_incoming_event(
            UserConfirmResultEvent(
                reply_id=agent.state.reply_id,
                confirm_results=[
                    ConfirmResult(
                        confirmed=False,
                        tool_call=first,
                        rules=[],
                    ),
                    ConfirmResult(
                        confirmed=False,
                        tool_call=second,
                        rules=[],
                    ),
                ],
            ),
        ):
            pass

        assert sorted(observed) == [
            ("confirm_1", False),
            ("confirm_2", False),
        ]
