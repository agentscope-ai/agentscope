# -*- coding: utf-8 -*-
"""
Clicks Protocol DeFi Yield Agent — AgentScope Example

Demonstrates how an AI agent can autonomously query DeFi yield data
on Base L2 using Clicks Protocol's MCP tools via AgentScope.

Usage:
    OPENAI_API_KEY=sk-xxx python main.py --model openai
    DASHSCOPE_API_KEY=xxx python main.py --model dashscope
    ANTHROPIC_API_KEY=xxx python main.py --model anthropic

No wallet or API keys needed for Clicks Protocol (read-only tools).
"""

import argparse
import asyncio
import os

from agentscope.agent import ReActAgent
from agentscope.mcp import HttpStatelessClient
from agentscope.message import Msg
from agentscope.tool import Toolkit


# Clicks Protocol MCP endpoint (public, no auth required)
CLICKS_MCP_URL = "https://clicks-mcp.rechnung-613.workers.dev/mcp"


def get_model_and_formatter(provider: str):
    """Get the model and formatter based on the provider."""

    if provider == "openai":
        from agentscope.model import OpenAIChatModel
        from agentscope.formatter import OpenAIChatFormatter

        return (
            OpenAIChatModel(
                model_name="gpt-4o",
                api_key=os.environ["OPENAI_API_KEY"],
                stream=True,
            ),
            OpenAIChatFormatter(),
        )

    elif provider == "anthropic":
        from agentscope.model import AnthropicChatModel
        from agentscope.formatter import AnthropicChatFormatter

        return (
            AnthropicChatModel(
                model_name="claude-sonnet-4-20250514",
                api_key=os.environ["ANTHROPIC_API_KEY"],
                stream=True,
            ),
            AnthropicChatFormatter(),
        )

    elif provider == "dashscope":
        from agentscope.model import DashScopeChatModel
        from agentscope.formatter import DashScopeChatFormatter

        return (
            DashScopeChatModel(
                model_name="qwen-max",
                api_key=os.environ["DASHSCOPE_API_KEY"],
                stream=True,
            ),
            DashScopeChatFormatter(),
        )

    elif provider == "ollama":
        from agentscope.model import OllamaChatModel
        from agentscope.formatter import OllamaChatFormatter

        return (
            OllamaChatModel(
                model_name="llama3.1:8b",
                stream=True,
            ),
            OllamaChatFormatter(),
        )

    else:
        raise ValueError(
            f"Unknown provider: {provider}. "
            "Supported: openai, anthropic, dashscope, ollama"
        )


async def main(provider: str) -> None:
    """Run the Clicks Protocol DeFi yield agent."""

    # 1. Create toolkit and register Clicks Protocol MCP tools
    toolkit = Toolkit()

    clicks_client = HttpStatelessClient(
        name="clicks_protocol",
        transport="streamable_http",
        url=CLICKS_MCP_URL,
    )

    await toolkit.register_mcp_client(clicks_client)

    print(f"Registered {len(toolkit)} tools from Clicks Protocol MCP")
    print("Available tools:")
    for name in toolkit.get_tool_names():
        print(f"  - {name}")
    print()

    # 2. Get model and formatter for the chosen provider
    model, formatter = get_model_and_formatter(provider)

    # 3. Create the DeFi yield agent
    agent = ReActAgent(
        name="ClicksAgent",
        sys_prompt=(
            "You are a DeFi treasury agent. You help AI agents understand "
            "and optimize their yield on Base L2 using Clicks Protocol.\n\n"
            "You have access to Clicks Protocol MCP tools that query real "
            "on-chain data from Base mainnet. Use them to:\n"
            "- Check current APY rates across Morpho Blue and Aave V3\n"
            "- Simulate how deposits would be split (80% liquid, 20% yield)\n"
            "- Query agent registration status and balances\n"
            "- Monitor referral network earnings\n\n"
            "Always provide specific numbers from tool calls. "
            "Never make up yield rates or balances."
        ),
        model=model,
        formatter=formatter,
        toolkit=toolkit,
    )

    # 4. Run example queries
    queries = [
        (
            "What are the current yield rates on Clicks Protocol? "
            "Compare Morpho and Aave."
        ),
        (
            "If an AI agent deposits 10,000 USDC into Clicks Protocol, "
            "how would it be split? How much yield would it earn per month?"
        ),
        (
            "Check the status of the Clicks Protocol referral network. "
            "How many agents are participating?"
        ),
    ]

    for i, query in enumerate(queries, 1):
        print(f"\n{'='*60}")
        print(f"Query {i}: {query}")
        print(f"{'='*60}\n")

        response = await agent(
            Msg("user", query, "user"),
        )

        print(f"\nAgent Response:\n{response.content}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Clicks Protocol DeFi Yield Agent"
    )
    parser.add_argument(
        "--model",
        type=str,
        default="openai",
        choices=["openai", "anthropic", "dashscope", "ollama"],
        help="LLM provider to use (default: openai)",
    )
    args = parser.parse_args()

    asyncio.run(main(args.model))
