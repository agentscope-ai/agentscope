# -*- coding: utf-8 -*-
"""Chat service encapsulating agent execution logic."""
from collections.abc import AsyncGenerator

from fastapi import HTTPException, status

from ..storage import StorageBase
from .._manager import SessionManager
from ...agent import Agent
from ...model import _deserialize_model
from ...tool import Toolkit
from ...event import AgentEvent
from ...message import Msg
from ...permission import PermissionMode, PermissionContext
from ..storage._model._session import SessionData


class ChatService:
    """Encapsulates agent chat execution logic.

    Shared by the HTTP chat endpoint and the schedule trigger so both
    paths go through identical validation, assembly, and persistence.
    """

    def __init__(
        self,
        storage: StorageBase,
        session_manager: SessionManager,
    ) -> None:
        self._storage = storage
        self._session_manager = session_manager

    async def stream_chat(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Msg | None,
        permission_mode: PermissionMode = PermissionMode.DEFAULT,
    ) -> AsyncGenerator[AgentEvent, None]:
        """Run the agent and yield events.

        Args:
            user_id: Authenticated caller's user ID.
            session_id: Target session ID.
            agent_id: Agent to run.
            input_msg: User message, or None to continue from current state.
            permission_mode: Permission level for this execution.

        Yields:
            AgentEvent: Streamed events from the agent.

        Raises:
            HTTPException: 404 if session, agent, or credential not found.
        """
        # 1. Load and validate records
        session_record = await self._storage.get_session(user_id, session_id)
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

        # 2. Build the model: merge credential secrets with caller-supplied params
        cfg = session_record.data.chat_model_config
        credential = await self._storage.get_credential(
            user_id,
            cfg.credential_id,
        )
        if credential is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Credential {cfg.credential_id!r} not found.",
            )
        # credential.data holds the API key / base_url; cfg.parameters holds
        # model-name, temperature, etc.  Parameters win on conflict.
        model = _deserialize_model(
            {"type": cfg.type, **credential.data, **cfg.parameters},
        )

        # 3. Build toolkit
        # TODO: load tools / skills configured on the agent record
        toolkit = Toolkit()

        # 4. Restore persisted state and apply the requested permission mode
        state = session_record.data.agent_state
        state.session_id = session_id
        state.permission_context = PermissionContext(mode=permission_mode)

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
        await self._storage.upsert_session(
            user_id=user_id,
            agent_id=session_record.agent_id,
            workspace_id=session_record.workspace_id,
            session_data=SessionData(
                agent_state=agent.state,
                chat_model_config=cfg,
            ),
        )
