# -*- coding: utf-8 -*-
"""Unit tests for BudgetControlMiddleware."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel
from agentscope.agent import Agent
from agentscope.message import UserMsg, TextBlock, ToolCallBlock, HintBlock
from agentscope.middleware import BudgetControlMiddleware
from agentscope.model import ChatResponse
from agentscope.model._model_usage import ChatUsage
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from agentscope.tool import ToolBase, Toolkit, ToolChunk


def _response(
    text: str,
    input_tokens: int,
    output_tokens: int,
) -> ChatResponse:
    """Build a non-streaming ChatResponse with usage."""
    return ChatResponse(
        content=[TextBlock(text=text)],
        is_last=True,
        usage=ChatUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            time=0.0,
        ),
    )


class DummyTool(ToolBase):
    """Minimal tool that always allows and returns a fixed result."""

    name: str = "dummy"
    description: str = "A dummy tool for testing"
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}
    is_concurrency_safe: bool = True
    is_read_only: bool = True
    is_external_tool: bool = False
    is_mcp: bool = False

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            decision_reason="Dummy tool always allows",
            message="Dummy tool always allows",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """Return a fixed result."""
        return ToolChunk(content=[TextBlock(text="ok")])


def _has_hint_block(msg: Any, hint_message: str) -> bool:
    """Return True if *msg* contains a HintBlock with *hint_message*."""
    content = getattr(msg, "content", None)
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, HintBlock) and hint_message in b.hint for b in content
    )


class TestBudgetControlMiddleware(IsolatedAsyncioTestCase):
    """Test cases for BudgetControlMiddleware."""

    async def asyncSetUp(self) -> None:
        """Set up shared fixtures."""
        self.toolkit = Toolkit()

    async def test_under_budget_no_hint_injected(self) -> None:
        """When token usage stays below the budget, no hint is injected."""
        model = MockModel()
        model.set_responses(
            [_response("done", input_tokens=10, output_tokens=5)],
        )

        middleware = BudgetControlMiddleware(max_tokens=1000)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        context_before = len(agent.state.context)
        await agent.reply(UserMsg("user", "hello"))

        # No HintBlock should have been added to context
        hint_msgs = [
            m
            for m in agent.state.context[context_before:]
            if _has_hint_block(m, middleware.hint_message)
        ]
        self.assertEqual(len(hint_msgs), 0)

    async def test_budget_exceeded_injects_hint(self) -> None:
        """When the budget is exceeded, the hint block is injected.

        Uses max_tokens=0 so the budget condition fires on the very first
        reasoning call (0 used >= 0 max).
        """
        model = MockModel()
        model.set_responses(
            [_response("wrap up", input_tokens=10, output_tokens=5)],
        )

        middleware = BudgetControlMiddleware(max_tokens=0)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        context_before = len(agent.state.context)
        await agent.reply(UserMsg("user", "hello"))

        hint_msgs = [
            m
            for m in agent.state.context[context_before:]
            if _has_hint_block(m, middleware.hint_message)
        ]
        self.assertGreater(len(hint_msgs), 0)

    async def test_budget_exceeded_forces_tool_choice_none(self) -> None:
        """When budget is exceeded, tool_choice forwarded to model is none.

        Uses max_tokens=0 so the override fires on the first reasoning call.
        """
        received_tool_choices: list = []

        class TrackingModel(MockModel):
            """Model that records tool_choice on every call."""

            async def _call_api(
                self,
                *args: Any,
                **kwargs: Any,
            ) -> ChatResponse:
                """Record tool_choice and delegate to mock."""
                received_tool_choices.append(kwargs.get("tool_choice"))
                return await super()._call_api(*args, **kwargs)

        model = TrackingModel()
        model.set_responses(
            [_response("wrap up", input_tokens=10, output_tokens=5)],
        )

        middleware = BudgetControlMiddleware(max_tokens=0)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        await agent.reply(UserMsg("user", "hello"))

        # At least one call must have received tool_choice with mode="none"
        self.assertTrue(
            any(
                getattr(tc, "mode", None) == "none"
                for tc in received_tool_choices
                if tc is not None
            ),
        )

    async def test_token_accumulation_across_steps(self) -> None:
        """Tokens accumulate across steps and trigger enforcement correctly.

        Step 1: tool call costs 200+100=300 tokens (max_tokens=300 so
        step 2 sees used >= max and injects the hint).
        """
        toolkit = Toolkit(tools=[DummyTool()])

        model = MockModel()
        model.set_responses(
            [
                # Step 1: tool call with token usage
                [
                    ChatResponse(
                        content=[
                            ToolCallBlock(
                                id="tc_1",
                                name="dummy",
                                input="{}",
                            ),
                        ],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=200,
                            output_tokens=100,
                            time=0.0,
                        ),
                    ),
                ],
                # Step 2: final text answer (budget enforced before this call)
                [
                    ChatResponse(
                        content=[TextBlock(text="done")],
                        is_last=True,
                        usage=ChatUsage(
                            input_tokens=150,
                            output_tokens=50,
                            time=0.0,
                        ),
                    ),
                ],
            ],
        )

        # max_tokens=300: step 1 uses exactly 300, so step 2 triggers
        middleware = BudgetControlMiddleware(max_tokens=300)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=toolkit,
            middlewares=[middleware],
        )

        context_before = len(agent.state.context)
        await agent.reply(UserMsg("user", "hello"))

        # After step 1 used 300 tokens the budget was hit; verify hint
        # was injected before step 2 (proves accumulation worked)
        hint_msgs = [
            m
            for m in agent.state.context[context_before:]
            if _has_hint_block(m, middleware.hint_message)
        ]
        self.assertGreater(len(hint_msgs), 0)
