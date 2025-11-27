# -*- coding: utf-8 -*-
"""Example demonstrating ReMeShortTermMemory usage with ReActAgent."""
# noqa: E402
import asyncio
import os

from dotenv import load_dotenv

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg, TextBlock
from agentscope.model import DashScopeChatModel
from agentscope.tool import ToolResponse, Toolkit, view_text_file

load_dotenv()


async def main() -> None:
    """Main function demonstrating ReMeShortTermMemory with tool usage."""
    from reme_short_term_memory import ReMeShortTermMemory

    toolkit = Toolkit()

    async def grep(file_path: str, pattern: str, limit: str) -> ToolResponse:
        """A powerful search tool for finding patterns in files using regular
        expressions.

        Supports full regex syntax (e.g., "log.*Error", "function\\s+\\w+"),
        glob pattern filtering, and result limiting. Ideal for searching code
        or text content across multiple files.

        Args:
            file_path (`str`):
                The path to the file to search in. Can be an absolute or
                relative path.
            pattern (`str`):
                The search pattern or regular expression to match. Supports
                full regex syntax for complex pattern matching.
            limit (`str`):
                The maximum number of matching results to return. Use this to
                control output size for large files. Should not exceed 50.
        """
        from reme_ai.retrieve.working import GrepOp

        op = GrepOp()
        await op.async_call(file_path=file_path, pattern=pattern, limit=limit)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=op.output,
                ),
            ],
        )

    async def read_file(
        file_path: str,
        offset: int,
        limit: int,
    ) -> ToolResponse:
        """Reads and returns the content of a specified file.

        For text files, it can read specific line ranges using the 'offset' and
        'limit' parameters. Use offset and limit to paginate through large
        files.

        Note: It's recommended to use the `grep` tool first to locate the line
        numbers of interest before calling this function.

        Args:
            file_path (`str`):
                The path to the file to read. Can be an absolute or relative
                path.
            offset (`int`):
                The starting line number to read from (0-indexed). Use this to
                skip to a specific position in the file.
            limit (`int`):
                The maximum number of lines to read from the offset position.
                Helps control memory usage when reading large files. Should
                not exceed 100.
        """

        return await view_text_file(file_path, ranges=[offset, offset + limit])

    # These two tools are provided as examples. You can replace them with your
    # own retrieval tools, such as vector database embedding retrieval or other
    # search solutions that fit your use case.
    toolkit.register_tool_function(grep)
    toolkit.register_tool_function(read_file)

    llm = DashScopeChatModel(
        model_name="qwen3-max",
        # model_name="qwen3-coder-30b-a3b-instruct",
        api_key=os.environ.get("DASHSCOPE_API_KEY"),
        stream=False,
        generate_kwargs={
            "temperature": 0.001,
            "seed": 0,
        },
    )
    short_term_memory = ReMeShortTermMemory(
        model=llm,
        working_summary_mode="auto",
        compact_ratio_threshold=0.75,
        max_total_tokens=20000,
        max_tool_message_tokens=2000,
        group_token_threshold=None,  # Max tokens per compression batch
        keep_recent_count=1,  # Set to 1 for demo; use 10 in production
        store_dir="inmemory",
    )

    async with short_term_memory:
        # Simulate ultra long context
        f = open("../../../../README.md", encoding="utf-8")
        readme_content = f.read()
        f.close()

        memories = [
            {
                "role": "user",
                "content": "Search for project information",
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_6596dafa2a6a46f7a217da",
                        "function": {
                            "arguments": "{}",
                            "name": "web_search",
                        },
                        "type": "function",
                    },
                ],
            },
            {
                "role": "tool",
                "content": readme_content * 10,
                "tool_call_id": "call_6596dafa2a6a46f7a217da",
            },
        ]
        await short_term_memory.add(
            ReMeShortTermMemory.list_to_msg(memories),
            allow_duplicates=True,
        )

        agent = ReActAgent(
            name="react",
            sys_prompt=(
                "You are a helpful assistant. "
                "Tool calls may be cached locally. "
                "You can first use `Grep` to match keywords or regular "
                "expressions to find line numbers, then use `ReadFile` "
                "to read the code near that location. "
                "If no matches are found, never give up trying - try "
                "other parameters or relax the matching conditions, such "
                "as searching for only partial keywords. "
                "After `Grep`, you can use the `ReadFile` command to "
                "view content starting from a specified offset position "
                "`offset` with length `limit`. "
                "The maximum limit is 100. "
                "If the current content is insufficient, the `ReadFile` "
                "command can continuously try different `offset` and "
                "`limit` parameters."
            ),
            model=llm,
            formatter=DashScopeChatFormatter(),
            toolkit=toolkit,
            memory=short_term_memory,
            max_iters=20,
        )

        msg = Msg(
            role="user",
            content=(
                "In the project documentation, who is the first author "
                "of the agentscope_v1 paper?"
            ),
            name="user",
        )
        msg = await agent(msg)
        print(f"✓ Agent response: {msg.get_text_content()}\n")


if __name__ == "__main__":
    asyncio.run(main())
