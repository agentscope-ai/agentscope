# -*- coding: utf-8 -*-
"""Unit tests for BudgetControlMiddleware."""
from typing import Any
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel
from agentscope.agent import Agent
from agentscope.message import UserMsg, TextBlock
from agentscope.middleware import BudgetControlMiddleware
from agentscope.model import ChatResponse
from agentscope.model._model_usage import ChatUsage
from agentscope.tool import Toolkit


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

        # No hint message should have been appended to context
        hint_msgs = [
            m
            for m in agent.state.context[context_before:]
            if middleware.hint_message in (m.get_text_content() or "")
        ]
        self.assertEqual(len(hint_msgs), 0)

    async def test_budget_exceeded_injects_hint(self) -> None:
        """When the budget is exceeded, the hint message is injected.

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
            if middleware.hint_message in (m.get_text_content() or "")
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
        """Tokens from ModelCallEndEvent accumulate in _used_tokens."""
        model = MockModel()
        model.set_responses(
            [_response("answer", input_tokens=200, output_tokens=100)],
        )

        middleware = BudgetControlMiddleware(max_tokens=10000)
        agent = Agent(
            name="test_agent",
            system_prompt="you are helpful",
            model=model,
            toolkit=self.toolkit,
            middlewares=[middleware],
        )

        await agent.reply(UserMsg("user", "hello"))

        # reply_id is set at the start of _reply and holds after completion
        reply_id = agent.state.reply_id
        total = (
            middleware._used_tokens.get(  # pylint: disable=protected-access
                reply_id,
                0,
            )
        )
        self.assertEqual(total, 300)  # 200 input + 100 output
