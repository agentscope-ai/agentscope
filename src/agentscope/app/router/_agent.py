# -*- coding: utf-8 -*-
"""Agent router for managing agent configurations."""
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException  # noqa: F401
from pydantic import BaseModel, Field

from agentscope.agent import CompressionConfig, ReActConfig


class AgentInfo(AgentConfig):
    """Full agent record including server-assigned metadata."""

    agent_id: str = Field(description="Unique identifier of the agent.")
    created_at: float = Field(description="Creation timestamp (Unix epoch).")
    updated_at: float = Field(
        description="Last-updated timestamp (Unix epoch)."
    )


class AgentListResponse(BaseModel):
    """Response model for listing agents."""

    agents: list[AgentInfo] = Field(description="List of agent records.")
    total: int = Field(description="Total number of agents.")


class CreateAgentResponse(BaseModel):
    """Response model after creating an agent."""

    agent_id: str = Field(
        description="Unique identifier of the newly created agent.",
    )
    name: str = Field(description="Display name of the created agent.")


class UpdateAgentRequest(BaseModel):
    """Request body for partially updating an agent.

    All fields are optional; omit any field to keep its current value.
    """

    name: str | None = Field(default=None, description="New display name.")
    system_prompt: str | None = Field(
        default=None,
        description="New system prompt.",
    )
    chat_model_config: ChatModelConfig | None = Field(
        default=None,
        description="New model configuration.",
    )
    compression_config: CompressionConfig | None = Field(
        default=None,
        description="New compression configuration.",
    )
    react_config: ReActConfig | None = Field(
        default=None,
        description="New ReAct loop configuration.",
    )


agent_router = APIRouter(
    prefix="/agent",
    tags=["agent"],
    responses={404: {"description": "Not found"}},
)


@agent_router.get(
    "/",
    response_model=AgentListResponse,
    summary="List all agents",
)
async def list_agents() -> AgentListResponse:
    """Return a list of all stored agent configurations.

    Returns:
        `AgentListResponse`:
            The list of agents and the total count.
    """
    # TODO: query storage / agent registry to retrieve all agent records
    agents: list[AgentInfo] = []
    return AgentListResponse(agents=agents, total=len(agents))


@agent_router.post(
    "/",
    response_model=CreateAgentResponse,
    status_code=201,
    summary="Create a new agent",
)
async def create_agent(body: AgentConfig) -> CreateAgentResponse:
    """Create and persist a new agent configuration.

    Args:
        body (`AgentConfig`):
            The full agent configuration to store.

    Returns:
        `CreateAgentResponse`:
            The server-assigned identifier and display name of the new agent.
    """
    agent_id = uuid.uuid4().hex
    # TODO: persist the agent configuration to storage / agent registry
    return CreateAgentResponse(agent_id=agent_id, name=body.name)


@agent_router.delete(
    "/{agent_id}",
    status_code=204,
    summary="Delete an agent",
)
async def delete_agent(agent_id: str) -> None:
    """Permanently delete an agent configuration.

    Args:
        agent_id (`str`):
            The unique identifier of the agent to delete.

    Raises:
        `HTTPException`:
            404 if the agent does not exist.
    """
    # TODO: verify agent exists; raise HTTPException(status_code=404) if not found
    # TODO: delete the agent record from storage / agent registry


@agent_router.patch(
    "/{agent_id}",
    response_model=AgentInfo,
    summary="Update an agent",
)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
) -> AgentInfo:
    """Partially update an existing agent configuration.

    Only the fields present in the request body are updated; all other fields
    keep their current values.

    Args:
        agent_id (`str`):
            The unique identifier of the agent to update.
        body (`UpdateAgentRequest`):
            Fields to update.

    Returns:
        `AgentInfo`:
            The full agent record after the update.

    Raises:
        `HTTPException`:
            404 if the agent does not exist.
    """
    # TODO: load existing agent record; raise HTTPException(status_code=404) if not found
    # TODO: apply partial updates and persist the changes to storage
    # TODO: return the updated agent record fetched from storage

    # Placeholder – replaced once storage is wired in
    raise HTTPException(status_code=501, detail="Not implemented")
