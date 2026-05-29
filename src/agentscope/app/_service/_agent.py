# -*- coding: utf-8 -*-
"""The agent service layer, responsible for getting the agent class."""
from fastapi import HTTPException

from ._model import get_model
from .._manager import WorkspaceManagerBase
from .._types import AgentMiddlewareFactory, AgentToolFactory
from ..storage import StorageBase
from ...agent import Agent, ModelConfig
from ...middleware import MiddlewareBase
from ...tool import Toolkit


async def get_agent(
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
    user_id: str,
    agent_id: str,
    session_id: str,
    middlewares: list[MiddlewareBase] | None = None,
    extra_agent_middlewares: AgentMiddlewareFactory | None = None,
    extra_agent_tools: AgentToolFactory | None = None,
) -> Agent:
    """Assemble the agent for ``(user_id, agent_id, session_id)``.

    Loads the agent configuration and session state from storage, resolves
    the chat model, materialises the per-session workspace (tools / skills
    / MCPs), and wires in any caller-supplied middlewares plus extras
    produced by the supplied factories.

    Args:
        storage (`StorageBase`):
            Application storage backend used to fetch the agent record and
            session record.
        workspace_manager (`WorkspaceManagerBase`):
            Workspace manager used to obtain the per-session workspace.
        user_id (`str`):
            The authenticated caller's user ID.
        agent_id (`str`):
            The agent record ID to assemble.
        session_id (`str`):
            The session ID whose persisted state will be restored onto the
            agent.
        middlewares (`list[MiddlewareBase] | None`, optional):
            Framework-supplied middlewares (e.g.
            ``ToolOffloadMiddleware``) to attach to the agent.  Extras
            produced by ``extra_agent_middlewares`` are appended to this
            list.
        extra_agent_middlewares (`AgentMiddlewareFactory | None`, optional):
            Async factory ``(user_id, agent_id, session_id) -> list[
            MiddlewareBase]``.  Awaited once per call; results are
            appended to ``middlewares``.
        extra_agent_tools (`AgentToolFactory | None`, optional):
            Async factory ``(user_id, agent_id, session_id) -> list[
            ToolBase]``.  Awaited once per call; results are merged into
            the toolkit's ``"basic"`` group alongside workspace tools.

    Returns:
        `Agent`: The fully-assembled agent ready to reply.
    """

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
    # Step 2.1.1. Build the optional fallback model
    # ====================================================================
    # The fallback model is invoked by the agent when the primary model
    # fails. ``None`` means no fallback is configured for this session.
    fallback_cfg = session_record.config.fallback_chat_model_config
    fallback_model = (
        await get_model(user_id, fallback_cfg, storage)
        if fallback_cfg is not None
        else None
    )

    # ====================================================================
    # Step 2.2. Get the session data, i.e. the agent state
    # ====================================================================
    agent_state = session_record.state
    agent_state.session_id = session_id

    # ====================================================================
    # Step 2.3. Get the workspace from the manager
    # ====================================================================
    workspace = await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
    )

    # ====================================================================
    # Step 3. Resolve caller-supplied factories for tools and middlewares
    # ====================================================================
    tools = await workspace.list_tools()
    if extra_agent_tools is not None:
        tools = tools + await extra_agent_tools(
            user_id,
            agent_id,
            session_id,
        )

    final_middlewares = list(middlewares or [])
    if extra_agent_middlewares is not None:
        final_middlewares.extend(
            await extra_agent_middlewares(user_id, agent_id, session_id),
        )

    return Agent(
        name=cfg.name,
        system_prompt=cfg.system_prompt,
        model=model,
        toolkit=Toolkit(
            tools=tools,
            skills_or_loaders=await workspace.list_skills(),
            mcps=await workspace.list_mcps(),
        ),
        model_config=ModelConfig(fallback_model=fallback_model),
        context_config=cfg.context_config,
        react_config=cfg.react_config,
        state=agent_state,
        middlewares=final_middlewares,
        offloader=workspace,
    )
