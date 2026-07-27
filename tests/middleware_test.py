# -*- coding: utf-8 -*-
# pylint: disable=abstract-method
"""Unit tests for middleware system."""
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch
from typing import Any, AsyncGenerator, Callable, Union

from utils import MockModel
from pydantic import BaseModel
from agentscope.event import AgentEvent
from agentscope.agent import Agent, ContextConfig, InjectionConfig
from agentscope.middleware import MiddlewareBase
from agentscope.model import ChatResponse
from agentscope.message import (
    TextBlock,
    HintBlock,
    UserMsg,
    SystemMsg,
    Msg,
    ToolCallBlock,
)
from agentscope.tool import Toolkit, ToolBase, ToolChunk
from agentscope.permission import (
    PermissionContext,
    PermissionDecision,
    PermissionBehavior,
)


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
            # The runtime state injection is covered by
            # agent_injection_test, turn it off to keep the assertions
            # focused.
            injection_config=InjectionConfig(inject_runtime_state=False),
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
            # The final Msg is the terminal item of the reply stream,
            # emitted after REPLY_END
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
            # The reasoning generator now completes naturally instead of
            # being aborted by an early return, so the post hooks run
            "mw2_post",
            "mw1_post",
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
            # The runtime state injection is covered by agent_injection_test,
            # turn it off to keep the assertions focused.
            injection_config=InjectionConfig(inject_runtime_state=False),
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
            # The runtime state injection is covered by agent_injection_test,
            # turn it off to keep the assertions focused.
            injection_config=InjectionConfig(inject_runtime_state=False),
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
            # The reasoning generator now completes naturally instead of
            # being aborted by an early return, so its post hook runs
            "multi_reasoning_post",
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
