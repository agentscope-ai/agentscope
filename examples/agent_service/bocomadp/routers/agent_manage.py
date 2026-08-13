# -*- coding: utf-8 -*-
"""Multi-agent management router — Agent CRUD API.

GET    /api/agents            — list all agents
POST   /api/agents            — create a new agent config
GET    /api/agents/{id}       — get one agent
PUT    /api/agents/{id}       — update an agent
DELETE /api/agents/{id}       — delete an agent

Agent configs are stored in-memory (via MultiAgentManager) and
can be persisted to JSON files for production use.

Note: AgentScope's ``create_app`` already has built-in agent
management (``/agents``). This router extends that with
product-specific fields (system_prompt, tool whitelist, etc.).
Mount it at a different prefix to avoid conflicts.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

agent_manage_router = APIRouter(prefix="/agents", tags=["agent-manage"])


# ------------------------------------------------------------------
# Request / response schemas
# ------------------------------------------------------------------


class AgentConfigRequest(BaseModel):
    """Create / update agent configuration."""

    agent_id: str = Field(description="Unique agent identifier")
    name: str = Field(default="", description="Display name")
    system_prompt: str = Field(
        default="你是一个乐于助人的AI助手。",
        description="System prompt for the agent",
    )
    model_provider: str = Field(
        default="",
        description="Provider id for the model",
    )
    model_name: str = Field(default="", description="Model name")
    max_iters: int = Field(default=20, description="Max ReAct iterations")
    enabled_tools: list[str] = Field(
        default_factory=list,
        description="Tool names to enable (empty = all)",
    )


class AgentConfigResponse(BaseModel):
    """Agent config as returned by the API."""

    agent_id: str
    name: str
    system_prompt: str
    model_provider: str
    model_name: str
    max_iters: int
    enabled_tools: list[str]


# ------------------------------------------------------------------
# In-memory store (replace with persistent storage in production)
# ------------------------------------------------------------------


class MultiAgentManager:
    """Simple in-memory multi-agent config manager.

    Replace with persistent storage (Redis, DB, JSON files) for
    production use. This is the QwenPaw ``app/multi_agent_manager.py``
    equivalent — managing multiple agent profiles with independent
    configs.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentConfigResponse] = {}
        # Seed a default agent
        default = AgentConfigResponse(
            agent_id="default",
            name="Default Agent",
            system_prompt="你是一个乐于助人的AI助手。",
            model_provider="",
            model_name="",
            max_iters=20,
            enabled_tools=[],
        )
        self._agents["default"] = default

    def list_agents(self) -> list[AgentConfigResponse]:
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> AgentConfigResponse | None:
        return self._agents.get(agent_id)

    def create_agent(self, config: AgentConfigRequest) -> AgentConfigResponse:
        if config.agent_id in self._agents:
            raise ValueError(f"Agent '{config.agent_id}' already exists")
        agent = AgentConfigResponse(**config.model_dump())
        self._agents[config.agent_id] = agent
        logger.info("agent created: %s", config.agent_id)
        return agent

    def update_agent(
        self,
        agent_id: str,
        config: AgentConfigRequest,
    ) -> AgentConfigResponse:
        if agent_id not in self._agents:
            raise KeyError(f"Agent '{agent_id}' not found")
        agent = AgentConfigResponse(**config.model_dump())
        self._agents[agent_id] = agent
        logger.info("agent updated: %s", agent_id)
        return agent

    def delete_agent(self, agent_id: str) -> bool:
        if agent_id == "default":
            raise ValueError("Cannot delete the default agent")
        if agent_id not in self._agents:
            return False
        del self._agents[agent_id]
        logger.info("agent deleted: %s", agent_id)
        return True


# ------------------------------------------------------------------
# Routes
# ------------------------------------------------------------------


def _get_manager(request: Request) -> MultiAgentManager:
    """Get the MultiAgentManager from app state."""
    mgr = getattr(request.app.state, "multi_agent_manager", None)
    if mgr is None:
        mgr = MultiAgentManager()
        request.app.state.multi_agent_manager = mgr
    return mgr


@agent_manage_router.get("", summary="List all agents")
async def list_agents(request: Request) -> list[dict]:
    mgr = _get_manager(request)
    return [a.model_dump() for a in mgr.list_agents()]


@agent_manage_router.post("", summary="Create a new agent")
async def create_agent(
    config: AgentConfigRequest,
    request: Request,
) -> dict:
    mgr = _get_manager(request)
    try:
        agent = mgr.create_agent(config)
        return agent.model_dump()
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))


@agent_manage_router.get("/{agent_id}", summary="Get one agent")
async def get_agent(agent_id: str, request: Request) -> dict:
    mgr = _get_manager(request)
    agent = mgr.get_agent(agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent.model_dump()


@agent_manage_router.put("/{agent_id}", summary="Update an agent")
async def update_agent(
    agent_id: str,
    config: AgentConfigRequest,
    request: Request,
) -> dict:
    mgr = _get_manager(request)
    try:
        # Ensure agent_id in path matches body
        config.agent_id = agent_id
        agent = mgr.update_agent(agent_id, config)
        return agent.model_dump()
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))


@agent_manage_router.delete("/{agent_id}", summary="Delete an agent")
async def delete_agent(agent_id: str, request: Request) -> dict:
    mgr = _get_manager(request)
    try:
        ok = mgr.delete_agent(agent_id)
        # ── 清理该 agent 的池资源（Pod + PVC） ──
        try:
            import asyncio
            runtime = getattr(request.app.state, "runtime", None)
            if runtime is not None:
                ws_mgr = getattr(runtime, "workspace_manager", None)
                if ws_mgr is not None and hasattr(ws_mgr, "cleanup_pool"):
                    # 后台任务，不阻塞响应
                    asyncio.create_task(ws_mgr.cleanup_pool(agent_id))
        except Exception:
            pass
        return {"deleted": ok}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


__all__ = ["agent_manage_router", "MultiAgentManager"]
