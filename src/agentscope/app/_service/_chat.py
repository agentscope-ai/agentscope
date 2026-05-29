# -*- coding: utf-8 -*-
"""Chat service encapsulating agent execution + persistence logic.

This is the single source of truth for running an agent against a session.
Both the HTTP chat endpoint and the schedule trigger call
:meth:`ChatService.stream_chat`, guaranteeing identical message
persistence, ``ToolOffloadMiddleware`` wiring, and state handling.
"""
from collections.abc import AsyncGenerator

from ..storage import StorageBase
from .._manager import (
    BackgroundTaskManager,
    SessionManager,
    WorkspaceManagerBase,
)
from .._middleware import ToolOffloadMiddleware
from .._types import AgentMiddlewareFactory, AgentToolFactory
from ._agent import get_agent
from ..._logging import logger
from ...event import (
    AgentEvent,
    ReplyStartEvent,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
)
from ...message import AssistantMsg, Msg


class ChatService:
    """Run an agent against a session, streaming events while persisting
    input/reply messages and updated agent state.

    Shared by the HTTP chat endpoint and the schedule trigger so both
    paths go through identical validation, assembly, and persistence.
    """

    def __init__(
        self,
        storage: StorageBase,
        session_manager: SessionManager,
        background_task_manager: BackgroundTaskManager,
        workspace_manager: WorkspaceManagerBase,
        extra_agent_middlewares: AgentMiddlewareFactory | None = None,
        extra_agent_tools: AgentToolFactory | None = None,
    ) -> None:
        """Initialize chat service.

        Args:
            storage (`StorageBase`):
                Application storage backend.
            session_manager (`SessionManager`):
                Per-session run serializer and event buffer for SSE
                subscribers.
            background_task_manager (`BackgroundTaskManager`):
                Tracks offloaded long-running tool tasks and queues their
                results for re-injection on the next reasoning step.
            workspace_manager (`WorkspaceManagerBase`):
                Provides per-session workspace (tools, MCPs, skills) used
                during agent assembly.
            extra_agent_middlewares (`AgentMiddlewareFactory | None`, \
optional):
                Async factory invoked at every chat turn to produce
                user/session-specific middlewares to attach to the agent.
            extra_agent_tools (`AgentToolFactory | None`, optional):
                Async factory invoked at every chat turn to produce
                user/session-specific tools to register in the toolkit.
        """
        self._storage = storage
        self._session_manager = session_manager
        self._background_task_manager = background_task_manager
        self._workspace_manager = workspace_manager
        self._extra_agent_middlewares = extra_agent_middlewares
        self._extra_agent_tools = extra_agent_tools

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

        Persists input messages (Case A) or the incoming continuation event
        applied to the existing reply (Case B), streams the agent's reply
        while building an ``AssistantMsg`` from the event flow, and
        persists the reply and updated agent state when finished.

        Args:
            user_id (`str`):
                Authenticated caller's user ID.
            session_id (`str`):
                Target session ID.
            agent_id (`str`):
                Agent to run.
            input_msg:
                One of:
                - ``Msg`` / ``list[Msg]``: new user message(s) (Case A)
                - ``None``: continue from current state, e.g. a retrigger
                  triggered by a completed background tool (Case A with
                  no input)
                - ``UserConfirmResultEvent`` /
                ``ExternalExecutionResultEvent``:
                  resume an awaiting tool call (Case B)

        Yields:
            AgentEvent: Streamed events from the agent.
        """

        # ----------------------------------------------------------------
        # 1. Build middlewares
        # ----------------------------------------------------------------
        async def _retrigger(
            sess_id: str,
            ag_id: str,
            uid: str,
        ) -> None:
            """Re-invoke the agent to process completed background results."""
            async for _ in self.stream_chat(
                user_id=uid,
                session_id=sess_id,
                agent_id=ag_id,
                input_msg=None,
            ):
                pass

        middlewares: list = [
            ToolOffloadMiddleware(
                self._background_task_manager,
                user_id=user_id,
                agent_id=agent_id,
                session_manager=self._session_manager,
                retrigger_fn=_retrigger,
            ),
        ]

        # ----------------------------------------------------------------
        # 2. Assemble the agent (loads session/agent/credential/model,
        #    restores state, builds toolkit from workspace)
        # ----------------------------------------------------------------
        agent = await get_agent(
            storage=self._storage,
            workspace_manager=self._workspace_manager,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            middlewares=middlewares,
            extra_agent_middlewares=self._extra_agent_middlewares,
            extra_agent_tools=self._extra_agent_tools,
        )

        # ----------------------------------------------------------------
        # 3. Run the agent inside the session manager context
        # ----------------------------------------------------------------
        async with self._session_manager.run(session_id) as run:
            reply_msg: Msg | None = None

            if input_msg is None or isinstance(input_msg, (Msg, list)):
                # Case A: new reply (user message(s), or retrigger with
                # empty input)
                if isinstance(input_msg, (Msg, list)):
                    input_msgs = (
                        [input_msg]
                        if isinstance(input_msg, Msg)
                        else input_msg
                    )
                    for msg in input_msgs:
                        await self._storage.upsert_message(
                            user_id,
                            session_id,
                            msg,
                        )

                async for event in agent.reply_stream(inputs=input_msg):
                    await run.publish(event)
                    yield event
                    if isinstance(event, ReplyStartEvent):
                        reply_msg = AssistantMsg(
                            id=event.reply_id,
                            name=event.name,
                            content=[],
                        )
                    elif reply_msg is not None:
                        reply_msg.append_event(event)

            else:
                # Case B: continuation (UserConfirmResult / ExternalExecResult)
                reply_msg = await self._storage.get_message(
                    user_id,
                    session_id,
                    agent.state.reply_id,
                )

                if reply_msg is None:
                    logger.warning(
                        "Reply message %r not found in storage for session "
                        "%r; tool-call state changes from the incoming event "
                        "will not be persisted.",
                        agent.state.reply_id,
                        session_id,
                    )
                elif input_msg:
                    # Apply the incoming event so the persisted message
                    # reflects updated tool-call states before streaming
                    # resumes (e.g. ASKING→ALLOWED/FINISHED for
                    # UserConfirmResultEvent, or appended ToolResultBlocks
                    # for ExternalExecutionResultEvent).
                    reply_msg.append_event(input_msg)

                async for event in agent.reply_stream(inputs=input_msg):
                    await run.publish(event)
                    yield event
                    if reply_msg is not None:
                        reply_msg.append_event(event)

            # Persist the reply Msg (upsert: overwrite if same id, append
            # if new)
            if reply_msg is not None:
                await self._storage.upsert_message(
                    user_id,
                    session_id,
                    reply_msg,
                )

        # ----------------------------------------------------------------
        # 4. Persist agent state
        # ----------------------------------------------------------------
        await self._storage.update_session_state(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            state=agent.state,
        )
