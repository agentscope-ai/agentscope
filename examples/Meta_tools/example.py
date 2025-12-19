# -*- coding: utf-8 -*-
"""
Example script demonstrating the use of Meta tools with AgentScope.

This module shows how to initialize and use a MetaManager to organize tools
into categories and use them with ReActAgent for conversational interactions.
"""

import asyncio
import contextlib
import json
import os

from Meta_toolkit import CategoryManager, MetaManager

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.mcp import HttpStatefulClient
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, execute_python_code, execute_shell_command

gaode_api_key = os.environ["GAODE_API_KEY"]
bing_api_key = os.environ["BING_API_KEY"]
train_api_key = os.environ["TRAIN_API_KEY"]
dashscope_api_key = os.environ["DASHSCOPE_API_KEY"]


async def main() -> None:
    """Main function to demonstrate Meta tools usage."""
    # Create global toolkit with your tools
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(execute_shell_command)

    toolkit.create_tool_group(
        "map_tools",
        "Tools related to Gaode map",
        active=True,
    )
    # If a group's 'active' flag is set to False, its tools will be registered
    # to toolkit but remain hidden

    gaode_client = HttpStatefulClient(
        name="amap-sse",
        transport="sse",
        url=f"https://mcp.amap.com/sse?key={gaode_api_key}",
        # get your own API keys from Gaode MCP servers
    )
    bing_client = HttpStatefulClient(
        name="bing-cn-mcp-server",
        transport="sse",
        url=f"https://mcp.api-inference.modelscope.net/{bing_api_key}/sse",
        # get your own API keys from Modelscope's Bing MCP servers
    )
    train_client = HttpStatefulClient(
        name="12306-mcp",
        transport="sse",
        url=f"https://mcp.api-inference.modelscope.net/{train_api_key}/sse",
        # get your own API keys from Modelscope's train 12306 MCP servers
    )
    try:
        await gaode_client.connect()
        await toolkit.register_mcp_client(
            gaode_client,
            group_name="map_tools",
        )
        print("Gaode MCP client connected successfully")

        await bing_client.connect()
        await toolkit.register_mcp_client(bing_client)
        print("Bing MCP client connected successfully")

        await train_client.connect()
        await toolkit.register_mcp_client(train_client)
        print("12306 MCP client connected successfully")

        # Meta tool initialization with Pydantic validation
        current_dir = os.path.dirname(os.path.realpath(__file__))
        meta_tool_config_path = os.path.join(current_dir, "Meta_tool_config.json")

        model = DashScopeChatModel(
            model_name="qwen-max",
            api_key=dashscope_api_key,
            stream=True,
        )

        # Load and validate configuration
        try:
            from meta_config_models import MetaToolConfig
            # Load and validate with Pydantic
            meta_tool_config = MetaToolConfig.from_json_file(meta_tool_config_path)
        except Exception as e:
            print(f"Error: {e}")
            # Fallback: load as plain dict
            with open(meta_tool_config_path, "r", encoding="utf-8") as f:
                meta_tool_config = json.load(f)
        meta_manager = MetaManager(
            model=model,
            meta_tool_config=meta_tool_config,
            global_toolkit=toolkit,
            formatter=DashScopeChatFormatter(),
            memory=InMemoryMemory(),
        )

        # Use meta_manager directly as toolkit for ReActAgent
        agent = ReActAgent(
            name="Friday",
            sys_prompt="You're a helpful assistant named Friday.",
            model=DashScopeChatModel(
                model_name="qwen-max",
                api_key=dashscope_api_key,
                stream=True,
            ),
            memory=InMemoryMemory(),
            formatter=DashScopeChatFormatter(),
            toolkit=meta_manager,  # Direct replacement for traditional toolkit
        )

        user = UserAgent(name="user")

        msg = None
        try:
            while True:
                msg = await agent(msg)
                msg = await user(msg)
                if msg.get_text_content() == "exit":
                    break
        except Exception as e:
            print(f"Error: {e}")

        return

    finally:
        with contextlib.suppress(Exception):
            await train_client.close()
        with contextlib.suppress(Exception):
            await bing_client.close()
        with contextlib.suppress(Exception):
            await gaode_client.close()


if __name__ == "__main__":
    asyncio.run(main())
