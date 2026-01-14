# -*- coding: utf-8 -*-
# pylint: disable=too-many-lines
"""The main entry point of the browser agent example."""
import asyncio
import os
import sys
import argparse
import traceback
from pydantic import BaseModel, Field
from browser_agent import BrowserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit
from agentscope.mcp import StdIOStatefulClient
from agentscope.agent import UserAgent


class FinalResult(BaseModel):
    """A structured result model for structured output."""

    result: str = Field(
        description="The final result to the initial user query",
    )


async def main(
    start_url_param: str = "https://www.google.com",
    max_iters_param: int = 50,
) -> None:
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

        agent = BrowserAgent(
            name="Browser-Use Agent",
            model=DashScopeChatModel(
                api_key=os.environ.get("DASHSCOPE_API_KEY"),
                model_name="qwen-max",
                stream=False,
            ),
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
            toolkit=toolkit,
            max_iters=max_iters_param,
            start_url=start_url_param,
        )
        user = UserAgent("User")

        msg = None
        while True:
            msg = await user(msg)
            if msg.get_text_content() == "exit":
                break
            msg = await agent(msg, structured_model=FinalResult)

    except Exception as e:
        traceback.print_exc()
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


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Browser Agent Example with configurable reply method",
    )
    parser.add_argument(
        "--start-url",
        type=str,
        default="https://www.google.com",
        help=(
            "Starting URL for the browser agent "
            "(default: https://www.google.com)"
        ),
    )
    parser.add_argument(
        "--max-iters",
        type=int,
        default=50,
        help="Maximum number of iterations (default: 50)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    print("Starting Browser Agent Example...")
    print(
        "The browser agent will use "
        "playwright-mcp (https://github.com/microsoft/playwright-mcp)."
        "Make sure the MCP server is installed "
        "by `npx @playwright/mcp@latest`",
    )
    print("\nUsage examples:")
    print("  python main.py                           # Start with defaults")
    print("  python main.py --start-url https://example.com --max-iters 100")
    print("  python main.py --help                   # Show all options")
    print()

    # Parse command line arguments
    args = parse_arguments()

    # Get other parameters
    start_url = args.start_url
    max_iters = args.max_iters

    # Validate parameters
    if max_iters <= 0:
        print("Error: max-iters must be positive")
        sys.exit(1)

    if not start_url.startswith(("http://", "https://")):
        print("Error: start-url must be a valid HTTP/HTTPS URL")
        sys.exit(1)

    print(f"Starting URL: {start_url}")
    print(f"Maximum iterations: {max_iters}")

    asyncio.run(main(start_url, max_iters))
