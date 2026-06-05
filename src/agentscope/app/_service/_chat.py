# -*- coding: utf-8 -*-
"""Chat service encapsulating agent execution + persistence logic.

This is the single source of truth for running an agent against a
session. Both the HTTP chat endpoint and the wakeup dispatcher call
:meth:`ChatService.run`, guaranteeing identical message persistence,
middleware wiring, and state handling.

Events produced by the agent are not exposed back through this method
— they are published to the message bus inside the run, and any client
that wants them subscribes through the
``GET /sessions/{sid}/stream`` SSE endpoint.
"""
from fastapi import HTTPException

from ..message_bus import MessageBus
from ..storage import StorageBase
from .._manager import BackgroundTaskManager, SchedulerManager
from ..workspace_manager import WorkspaceManagerBase
from ..middleware import (
    InboxMiddleware,
    StateChangeMiddleware,
    ToolOffloadMiddleware,
)
from .._types import AgentMiddlewareFactory, AgentToolFactory
from ._model import get_model
from ._toolkit import get_toolkit

from ..._logging import logger
from ...agent import Agent, ModelConfig
from ...event import (
    ReplyStartEvent,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
)
from ...message import AssistantMsg, Msg, ToolCallState


class ChatService:
    """Run an agent against a session, persisting input/reply messages
    and updated agent state.

    Shared by the HTTP chat endpoint and the wakeup dispatcher so both
    paths go through identical validation, assembly, and persistence.

    Session serialisation and event fan-out are both handled by the
    :class:`MessageBus`: :meth:`bus.session_run` acquires a distributed
    lock (guaranteeing at most one chat run per session across all
    processes), and :meth:`bus.session_publish_event` writes each event
    to both a replay log (for late-joining subscribers) and a live
    Pub/Sub channel.
    """

    def __init__(
        self,
        storage: StorageBase,
        workspace_manager: WorkspaceManagerBase,
        scheduler_manager: SchedulerManager,
        background_task_manager: BackgroundTaskManager,
        message_bus: MessageBus,
        extra_agent_middlewares: AgentMiddlewareFactory | None = None,
        extra_agent_tools: AgentToolFactory | None = None,
    ) -> None:
        """Initialize chat service.

        Args:
            storage (`StorageBase`):
                Application storage backend.
            workspace_manager (`WorkspaceManagerBase`):
                Provides per-session workspace (tools, MCPs, skills) used
                during agent assembly.
            scheduler_manager (`SchedulerManager`):
                Application scheduler — passed through to
                :func:`get_toolkit` so the agent toolkit gets the four
                ``Schedule*`` tools.
            background_task_manager (`BackgroundTaskManager`):
                Tracks offloaded long-running tool tasks. Also provides
                the :class:`TaskStop` tool through
                :func:`get_toolkit`.
            message_bus (`MessageBus`):
                Application-wide message bus. Provides session-level
                distributed locking (via :meth:`session_run`), event
                replay + live fan-out (via :meth:`session_publish_event`),
                and inbox delivery (via :class:`InboxMiddleware`).
            extra_agent_middlewares (`AgentMiddlewareFactory | None`, \
optional):
                Async factory invoked at every chat turn to produce
                user/session-specific middlewares to attach to the agent.
            extra_agent_tools (`AgentToolFactory | None`, optional):
                Async factory invoked at every chat turn to produce
                user/session-specific tools to register in the toolkit.
        """
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._scheduler_manager = scheduler_manager
        self._background_task_manager = background_task_manager
        self._message_bus = message_bus
        self._extra_agent_middlewares = extra_agent_middlewares
        self._extra_agent_tools = extra_agent_tools

    async def run(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> None:
        """Drive a chat run to completion.

        Persists input messages (Case A) or the incoming continuation
        event applied to the existing reply (Case B), runs the agent
        while publishing every produced event to the message bus, and
        persists the rebuilt reply ``Msg`` + updated agent state when
        finished.

        Session serialisation is handled by the bus's distributed lock
        (:meth:`MessageBus.session_run`); events are simultaneously
        persisted to the replay log and fanned out on the live channel
        via :meth:`MessageBus.session_publish_event`. Exceptions are
        logged and swallowed so a single failed fire does not tear
        down its trigger (HTTP request task, wakeup dispatcher, …).

        Args:
            user_id (`str`):
                Authenticated caller's user ID.
            session_id (`str`):
                Target session ID.
            agent_id (`str`):
                Agent to run.
            input_msg:
                One of:

                - ``Msg`` / ``list[Msg]``: new user message(s) (Case A).
                - ``None``: continue from current state — used by the
                  wakeup dispatcher when there is no fresh user input
                  but pending inbox content needs draining (Case A
                  with no input).
                - ``UserConfirmResultEvent`` /
                  ``ExternalExecutionResultEvent``: resume an awaiting
                  tool call (Case B).
        """
        try:
            await self._run_impl(user_id, session_id, agent_id, input_msg)
        except Exception as e:
            logger.exception(
                "ChatService.run failed for user_id=%s session_id=%s "
                "agent_id=%s, error=%s",
                user_id,
                session_id,
                agent_id,
                str(e),
            )

    async def _run_impl(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | None,
    ) -> None:
        """The actual chat-run body; wrapped by :meth:`run` for error
        swallowing. Separated so the try/except doesn't bury the
        per-step logic at one extra indentation level."""

        # ----------------------------------------------------------------
        # 1. Load records + resolve workspace ONCE here, reused below.
        # Reject missing records up front with a clear error so the
        # downstream assembly code can rely on non-None values.
        # ----------------------------------------------------------------
        agent_record = await self._storage.get_agent(user_id, agent_id)
        if agent_record is None:
            raise HTTPException(
                status_code=404,
                detail=f"Agent {agent_id!r} not found.",
            )
        session_record = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session_record is None:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"Session {session_id!r} not found for "
                    f"agent {agent_id!r}."
                ),
            )
        workspace = await self._workspace_manager.get_workspace(
            user_id,
            agent_id,
            session_id,
            session_record.config.workspace_id,
        )

        # ----------------------------------------------------------------
        # 2. Toolkit (workspace tools + planning + TaskStop + schedule +
        # team + extras + skills + mcps).
        # ----------------------------------------------------------------
        toolkit = await get_toolkit(
            storage=self._storage,
            workspace=workspace,
            scheduler_manager=self._scheduler_manager,
            background_task_manager=self._background_task_manager,
            message_bus=self._message_bus,
            user_id=user_id,
            agent_record=agent_record,
            session_record=session_record,
            extra_factory=self._extra_agent_tools,
        )

        # ----------------------------------------------------------------
        # 3. Middlewares — framework-supplied first, then caller extras.
        # Background-tool completions deliver their results via
        # ``message_bus.inbox_push + enqueue_wakeup``, so the dispatcher
        # (any process) wakes an idle session — no in-process retrigger
        # plumbing is needed here.
        # ----------------------------------------------------------------
        middlewares: list = [
            InboxMiddleware(self._message_bus),
            StateChangeMiddleware(
                message_bus=self._message_bus,
                session_id=session_id,
            ),
            ToolOffloadMiddleware(
                bg_manager=self._background_task_manager,
                message_bus=self._message_bus,
                user_id=user_id,
                agent_id=agent_id,
            ),
        ]
        if self._extra_agent_middlewares is not None:
            middlewares.extend(
                await self._extra_agent_middlewares(
                    user_id,
                    agent_id,
                    session_id,
                ),
            )

        # ----------------------------------------------------------------
        # 4. Model + fallback (resolved from session's config).
        # ----------------------------------------------------------------
        model_cfg = session_record.config.chat_model_config
        if not model_cfg:
            raise HTTPException(
                status_code=404,
                detail=f"No model configuration found for agent {agent_id}",
            )
        model = await get_model(user_id, model_cfg, self._storage)

        fallback_cfg = session_record.config.fallback_chat_model_config
        fallback_model = (
            await get_model(user_id, fallback_cfg, self._storage)
            if fallback_cfg is not None
            else None
        )

        # ----------------------------------------------------------------
        # 5. Assemble the Agent.
        # ----------------------------------------------------------------
        agent_state = session_record.state
        agent_state.session_id = session_id
        agent = Agent(
            name=agent_record.data.name,
            system_prompt=agent_record.data.system_prompt,
            model=model,
            toolkit=toolkit,
            model_config=ModelConfig(fallback_model=fallback_model),
            context_config=agent_record.data.context_config,
            react_config=agent_record.data.react_config,
            state=agent_state,
            middlewares=middlewares,
            offloader=workspace,
        )

        # ----------------------------------------------------------------
        # 6. Guard: skip wake-up driven runs when the agent is parked on
        # an awaiting tool call.
        #
        # Wake-ups deliver pending inbox content (team messages, etc.) by
        # poking the dispatcher to run the session with ``input_msg=None``.
        # If the agent is currently parked on an ``ASKING`` or
        # ``SUBMITTED`` tool call (waiting for user confirmation or
        # external-execution results), kicking off another ``None`` run
        # would hit :meth:`Agent._check_incoming_event`, which rightly
        # rejects ``None`` when there is something to confirm — and fail
        # the run noisily. The inbox content is safe to leave queued:
        # whenever the user does confirm (or the external result lands),
        # the resuming run's next reasoning step lets
        # :class:`InboxMiddleware` drain the queue naturally.
        # ----------------------------------------------------------------
        if input_msg is None and agent.state.context:
            last_msg = agent.state.context[-1]
            if last_msg.role == "assistant" and last_msg.name == agent.name:
                awaiting = [
                    tc
                    for tc in last_msg.get_content_blocks("tool_call")
                    if tc.state
                    in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
                ]
                if awaiting:
                    logger.info(
                        "Skipping wake-up for session %s: agent is parked "
                        "on %d awaiting tool call(s); inbox messages will "
                        "be drained when the agent resumes.",
                        session_id,
                        len(awaiting),
                    )
                    return

        # ----------------------------------------------------------------
        # 7. Run the agent inside the bus's distributed session lock
        # ----------------------------------------------------------------
        async with self._message_bus.session_run(session_id):
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
                    await self._message_bus.session_publish_event(
                        session_id,
                        event.model_dump(mode="json"),
                    )
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
                    reply_msg.append_event(input_msg)

                async for event in agent.reply_stream(inputs=input_msg):
                    await self._message_bus.session_publish_event(
                        session_id,
                        event.model_dump(mode="json"),
                    )
                    if reply_msg is not None:
                        reply_msg.append_event(event)

            # Persist the reply Msg (upsert: overwrite if same id, append
            # if new).
            if reply_msg is not None:
                await self._storage.upsert_message(
                    user_id,
                    session_id,
                    reply_msg,
                )

            # Persist the updated agent state. MUST happen inside the
            # session lock: if we released the lock first, another
            # process could acquire it and load a stale state from
            # storage before this write lands.
            await self._storage.update_session_state(
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                state=agent.state,
            )

        # ``session_run.__aexit__`` trims the replay log before
        # releasing the lock — see :meth:`MessageBus.session_run`.
