# -*- coding: utf-8 -*-
"""The agent service layer, responsible for getting the agent class."""
from fastapi import HTTPException

from ._model import get_model
from .._middleware import ToolOffloadMiddleware
from .._manager import WorkspaceManagerBase, BackgroundTaskManager
from ..storage import StorageBase
from ...agent import Agent
from ...tool import Toolkit


async def get_agent(
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
    background_task_manager: BackgroundTaskManager,
    user_id: str,
    agent_id: str,
    session_id: str,
) -> Agent:
    """Get the agent class for the given agent ID."""

    # ====================================================================
    # Step 1. Get the agent configuration
    # ====================================================================
    agent_record = await storage.get_agent(
        user_id=user_id,
        agent_id=agent_id,
    )

    cfg = agent_record.data

    # ====================================================================
    # Step 2. Get the agent state from the session
    # ====================================================================
    # TODO: get_session需要agent_id
    session_record = await storage.get_session(user_id, agent_id, session_id)

    # ====================================================================
    # Step 2.1. Get the model instance
    # ====================================================================
    model_cfg = session_record.config.chat_model_config

    if not model_cfg:
        # Raise error to the frontend
        raise HTTPException(
            status_code=404,
            detail=f"No model configuration found for agent {agent_id}",
        )

    model = await get_model(user_id, model_cfg, storage)

    # ====================================================================
    # Step 2.2. Get the session data, i.e. the agent state
    # ====================================================================
    agent_state = session_record.state

    # ====================================================================
    # Step 2.3. Get the workspace from the manager
    # ====================================================================
    workspace = await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
    )

    # TODO: should be configurable
    from agentscope.tool import Bash, Read, Write, Edit
    import os

    toolkit = Toolkit(
        tools=[Bash(), Read(), Write(), Edit()],
        skills=[os.path.join(workspace.workdir, "skills", "pdf")],
    )

    for mcp_client in await workspace.list_mcps():
        await toolkit.register_mcp(mcp_client)

    return Agent(
        name=cfg.name,
        system_prompt=cfg.system_prompt,
        model=model,
        toolkit=toolkit,
        context_config=cfg.context_config,
        react_config=cfg.react_config,
        state=agent_state,
        middlewares=[ToolOffloadMiddleware(background_task_manager)],
        # workspace=workspace,
    )
