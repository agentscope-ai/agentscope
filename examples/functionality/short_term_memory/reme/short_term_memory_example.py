# -*- coding: utf-8 -*-
"""Example demonstrating ReMeShortTermMemory usage with ReActAgent."""
# noqa: E402
import asyncio
import os

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    """Main function demonstrating ReMeShortTermMemory with tool usage."""
    from agentscope.agent import ReActAgent
    from agentscope.formatter import DashScopeChatFormatter
    from agentscope.memory import ReMeShortTermMemory
    from agentscope.message import Msg, TextBlock
    from agentscope.model import DashScopeChatModel
    from agentscope.tool import ToolResponse, Toolkit

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
        from reme_ai.retrieve.working import ReadFileOp

        op = ReadFileOp()
        await op.async_call(file_path=file_path, offset=offset, limit=limit)
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=op.output,
                ),
            ],
        )

    # These two tools are provided as examples. You can replace them with your
    # own retrieval tools, such as vector database embedding retrieval or other
    # search solutions that fit your use case.
    toolkit.register_tool_function(grep)
    toolkit.register_tool_function(read_file)

    llm = DashScopeChatModel(
        # model_name="qwen3-max",
        model_name="qwen3-coder-30b-a3b-instruct",
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
    # await short_term_memory.__aenter__()

    async with short_term_memory:
        # 模拟超长上下文
        f = open("../../../../README.md", encoding="utf-8")
        readme_content = f.read()
        f.close()

        memories = [
            {
                "role": "user",
                "content": "搜索下项目资料",
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
                "工具调用的调用可能会被缓存到本地。"
                "可以先使用`Grep`匹配关键词或者正则表达式所在行数，然后通过`ReadFile`读取位置附近的代码。"
                "如果没有找到匹配项，永远不要放弃尝试，尝试其他的参数，或者放松匹配条件，比如只搜索部分关键词。"
                "`Grep`之后通过`ReadFile`命令，你可以从指定偏移位置`offset`+长度`limit`开始查看内容, "
                "limit最大100。"
                "如果当前内容不足，`ReadFile` 命令也可以不断尝试不同的`offset`和`limit`参数"
            ),
            model=llm,
            formatter=DashScopeChatFormatter(),
            toolkit=toolkit,
            memory=short_term_memory,
            max_iters=20,
        )

        msg = Msg(
            role="user",
            content=("项目资料中，agentscope_v1论文的一作是谁？"),
            name="user",
        )
        msg = await agent(msg)
        print(f"✓ Agent response: {msg.get_text_content()}\n")

        # react_content = agent.memory.content

    # await short_term_memory.__aexit__()


if __name__ == "__main__":
    asyncio.run(main())
