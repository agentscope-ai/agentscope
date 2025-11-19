# -*- coding: utf-8 -*-
"""The main entry point of the ReAct agent example."""

import asyncio
import os

from dotenv import load_dotenv
from mcp.client.auth import (
    OAuthClientProvider,
)
from mcp.shared.auth import (
    OAuthClientMetadata,
)
from pydantic import AnyUrl

from oauth_handler import (
    InMemoryTokenStorage,
    handle_callback,
    handle_redirect,
)

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.mcp import HttpStatelessClient
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit

load_dotenv()

# Fetch the MCP endpoint from https://api.aliyun.com/mcp after provisioning.
server_url = (
    "https://openapi-mcp.cn-hangzhou.aliyuncs.com/accounts/14******/custom/"
    "****/id/KXy******/mcp"
)

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
    # Name used to identify the MCP
    name="mcp_services_stateless",
    transport="streamable_http",
    url=server_url,
    auth=oauth_provider,
)


def require_env_var(name: str) -> str:
    """Return the value of *name* or raise a helpful error."""
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(f"Environment variable '{name}' must be set.")
    return value


async def main() -> None:
    """The main entry point for the ReAct agent example."""
    toolkit = Toolkit()
    await toolkit.register_mcp_client(stateless_client)

    agent = ReActAgent(
        name="AlibabaCloudOpsAgent",
        sys_prompt=(
            "You are an Alibaba Cloud operations assistant. "
            "Use ECS, RDS, VPC, and other services to satisfy requests."
        ),
        model=DashScopeChatModel(
            api_key=require_env_var("DASHSCOPE_API_KEY"),
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
