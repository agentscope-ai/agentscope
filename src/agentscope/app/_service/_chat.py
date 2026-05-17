# -*- coding: utf-8 -*-
"""Chat service encapsulating agent execution logic."""
from collections.abc import AsyncGenerator

from fastapi import HTTPException, status

from ..storage import StorageBase
from .._manager import SessionManager, WorkspaceManagerBase
from ._model import get_model
from ...agent import Agent
from ...tool import Toolkit
from ...event import (
    AgentEvent,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
)
from ...message import Msg


class ChatService:
    """Encapsulates agent chat execution logic.

    Shared by the HTTP chat endpoint and the schedule trigger so both
    paths go through identical validation, assembly, and persistence.
    """

    def __init__(
        self,
        storage: StorageBase,
        session_manager: SessionManager,
        workspace_manager: WorkspaceManagerBase | None = None,
    ) -> None:
        """Initialize chat service."""
        self._storage = storage
        self._session_manager = session_manager
        self._workspace_manager = workspace_manager

    async def stream_chat(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the agent and yield events.

        Args:
            user_id: Authenticated caller's user ID.
            session_id: Target session ID.
            agent_id: Agent to run.
            input_msg: User message, or None to continue from current state.

        Yields:
            AgentEvent: Streamed events from the agent.

        Raises:
            HTTPException: 404 if session, agent, or credential not found.
        """
        # 1. Load and validate records
        session_record = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session_record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Session {session_id!r} not found.",
            )

        agent_record = await self._storage.get_agent(user_id, agent_id)
        if agent_record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Agent {agent_id!r} not found.",
            )

        # 2. Build the model (loads credential + instantiates)
        cfg = session_record.config.chat_model_config
        if cfg is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No model configured for this session. Update the "
                "session with a chat_model_config first.",
            )
        model = await get_model(user_id, cfg, self._storage)

        # 3. Build toolkit
        # TODO: load tools / skills configured on the agent record
        toolkit = Toolkit()

        # 4. Restore persisted state; permission_context.mode is already set
        #    on the stored state (set when the session was created or
        #    last updated)
        state = session_record.state
        state.session_id = session_id

        # 5. Assemble the agent from stored configuration + live state
        agent = Agent(
            name=agent_record.data.name,
            system_prompt=agent_record.data.system_prompt,
            model=model,
            toolkit=toolkit,
            context_config=agent_record.data.context_config,
            react_config=agent_record.data.react_config,
            state=state,
        )

        # 6. Run inside the session manager (serialises concurrent requests,
        #    buffers events for late-joining SSE subscribers)
        async with self._session_manager.run(session_id) as run:
            async for event in agent.reply_stream(input_msg):
                await run.publish(event)
                yield event

        # 7. Persist the updated agent state back into the session
        await self._storage.update_session_state(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            state=agent.state,
        )
