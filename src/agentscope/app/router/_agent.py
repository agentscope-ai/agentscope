# -*- coding: utf-8 -*-
"""Agent router — CRUD endpoints for agent configurations."""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status

from .._deps import get_current_user_id, get_storage
from .._schema._agent import (
    AgentListResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
)
from ..storage._base import StorageBase
from ..storage._model._agent import AgentData, AgentRecord

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
async def list_agents(
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> AgentListResponse:
    """Return all agent records belonging to the authenticated user.

    Args:
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `AgentListResponse`: All agent records and their total count.
    """
    agents = await storage.list_agent(user_id)
    return AgentListResponse(agents=agents, total=len(agents))


@agent_router.post(
    "/",
    response_model=CreateAgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new agent",
)
async def create_agent(
    body: CreateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> CreateAgentResponse:
    """Create and persist a new agent configuration.

    Args:
        body (`CreateAgentRequest`): Agent configuration to store.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `CreateAgentResponse`: The server-assigned agent identifier.
    """
    record = AgentRecord(
        user_id=user_id,
        data=AgentData(
            name=body.name,
            system_prompt=body.system_prompt,
            context_config=body.context_config,
            react_config=body.react_config,
        ),
    )
    agent_id = await storage.create_agent(user_id, record)
    return CreateAgentResponse(agent_id=agent_id)


@agent_router.patch(
    "/{agent_id}",
    response_model=AgentRecord,
    summary="Update an agent",
)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> AgentRecord:
    """Partially update an existing agent configuration.

    Only the fields present in the request body are updated; all other fields
    keep their current values.

    Args:
        agent_id (`str`): The agent to update.
        body (`UpdateAgentRequest`): Fields to update.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Returns:
        `AgentRecord`: The full agent record after the update.

    Raises:
        `HTTPException`: 404 if the agent does not exist or does not belong
            to the authenticated user.
    """
    agents = await storage.list_agent(user_id)
    existing = next((a for a in agents if a.id == agent_id), None)
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )

    updates = body.model_dump(exclude_none=True)
    updated_data = existing.data.model_copy(update=updates)
    existing = existing.model_copy(
        update={"data": updated_data, "updated_at": datetime.now()}
    )
    await storage.create_agent(user_id, existing)
    return existing


@agent_router.delete(
    "/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an agent",
)
async def delete_agent(
    agent_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> None:
    """Permanently delete an agent configuration.

    Args:
        agent_id (`str`): The agent to delete.
        user_id (`str`): Injected authenticated user ID.
        storage (`StorageBase`): Injected storage backend.

    Raises:
        `HTTPException`: 404 if the agent does not exist or does not belong
            to the authenticated user.
    """
    deleted = await storage.delete_agent(user_id, agent_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{agent_id}' not found.",
        )
