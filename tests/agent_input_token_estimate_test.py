# -*- coding: utf-8 -*-
"""Tests for usage-anchored context length estimates."""
# pylint: disable=protected-access
from copy import deepcopy
from unittest.async_case import IsolatedAsyncioTestCase

from utils import MockModel

from agentscope.agent import Agent, InjectionConfig
from agentscope.message import AssistantMsg, TextBlock, UserMsg
from agentscope.model import ChatResponse, ChatUsage
from agentscope.model._anthropic._model import AnthropicChatModel


class AgentInputTokenEstimateTest(IsolatedAsyncioTestCase):
    """Check append-only estimates and invalidation of the usage baseline."""

    async def asyncSetUp(self) -> None:
        self.model = MockModel()
        self.agent = Agent(
            name="assistant",
            system_prompt="original system",
            model=self.model,
            injection_config=InjectionConfig(inject_runtime_state=False),
        )

    async def test_actual_usage_anchors_new_user_input(self) -> None:
        """The next reply starts from actual usage, not a full estimate."""
        self.model.set_responses(
            [
                ChatResponse(
                    content=[TextBlock(text="answer")],
                    is_last=True,
                    usage=ChatUsage(
                        input_tokens=123,
                        output_tokens=8,
                        time=0.1,
                    ),
                ),
            ],
        )
        await self.agent.reply(UserMsg("user", "first"))
        current = await self.agent._prepare_model_input()
        delta = await self.model.count_tokens(current["messages"][2:], None)
        self.assertEqual(
            await self.agent._estimate_input_tokens(**current),
            123 + delta,
        )

    async def test_appended_blocks_in_existing_message(self) -> None:
        """Tool results added to the reply message count as an increment."""
        kwargs = await self.agent._prepare_model_input()
        kwargs["messages"].append(AssistantMsg("assistant", "call"))
        self.agent._input_token_baseline = (
            self.model,
            100,
            deepcopy(kwargs["messages"]),
            deepcopy(kwargs["tools"]),
        )
        kwargs["messages"][-1].content.append(TextBlock(text="tool result"))
        expected_delta = await self.model.count_tokens(
            [AssistantMsg("assistant", "tool result")],
            None,
        )
        self.assertEqual(
            await self.agent._estimate_input_tokens(**kwargs),
            100 + expected_delta,
        )

    async def test_changed_prefix_or_tools_uses_full_estimate(self) -> None:
        """Prompt and schema changes cannot reuse the old usage."""
        kwargs = await self.agent._prepare_model_input()
        self.agent._input_token_baseline = (
            self.model,
            10000,
            deepcopy(kwargs["messages"]),
            deepcopy(kwargs["tools"]),
        )
        changed_prompt = deepcopy(kwargs)
        changed_prompt["messages"] = deepcopy(kwargs["messages"])
        changed_prompt["messages"][0].content[0].text = "new memory"
        self.assertEqual(
            await self.agent._estimate_input_tokens(**changed_prompt),
            await self.model.count_tokens(**changed_prompt),
        )

        changed_tools = deepcopy(kwargs)
        changed_tools["tools"].append({"name": "new tool"})
        self.assertEqual(
            await self.agent._estimate_input_tokens(**changed_tools),
            await self.model.count_tokens(**changed_tools),
        )

    async def test_anthropic_cached_tokens_are_part_of_input(self) -> None:
        """Anthropic splits uncached, cache read and cache write tokens."""
        usage = ChatUsage(
            input_tokens=25,
            output_tokens=10,
            cache_input_tokens=60,
            cache_creation_input_tokens=15,
            time=0.1,
        )
        self.assertEqual(
            AnthropicChatModel.usage_input_tokens(None, usage),
            100,
        )
