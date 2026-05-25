# -*- coding: utf-8 -*-
"""The example script to start the agent service."""
import os

import uvicorn
from fastapi.middleware import Middleware
from fastapi.middleware.cors import CORSMiddleware

from agentscope.app import (
    create_app,
    RedisStorage,
    LocalWorkspaceManager,
)
from agentscope.mcp import MCPClient, StdioMCPConfig, HttpMCPConfig

app = create_app(
    RedisStorage(),
    workspace_manager=LocalWorkspaceManager(
        basedir=os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "workspaces",
        ),
        default_mcps=[
            MCPClient(
                name="browser-use",
                mcp_config=StdioMCPConfig(
                    command="npx",
                    args=["@playwright/mcp@latest"],
                ),
                is_stateful=True,
            ),
            MCPClient(
                name="amap",
                mcp_config=HttpMCPConfig(
                    url=f"https://mcp.amap.com/mcp?key="
                    f"{os.environ['GAODE_API_KEY']}",
                ),
                is_stateful=False,
            ),
        ],
    ),
    extra_middlewares=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ],
)

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
