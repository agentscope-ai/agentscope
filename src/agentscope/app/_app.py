# -*- coding: utf-8 -*-
""""""

from fastapi import FastAPI, APIRouter
from fastapi.middleware import Middleware
from pydantic import BaseModel

from agentscope.app._schema import ConnectionScope
from agentscope.app._schema._mcp import MCPBase
from agentscope.mcp import HttpMCPConfig, StdioMCPConfig
from agentscope.skill import Skill
from agentscope.storage._redis_storage import RedisStorage
from agentscope.storage.models import MCPModel


class MCPConfig(BaseModel):

    name: str

    mcp_config: HttpMCPConfig | StdioMCPConfig

    connection_scope: ConnectionScope = ConnectionScope.ISOLATED


async def create_app(
    routers: list[APIRouter],
    middlewares: list[Middleware],
    mcps: list[MCPBase],
    skills: list[Skill],
    tools_factory: list[Skill],
    storage: RedisStorage,
) -> FastAPI:
    """A factory function that creates a FastAPI application with the given
    components.
    """

    # Save the MCP configs locally if not exists
    for mcp in mcps:
        await storage.upsert_mcp(mcp)

