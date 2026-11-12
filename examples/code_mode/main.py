# -*- coding: utf-8 -*-
"""Run Code Mode with DashScope using DASHSCOPE_API_KEY."""

import asyncio
import json
import os
import tempfile

from agentscope.agent import Agent, InjectionConfig
from agentscope.code_mode import CodeModeToolkit
from agentscope.credential import DashScopeCredential
from agentscope.message import (
    TextBlock,
    ToolCallBlock,
    ToolResultBlock,
    UserMsg,
)
from agentscope.model import DashScopeChatModel
from agentscope.tool import FunctionTool, LocalBackend, ToolChunk, Toolkit


async def fetch_sales(region: str) -> ToolChunk:
    """Fetch all sample sales rows for the north or south region.

    Inside run_code, call await tools.fetch_sales(region="north") or
    await tools.fetch_sales(region="south"). The bare name fetch_sales is
    not defined in the Python execution scope.

    The awaited result has content, metadata, and state fields. Read rows
    from result["metadata"]["rows"]; each row has a numeric "amount" field.
    The content text is only a loading message, not the sales data.
    Sum the amounts and count the rows in the same script as the fetch.

    Args:
        region (`str`):
            The region to fetch: "north" or "south".

    Returns:
        `ToolChunk`:
            A loading message and sales rows in metadata.
    """
    amounts = {"north": [10, 20], "south": [30, 40]}[region]
    return ToolChunk(
        content=[TextBlock(text=f"Loaded sales for {region}")],
        metadata={"rows": [{"amount": amount} for amount in amounts]},
    )


async def main() -> None:
    """Let a real model generate Python that calls the sample sales tool."""
    api_key = os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set the DASHSCOPE_API_KEY environment variable before "
            "running this demo.",
        )

    with tempfile.TemporaryDirectory(prefix="agentscope-code-") as directory:
        catalog = Toolkit(tools=[FunctionTool(fetch_sales, is_read_only=True)])
        agent = Agent(
            name="sales",
            system_prompt=(
                "Use run_code to fetch sales with the available tools, "
                "calculate totals in Python, and return the selected results. "
                "Use the provided tool contracts to fetch each region once "
                "and aggregate both regions in the same run_code call; "
                "variables do not persist between calls. Use asyncio.gather "
                "for independent region queries. Emit the summary once with "
                "text(summary), without also printing it or leaving it as "
                "the final expression. If a query fails, retry only the "
                "failed query when its successful peers are still available. "
                "Report the requested results concisely. Include "
                "implementation details only when the user asks for them "
                "or they are necessary to explain an error or limitation."
            ),
            model=DashScopeChatModel(
                credential=DashScopeCredential(api_key=api_key),
                model="qwen3.8-flash",
                stream=False,
            ),
            toolkit=CodeModeToolkit(
                catalog,
                backend=LocalBackend(),
                cwd=directory,
                # Explicitly authorize this demo's generated Python scripts.
                auto_approve=True,
            ),
            injection_config=InjectionConfig(inject_runtime_state=False),
        )
        result = await agent.reply(
            UserMsg(
                name="user",
                content=(
                    "Calculate the total sales and record count for the "
                    "north and south regions."
                ),
            ),
        )
        for message in agent.state.context:
            for block in message.content:
                if (
                    isinstance(block, ToolCallBlock)
                    and block.name == "run_code"
                ):
                    try:
                        arguments = json.loads(block.input)
                    except json.JSONDecodeError:
                        arguments = None
                    if isinstance(arguments, dict) and isinstance(
                        arguments.get("code"),
                        str,
                    ):
                        print("\nGenerated Python:")
                        print(arguments["code"])
                    else:
                        print("\nRaw run_code arguments:")
                        print(block.input)
                elif (
                    isinstance(block, ToolResultBlock)
                    and block.name == "run_code"
                ):
                    print("\nNative tool calls:")
                    calls = block.metadata.get("code_mode", {}).get(
                        "calls",
                        [],
                    )
                    print(json.dumps(calls, ensure_ascii=False, indent=2))
        print(result.get_text_content())


if __name__ == "__main__":
    asyncio.run(main())
