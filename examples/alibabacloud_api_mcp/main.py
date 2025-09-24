# -*- coding: utf-8 -*-
"""The main entry point of the ReAct agent example."""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import (
    Toolkit,
    execute_shell_command,
    execute_python_code,
    view_text_file,
)
from agentscope.mcp import HttpStatelessClient

from mcp.client.auth import OAuthClientProvider, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl
from oauth_handler import InMemoryTokenStorage, handle_redirect, handle_callback


# openai base   
# read from .env
load_dotenv()

# 请从https://api.aliyun.com/mcp 创建后获取连接地址
server_url = "https://openapi-mcp.cn-hangzhou.aliyuncs.com/accounts/14******/custom/****/id/KXy******/mcp"

memory_token_storage = InMemoryTokenStorage()

oauth_provider = OAuthClientProvider(
    server_url=server_url,
    client_metadata=OAuthClientMetadata(
        client_name="AgentScopeExampleClient",
        redirect_uris=[AnyUrl("http://localhost:3000/callback")],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=None,
    ),
    storage=memory_token_storage,
    redirect_handler=handle_redirect,
    callback_handler=handle_callback,
)

stateless_client = HttpStatelessClient(
    # 用于标识 MCP 的名称
    name="mcp_services_stateless",
    transport="streamable_http",
    url=server_url,
    auth=oauth_provider,
)

async def main() -> None:
    """The main entry point for the ReAct agent example."""
    toolkit = Toolkit()
    # toolkit.register_tool_function(execute_shell_command)
    # toolkit.register_tool_function(execute_python_code)
    # toolkit.register_tool_function(view_text_file)
    await toolkit.register_mcp_client(stateless_client)

    agent = ReActAgent(
        name="AlibabaCloudOpsAgent",
        sys_prompt="你是阿里云运维助手，善于使用各种阿里云产品如 ECS、RDS、VPC 等，完成我的需求。",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name="qwen3-max-preview",
            enable_thinking=False,
            stream=True,
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )
    user = UserAgent("User")

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break
        msg = await agent(msg)


asyncio.run(main())
