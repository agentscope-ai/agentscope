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

from ..message_bus import MessageBus, MessageBusKeys
from .._bus_ops import publish_session_event
from ..storage import StorageBase, AgentRecord, SessionRecord
from .._manager import BackgroundTaskManager, SchedulerManager
from ..workspace_manager import WorkspaceManagerBase
from ..middleware import (
    InboxMiddleware,
    StateChangeMiddleware,
    ToolOffloadMiddleware,
)
from ...middleware import TTSMiddleware
from .._types import (
    AgentMiddlewareFactory,
    AgentToolFactory,
    EventProjector,
    SubAgentTemplate,
)
from ._model import get_model
from ._tts_model import get_tts_model
from ._toolkit import get_toolkit
from ._session_projection import SessionProjection
from ._projectors import SubagentHitlProjector

from ..._logging import logger
from ...agent import Agent, ModelConfig
from ...event import (
    AgentEvent,
    ReplyStartEvent,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
)
from ...message import AssistantMsg, Msg, ToolCallState
from ...permission import AdditionalWorkingDirectory


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
        custom_subagent_templates: dict[str, SubAgentTemplate] | None = None,
        custom_agent_cls: type[Agent] | None = None,
        extra_projectors: list[EventProjector] | None = None,
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
                the :class:`ToolStop` tool through
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
            custom_subagent_templates (`dict[str, SubAgentTemplate] | None`,\
             optional):
                Sub-agent template registry, keyed by template type.
                Passed through to :func:`get_toolkit` so that
                ``AgentCreate`` can route to the appropriate template
                when a ``subagent_type`` is specified.
            custom_agent_cls (`type[Agent] | None`, optional):
                Custom :class:`Agent` subclass for assembling agents.
                Falls back to :class:`Agent` when ``None``.
            extra_projectors (`list[EventProjector] | None`, optional):
                Additional cross-session event projectors to run after
                the built-in ones (mirrors the ``extra_agent_*``
                injection style). Each is invoked once per produced
                event to mirror a UI feed onto another session; see
                :class:`~agentscope.app._types.EventProjector`.
        """
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._scheduler_manager = scheduler_manager
        self._background_task_manager = background_task_manager
        self._message_bus = message_bus
        self._extra_agent_middlewares = extra_agent_middlewares
        self._extra_agent_tools = extra_agent_tools
        self._sub_agent_templates = custom_subagent_templates
        self._agent_cls = custom_agent_cls or Agent
        self._projection = SessionProjection(message_bus)
        self._projectors: list[EventProjector] = [
            SubagentHitlProjector(storage),
            *(extra_projectors or []),
        ]

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

        # Add workspace working directory to the permission context
        if (
            workspace.workdir
            not in session_record.state.permission_context.working_directories
        ):
            session_record.state.permission_context.working_directories[
                workspace.workdir
            ] = AdditionalWorkingDirectory(
                path=workspace.workdir,
                source="session",
            )

        # ----------------------------------------------------------------
        # 2. Toolkit (workspace tools + planning + ToolStop + schedule +
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
            sub_agent_templates=self._sub_agent_templates,
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
        # 3b. TTS middleware — inject when the session has a TTS config.
        # ----------------------------------------------------------------
        tts_cfg = session_record.config.tts_model_config
        if tts_cfg is not None:
            tts_model = await get_tts_model(
                user_id,
                tts_cfg,
                self._storage,
            )
            middlewares.append(TTSMiddleware(tts_model))

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
        agent = self._agent_cls(
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
        # 7. Run the agent inside the distributed session lock
        # ----------------------------------------------------------------
        lock_key = MessageBusKeys.session_lock(session_id)
        events_key = MessageBusKeys.session_events(session_id)
        async with self._message_bus.acquire_lock(
            lock_key,
            ttl_secs=MessageBusKeys.SESSION_RUN_TTL_SECS,
        ):
            try:
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
                        await publish_session_event(
                            self._message_bus,
                            session_id,
                            event.model_dump(mode="json"),
                        )
                        await self._project_event(
                            user_id,
                            session_record,
                            agent_record,
                            event,
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
                    # Case B: continuation (UserConfirmResult
                    #  / ExternalExecResult)
                    reply_msg = await self._storage.get_message(
                        user_id,
                        session_id,
                        agent.state.reply_id,
                    )

                    if reply_msg is None:
                        logger.warning(
                            "Reply message %r not found in storage for "
                            "session %r; tool-call state changes from the "
                            "incoming event will not be persisted.",
                            agent.state.reply_id,
                            session_id,
                        )
                    elif input_msg:
                        reply_msg.append_event(input_msg)

                    async for event in agent.reply_stream(inputs=input_msg):
                        await publish_session_event(
                            self._message_bus,
                            session_id,
                            event.model_dump(mode="json"),
                        )
                        await self._project_event(
                            user_id,
                            session_record,
                            agent_record,
                            event,
                        )
                        if reply_msg is not None:
                            reply_msg.append_event(event)

                # Persist the reply Msg (upsert: overwrite if same id,
                # append if new).
                if reply_msg is not None:
                    await self._storage.upsert_message(
                        user_id,
                        session_id,
                        reply_msg,
                    )

                # Persist the updated agent state. MUST happen inside
                # the session lock: if we released the lock first,
                # another process could acquire it and load a stale
                # state from storage before this write lands.
                await self._storage.update_session_state(
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    state=agent.state,
                )
            finally:
                await self._message_bus.log_trim(events_key)

    async def _project_event(
        self,
        user_id: str,
        session_record: SessionRecord,
        agent_record: AgentRecord,
        event: AgentEvent,
    ) -> None:
        """Run every registered projector against one produced event.

        Each :class:`~agentscope.app._types.EventProjector` decides
        whether the event is relevant to its cross-session UI feed and,
        if so, mirrors it onto the owning session via the shared
        :class:`SessionProjection`. Projectors are independent: one
        failing must neither tear down the producing run nor block the
        others, so each call is guarded individually and its error
        logged. Adding a feed means adding a projector — no change here.

        Args:
            user_id (`str`):
                The owner user id.
            session_record (`SessionRecord`):
                The currently-running session's record.
            agent_record (`AgentRecord`):
                The currently-running agent's record.
            event (`AgentEvent`):
                The event just published to this session's channel.
        """
        for projector in self._projectors:
            try:
                await projector.maybe_project(
                    user_id,
                    session_record,
                    agent_record,
                    event,
                    self._projection,
                )
            except Exception as e:  # pylint: disable=broad-except
                logger.warning(
                    "Projector %s failed on event %s from session %s: %s",
                    type(projector).__name__,
                    type(event).__name__,
                    session_record.id,
                    str(e),
                )
