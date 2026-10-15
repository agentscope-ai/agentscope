# -*- coding: utf-8 -*-
"""Run repeated context compression with explicit pins, entirely offline."""
import asyncio
import json
from typing import Any

from agentscope.agent import Agent, ContextConfig, PinnedContextRetentionPolicy
from agentscope.credential import CredentialBase
from agentscope.formatter import OpenAIChatFormatter
from agentscope.message import Msg, TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatModelBase, ChatResponse
from agentscope.tool import ToolChoice


class OfflineSummaryModel(ChatModelBase):
    """Supply a fixed summary to demonstrate retention without a model API.

    This fixture verifies context bookkeeping, not summary quality. Token
    estimates use the existing ChatModelBase implementation.
    """

    def __init__(self) -> None:
        """Create a small local model window to trigger compression."""
        super().__init__(
            credential=CredentialBase(name="offline-demo"),
            model="offline-summary",
            parameters=self.Parameters(),
            stream=False,
            max_retries=0,
            context_size=4096,
        )
        self.formatter = OpenAIChatFormatter()
        self.summary_count = 0

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """Return the structured summary locally; no network is contacted."""
        if any(
            tool["function"]["name"] == "generate_structured_output"
            for tool in tools or []
        ):
            self.summary_count += 1
            return ChatResponse(
                content=[
                    ToolCallBlock(
                        id=f"summary-{self.summary_count}",
                        name="generate_structured_output",
                        input=json.dumps(
                            {
                                "task_overview": "Check context retention.",
                                "current_state": "Older details summarized.",
                                "important_discoveries": "None.",
                                "next_steps": "Continue with recent context.",
                                "context_to_preserve": "See pinned message.",
                            },
                        ),
                    ),
                ],
                is_last=True,
            )
        return ChatResponse(
            content=[TextBlock(text="Offline demo complete.")],
            is_last=True,
        )


async def main() -> None:
    """Retain a constraint twice, then demonstrate a rejected oversized pin."""
    model = OfflineSummaryModel()
    agent = Agent(
        name="retention-demo",
        system_prompt="Complete the task while respecting user constraints.",
        model=model,
        context_config=ContextConfig(trigger_ratio=0.8, reserve_ratio=0.3),
        context_retention_policy=PinnedContextRetentionPolicy(
            max_pinned_tokens=64,
        ),
    )
    constraint = UserMsg(
        "user",
        "Keep the public API backward compatible.",
        id="api-constraint",
        metadata={"context_retention": True},
    )
    await agent.observe(constraint)

    for round_index in range(2):
        await agent.observe(
            [
                UserMsg(
                    "user",
                    "Historical working detail: " + "x" * 1200,
                    id=f"round-{round_index}-detail-{index}",
                )
                for index in range(11 if round_index == 0 else 8)
            ],
        )
        await agent.compress_context()
        pinned = [
            msg for msg in agent.state.context if msg.id == constraint.id
        ]
        assert pinned == [constraint], "The exact constraint must survive."
        assert agent.state.summary, "The old messages must be summarized."
        print(
            f"Round {round_index + 1}: pinned text preserved; retained IDs = "
            f"{[msg.id for msg in agent.state.context]}",
        )

    assert model.summary_count == 2
    await agent.observe(
        [
            UserMsg(
                "user",
                "An oversized pinned message. " * 200,
                metadata={"context_retention": True},
            ),
            UserMsg("user", "Unpinned history: " + "y" * 8000),
        ],
    )
    before = agent.state.model_copy(deep=True)
    try:
        await agent.compress_context()
    except ValueError as error:
        assert agent.state == before, "Overflow must preserve the state."
        assert model.summary_count == 2, "Overflow must precede summarization."
        print(f"Overflow rejected: {error}")
        print("State and summary unchanged after the rejected compression.")
    else:
        raise AssertionError("An oversized pin must raise ValueError.")


if __name__ == "__main__":
    asyncio.run(main())
