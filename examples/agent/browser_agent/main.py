# -*- coding: utf-8 -*-
"""The main entry point of the browser agent example."""
import asyncio
import os

from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit
from agentscope.mcp import StdIOStatefulClient
from agentscope.agent import UserAgent

from browser_agent import BrowserAgent  # pylint: disable=C0411


async def main() -> None:
    """The main entry point for the browser agent example."""
    # Setup toolkit with browser tools from MCP server
    toolkit = Toolkit()
    browser_client = StdIOStatefulClient(
        name="playwright-mcp",
        command="npx",
        args=["@playwright/mcp@latest"],
    )

    try:
        # Connect to the browser client
        await browser_client.connect()
        await toolkit.register_mcp_client(browser_client)

        # Create browser agent
        agent = BrowserAgent(
            name="BrowserBot",
            model=DashScopeChatModel(
                api_key=os.environ.get("DASHSCOPE_API_KEY"),
                model_name="qwen-max",
                stream=True,
            ),
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=toolkit,
            max_iters=50,
            start_url="https://www.google.com",
        )
        user = UserAgent("Bob")

        msg = None
        while True:
            msg = await user(msg)
            if msg.get_text_content() == "exit":
                break
            msg = await agent(msg)

    except Exception as e:
        print(f"An error occurred: {e}")
        print("Cleaning up browser client...")
    finally:
        # Ensure browser client is always closed,
        # regardless of success or failure
        try:
            await browser_client.close()
            print("Browser client closed successfully.")
        except Exception as cleanup_error:
            print(f"Error while closing browser client: {cleanup_error}")


if __name__ == "__main__":
    print("Starting Browser Agent Example...")
    print(
        "The browser agent will use "
        "playwright-mcp (https://github.com/microsoft/playwright-mcp)."
        "Make sure the MCP server is can be install "
        "by `npx @playwright/mcp@latest`",
    )

    asyncio.run(main())
