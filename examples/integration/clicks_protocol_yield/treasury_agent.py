# -*- coding: utf-8 -*-
"""
Treasury Management Agent — Advanced Example

A multi-step agent that acts as an autonomous treasury manager:
1. Checks current yield rates across protocols
2. Simulates optimal deposit allocation
3. Monitors existing agent positions
4. Generates a treasury report

This demonstrates how an AgentScope ReAct agent can chain
multiple Clicks Protocol MCP tool calls into a coherent workflow.

Usage:
    OPENAI_API_KEY=sk-xxx python treasury_agent.py
"""

import asyncio
import os
from datetime import datetime

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import OpenAIChatFormatter
from agentscope.mcp import HttpStatelessClient
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit


CLICKS_MCP_URL = "https://clicks-mcp.rechnung-613.workers.dev/mcp"


class TreasuryReport(BaseModel):
    """Structured output for the treasury report."""

    timestamp: str = Field(description="ISO timestamp of the report")
    active_protocol: str = Field(
        description="Currently active yield protocol (Morpho/Aave)"
    )
    current_apy_percent: float = Field(
        description="Current APY in percent"
    )
    recommended_action: str = Field(
        description="What the agent should do next"
    )
    monthly_yield_per_10k: float = Field(
        description="Estimated monthly yield for 10,000 USDC deposit"
    )
    risk_assessment: str = Field(
        description="Brief risk assessment (low/medium/high)"
    )


async def main() -> None:
    """Run the treasury management agent."""

    # Setup
    toolkit = Toolkit()

    clicks = HttpStatelessClient(
        name="clicks",
        transport="streamable_http",
        url=CLICKS_MCP_URL,
    )
    await toolkit.register_mcp_client(clicks)

    model = OpenAIChatModel(
        model_name="gpt-4o",
        api_key=os.environ["OPENAI_API_KEY"],
        stream=True,
    )

    # Create agent with memory so it can reference prior tool results
    agent = ReActAgent(
        name="TreasuryManager",
        sys_prompt=(
            "You are an autonomous treasury manager for AI agents. "
            "Your job is to analyze DeFi yield opportunities on Base L2 "
            "and produce a structured treasury report.\n\n"
            "Steps:\n"
            "1. Call clicks_get_yield_info to check current APY rates\n"
            "2. Call clicks_simulate_split with amount=10000 to see "
            "   how a $10K deposit would be allocated\n"
            "3. Call clicks_get_referral_stats to check network activity\n"
            "4. Synthesize findings into a treasury report\n\n"
            "Base your analysis only on tool call results. "
            "Include specific numbers. No speculation."
        ),
        model=model,
        formatter=OpenAIChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    print("Treasury Management Agent initialized")
    print(f"Connected to Clicks Protocol MCP at {CLICKS_MCP_URL}")
    print(f"Tools available: {toolkit.get_tool_names()}")
    print(f"\nGenerating treasury report...\n{'='*60}\n")

    # Run the multi-step analysis
    response = await agent(
        Msg(
            "user",
            f"Generate a complete treasury report as of "
            f"{datetime.utcnow().isoformat()}Z. "
            f"Check all yield rates, simulate a 10,000 USDC deposit, "
            f"and assess the referral network. "
            f"Then provide your structured recommendation.",
            "user",
        ),
        structured_model=TreasuryReport,
    )

    print(f"Agent narrative:\n{response.content}\n")

    if response.metadata:
        print(f"{'='*60}")
        print("Structured Treasury Report:")
        print(f"{'='*60}")
        for key, value in response.metadata.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
