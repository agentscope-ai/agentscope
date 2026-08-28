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
import asyncio
import inspect
import json
from dataclasses import dataclass
from typing import Literal, TYPE_CHECKING

from fastapi import HTTPException

from .._bus_ops import (
    abandon_inbox_consumer,
    deliver_to_inbox,
    enqueue_run_trigger,
    has_pending_inbox_or_release,
    publish_session_event,
    register_inbox_consumer,
)
from ..message_bus import MessageBus, MessageBusKeys
from ..rag.knowledge_base_manager import KnowledgeBaseManagerBase
from ..channel import ChatKind
from ..storage import StorageBase, AgentRecord, SessionRecord, SessionSource
from ..storage._utils import _resolve_team_leader
from .._manager import BackgroundTaskManager, SchedulerManager
from ..workspace_manager import WorkspaceManagerBase
from ..middleware import (
    InboxMiddleware,
    StateChangeMiddleware,
    ToolOffloadMiddleware,
    TeamMemberLoopMiddleware,
)
from ...middleware import TTSMiddleware, RAGMiddleware
from ...rag import KnowledgeBase
from .._types import (
    AgentMiddlewareFactory,
    AgentToolFactory,
    EventProjector,
    SubAgentTemplate,
)
from ._access import ResourceAccessService
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
    ReplyEndEvent,
    ReplyFinishedReason,
    UserConfirmResultEvent,
    ExternalExecutionResultEvent,
    UserInterruptEvent,
)
from ._errors import _classify_error, _classify_setup_error
from ..._utils._common import _generate_id
from ...message import AssistantMsg, HintBlock, Msg, ToolCallState
from ...permission import AdditionalWorkingDirectory

if TYPE_CHECKING:
    from ..channel import ChannelClients


@dataclass(frozen=True)
class _LeaderContext:
    """This session leads its team."""

    role: Literal["leader"] = "leader"


@dataclass(frozen=True)
class _WorkerContext:
    """This session is a worker; its leader is fully resolved."""

    leader_session_id: str
    leader_agent_id: str
    leader_name: str
    role: Literal["worker"] = "worker"


_TeamContext = _LeaderContext | _WorkerContext


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
        resource_access_service: ResourceAccessService,
        knowledge_base_manager: KnowledgeBaseManagerBase | None = None,
        extra_agent_middlewares: AgentMiddlewareFactory | None = None,
        extra_agent_tools: AgentToolFactory | None = None,
        custom_subagent_templates: dict[str, SubAgentTemplate] | None = None,
        custom_agent_cls: type[Agent] | None = None,
        extra_projectors: list[EventProjector] | None = None,
        channel_clients: "ChannelClients | None" = None,
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
            resource_access_service (`ResourceAccessService`):
                Resolves cross-owner resources at runtime. Agent
                assembly and model / TTS construction all route
                through this service so shared credentials, agents,
                and knowledge bases work uniformly.
            knowledge_base_manager (`KnowledgeBaseManagerBase | None`, \
             optional):
                The application's knowledge base manager.  When
                provided and the session config carries a
                ``knowledge_config``, a
                :class:`~agentscope.middleware.RAGMiddleware`
                is attached to the agent at run time.  ``None``
                disables knowledge-base wiring even for sessions that
                have one configured.
            extra_agent_middlewares (`AgentMiddlewareFactory | None`, \
             optional):
                Async factory invoked at every chat turn to produce
                user/session-specific middlewares to attach to the agent.
                Called as ``(user_id, agent_id, session_id, workspace)``,
                falling back to the legacy three-argument call when the
                factory does not accept ``workspace``.
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
            channel_clients (`ChannelClients | None`, optional):
                Factory for unconnected channel instances, used to give
                a channel-originated session's agent that channel's
                platform tools and chat context. Neither needs the long
                connection, so the run gets them wherever it lands.
        """
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._scheduler_manager = scheduler_manager
        self._background_task_manager = background_task_manager
        self._message_bus = message_bus
        self._access = resource_access_service
        self._knowledge_base_manager = knowledge_base_manager
        self._extra_agent_middlewares = extra_agent_middlewares
        # Probed once (the service is built once per lifespan) so legacy
        # three-argument factories keep working without the per-turn cost.
        self._middlewares_take_workspace = False
        if extra_agent_middlewares is not None:
            try:
                inspect.signature(extra_agent_middlewares).bind(
                    "",
                    "",
                    "",
                    None,
                )
                self._middlewares_take_workspace = True
            except (TypeError, ValueError):
                pass
        self._extra_agent_tools = extra_agent_tools
        self._channel_clients = channel_clients
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
        | UserInterruptEvent
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
        down its trigger (HTTP request task, wakeup dispatcher, …);
        reporting them to the client is :meth:`_run_impl`'s job, which
        it does for every phase of the run.

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
                - ``UserInterruptEvent``: abort a parked reply — the
                  agent closes pending tool calls with interrupted
                  results and ends the reply (Case B, no reasoning).
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

    async def _close_failed_reply(
        self,
        session_id: str,
        reply_msg: Msg,
        error: Exception,
    ) -> None:
        """Close a reply that died mid-stream, and say why.

        The stream never emitted its terminating ``ReplyEndEvent``, so
        one is synthesized: publishing it stops the live SSE spinner,
        and appending it gives the persisted reply a finished_at, reason
        and error for anyone who refreshes.

        Nothing is reported when the agent already closed the reply and
        the failure is downstream (publish, projection): overwriting a
        completed reply with an error would tell the user their answer
        failed when it did not.

        Args:
            session_id (`str`):
                The session the reply belongs to.
            reply_msg (`Msg`):
                The reply in flight.
            error (`Exception`):
                What killed the stream.
        """
        if reply_msg.finished_reason is not None:
            logger.exception(
                "Post-reply failure for session %r; the reply itself "
                "completed.",
                session_id,
            )
            return

        end_event = ReplyEndEvent(
            session_id=session_id,
            reply_id=reply_msg.id,
            finished_reason=ReplyFinishedReason.ERROR,
            error=_classify_error(error),
        )
        reply_msg.append_event(end_event)
        await publish_session_event(
            self._message_bus,
            session_id,
            end_event.model_dump(mode="json"),
        )
        logger.exception(
            "Reply failed for session %r; reported to the client as %s.",
            session_id,
            end_event.error.type if end_event.error else "error",
        )

    async def _notify_leader_of_failure(
        self,
        user_id: str,
        team_ctx: "_TeamContext | None",
        worker_name: str,
        finished_reason: ReplyFinishedReason,
        detail: str,
    ) -> None:
        """Tell a worker's team leader that the worker stopped early.

        A member reports through ``TeamSay``; a member that failed or
        was interrupted never got there, so without this the leader
        waits forever for an answer that is not coming. Delivered as a
        system reminder in the leader's inbox — not as a team message,
        because the member did not say this, the framework did — waking
        the leader when it is idle.

        Best-effort: a failure to notify is logged and dropped rather
        than replacing the failure being reported.

        Args:
            user_id (`str`):
                The owner of both sessions.
            team_ctx (`_TeamContext | None`):
                The run's team identity. Anything but a
                :class:`_WorkerContext` is a no-op — a leader or a
                team-less session has nobody to report to.
            worker_name (`str`):
                The failing member's display name, as the leader knows
                it from the roster.
            finished_reason (`ReplyFinishedReason`):
                How the reply ended, which decides what the leader is
                asked to consider.
            detail (`str`):
                One-line, already-sanitized explanation of the error.
        """
        if not isinstance(team_ctx, _WorkerContext):
            return

        # Error messages come from several sources and only some end in
        # punctuation; the reminder reads as prose either way.
        detail = detail.strip()
        if detail and detail[-1] not in ".!?":
            detail += "."

        if finished_reason == ReplyFinishedReason.INTERRUPTED:
            reminder = (
                f"Team member {worker_name!r} was interrupted mid-task and "
                f"has stopped. Someone cancelled that run deliberately — "
                f"possibly the user, who may be working with this member "
                f"directly. Do not silently re-dispatch it: ask the user "
                f"what should happen to the task, or ask the member how "
                f"far it got. Left unresolved, the member is stranded with "
                f"work nobody is tracking."
            )
        else:
            reminder = (
                f"Team member {worker_name!r} hit an error while running, "
                f"so it never called TeamSay to report. Error: {detail} "
                f"Judge from the error type whether to retry — with this "
                f"member or a fresh one — or to raise it with the user: "
                f"invalid credentials or an exhausted quota will fail the "
                f"same way again. Assume no usable output unless the "
                f"member reported partial results earlier."
            )

        try:
            hint = HintBlock(
                hint=f"<system-reminder>{reminder}</system-reminder>",
                source=json.dumps(
                    {"label": "System", "sublabel": "Reminder"},
                    ensure_ascii=False,
                ),
            )
            await deliver_to_inbox(
                self._message_bus,
                user_id=user_id,
                session_id=team_ctx.leader_session_id,
                agent_id=team_ctx.leader_agent_id,
                payload=hint.model_dump(mode="json"),
            )
        except Exception:  # pylint: disable=broad-except
            logger.exception(
                "Failed to notify team leader %r that member %r stopped.",
                team_ctx.leader_name,
                worker_name,
            )

    async def _report_failure(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        error: Exception,
        team_ctx: "_TeamContext | None" = None,
        worker_name: str | None = None,
    ) -> None:
        """Tell the client about a failure that reached no reply.

        The caller is responsible for holding the session lock: these
        events go on the same channel as a real reply's, so publishing
        them unserialised would interleave a "reply failed" into an
        answer another run is streaming. It is not taken here because
        one call site already holds it.

        Publishes a start/end pair so the failure lands the same way a
        mid-reply one does — the UI has exactly one shape for "a reply
        failed", and a stream that merely stops is not it. The pair is
        persisted too, so the failure survives a refresh rather than
        vanishing from the transcript.

        Best-effort by construction: this runs on a path that has
        already failed, so its own failure is logged and dropped rather
        than replacing the error the caller is reporting.

        Args:
            user_id (`str`):
                Authenticated caller's user ID.
            session_id (`str`):
                The session whose run failed.
            agent_id (`str`):
                The agent that was being assembled.
            error (`Exception`):
                What went wrong while setting the run up.
            team_ctx (`_TeamContext | None`, optional):
                The run's team identity; a worker's leader is told the
                run never happened.
            worker_name (`str | None`, optional):
                Display name used in that notification. Defaults to
                ``agent_id`` when the agent record never loaded.
        """
        try:
            reply_id = _generate_id()
            start_event = ReplyStartEvent(
                session_id=session_id,
                reply_id=reply_id,
                name=agent_id,
            )
            end_event = ReplyEndEvent(
                session_id=session_id,
                reply_id=reply_id,
                finished_reason=ReplyFinishedReason.ERROR,
                error=_classify_setup_error(error),
            )

            reply_msg = AssistantMsg(
                id=reply_id,
                name=agent_id,
                content=[],
            )
            for event in (start_event, end_event):
                reply_msg.append_event(event)
                await publish_session_event(
                    self._message_bus,
                    session_id,
                    event.model_dump(mode="json"),
                )
            await self._storage.upsert_message(
                user_id,
                session_id,
                reply_msg,
            )
        except Exception:
            logger.exception(
                "Failed to report a failure for session %r; the original "
                "error is logged above.",
                session_id,
            )

        await self._notify_leader_of_failure(
            user_id,
            team_ctx,
            worker_name or agent_id,
            ReplyFinishedReason.ERROR,
            _classify_setup_error(error).message,
        )

    async def interrupt(
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
    ) -> None:
        """Interrupt an in-progress reply for a session.

        Two paths, chosen by session liveness:

        - **Running** (lock held): publish on the interrupt channel so
          the local :class:`~agentscope.app._manager.CancelDispatcher`
          cancels its chat-run task; the agent's ``CancelledError``
          cleanup runs (fake tool results for pending calls, fallback
          message, ``ReplyEndEvent(INTERRUPTED)``).
        - **Not running**: enqueue a ``resume`` trigger carrying a
          :class:`UserInterruptEvent`. If the session is parked on
          HITL, the agent short-circuits into the same cleanup path;
          if it is idle, the agent silently no-ops. Callers do not
          need to distinguish the two — the operation is idempotent.

        Args:
            user_id (`str`):
                Authenticated caller's user id.
            session_id (`str`):
                Target session id.
            agent_id (`str`):
                Agent that owns the session.

        Raises:
            LookupError:
                The session does not exist.
        """
        session = await self._storage.get_session(
            user_id,
            agent_id,
            session_id,
        )
        if session is None:
            raise LookupError(f"Session '{session_id}' not found.")

        if await self._message_bus.is_locked(
            MessageBusKeys.session_lock(session_id),
        ):
            await self._message_bus.publish(
                MessageBusKeys.session_interrupt_channel(),
                {"session_id": session_id},
            )
            return

        await enqueue_run_trigger(
            self._message_bus,
            user_id=user_id,
            session_id=session_id,
            agent_id=agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_RESUME,
            inputs=UserInterruptEvent(reply_id=session.state.reply_id),
        )

    @staticmethod
    def _skip_parked_wakeup(
        session_id: str,
        agent: Agent,
        input_msg: object,
    ) -> bool:
        """Whether a wake-up should be dropped rather than run.

        Wake-ups deliver pending inbox content (team messages, etc.) by
        poking the dispatcher to run the session with ``input_msg=None``.
        If the agent is parked on an ``ASKING`` or ``SUBMITTED`` tool
        call — waiting for user confirmation or external-execution
        results — another ``None`` run would hit
        :meth:`Agent._check_incoming_event`, which rightly rejects
        ``None`` when there is something to confirm, and fail noisily.

        The inbox content is safe to leave queued: whenever the user does
        confirm (or the external result lands), the resuming run's next
        reasoning step lets :class:`InboxMiddleware` drain it naturally.

        Args:
            session_id (`str`):
                The session being woken, for the log line.
            agent (`Agent`):
                The assembled agent, whose context is inspected.
            input_msg (`object`):
                The run's input; only ``None`` marks a wake-up.

        Returns:
            `bool`:
                ``True`` when the run should be skipped.
        """
        if input_msg is not None or not agent.state.context:
            return False

        last_msg = agent.state.context[-1]
        if last_msg.role != "assistant" or last_msg.name != agent.name:
            return False

        awaiting = [
            tc
            for tc in last_msg.get_content_blocks("tool_call")
            if tc.state in (ToolCallState.ASKING, ToolCallState.SUBMITTED)
        ]
        if not awaiting:
            return False

        logger.info(
            "Skipping wake-up for session %s: agent is parked on %d "
            "awaiting tool call(s); inbox messages will be drained when "
            "the agent resumes.",
            session_id,
            len(awaiting),
        )
        return True

    async def _run_impl(
        # pylint: disable=too-many-statements,too-many-branches
        self,
        user_id: str,
        session_id: str,
        agent_id: str,
        input_msg: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | ExternalExecutionResultEvent
        | UserInterruptEvent
        | None,
    ) -> None:
        """The actual chat-run body; wrapped by :meth:`run` for error
        swallowing. Separated so the try/except doesn't bury the
        per-step logic at one extra indentation level.

        The whole body runs under the distributed session lock: the mutable
        session record must be loaded only after this run owns the lock,
        otherwise a waiter can assemble an agent from a snapshot that the
        preceding holder replaces before releasing the lock."""

        async with self._message_bus.acquire_lock(
            MessageBusKeys.session_lock(session_id),
            ttl_secs=MessageBusKeys.SESSION_RUN_TTL_SECS,
        ):
            # Bound before the try: an assembly failure reports through
            # them, and may happen before either is resolved.
            team_ctx: _TeamContext | None = None
            worker_name: str = agent_id

            # Steps 1-6 assemble the run; step 7 performs it. A failure
            # here has no reply to attach to, so one is synthesized —
            # otherwise the client sees a stream that simply stops and is
            # left saying "unknown error".
            try:
                # -------------------------------------------------------------
                # 1. Load records + resolve workspace ONCE here, reused below.
                # Reject missing records up front with a clear error so the
                # downstream assembly code can rely on non-None values.
                #
                # ``resolve_agent`` covers own agents (including team workers,
                # which the owner runs directly) and cross-owner shared agents
                # (viewer runs a shared user-source agent). It raises 404 when
                # the agent is not visible to the caller.
                # -------------------------------------------------------------
                try:
                    agent_record = await self._access.resolve_agent(
                        user_id,
                        agent_id,
                    )
                except HTTPException as exc:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Agent {agent_id!r} not found.",
                    ) from exc
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
                worker_name = agent_record.data.name

                # -------------------------------------------------------------
                # 1b. Resolve the team identity ONCE, before anything that
                # can fail: a worker whose assembly dies still has to reach
                # its leader. Downstream consumers reuse this.
                # -------------------------------------------------------------
                if session_record.team_id is not None:
                    team = await self._storage.get_team(
                        user_id,
                        session_record.team_id,
                    )
                    if team is None:
                        # Dissolved concurrently, or a stale team_id:
                        # nothing to be a member of, so run teamless.
                        logger.warning(
                            "Session %r points at team %r which no longer "
                            "exists; running teamless.",
                            session_id,
                            session_record.team_id,
                        )
                    elif team.session_id == session_record.id:
                        team_ctx = _LeaderContext()
                    else:
                        leader = await _resolve_team_leader(
                            self._storage,
                            user_id,
                            team,
                        )
                        if leader is None:
                            # Unreachable while the data is consistent:
                            # deleting a leader dissolves the team.
                            raise HTTPException(
                                status_code=409,
                                detail=(
                                    f"Team {team.id!r} has no resolvable "
                                    f"leader; worker session "
                                    f"{session_id!r} cannot run."
                                ),
                            )
                        team_ctx = _WorkerContext(
                            leader_session_id=leader.session_id,
                            leader_agent_id=leader.agent.id,
                            leader_name=leader.name,
                        )

                workspace = await self._workspace_manager.get_workspace(
                    user_id,
                    agent_id,
                    session_id,
                    session_record.config.workspace_id,
                )

                # Add workspace working directory to the permission context
                working_dirs = (
                    session_record.state.permission_context.working_directories
                )
                if workspace.workdir not in working_dirs:
                    working_dirs[
                        workspace.workdir
                    ] = AdditionalWorkingDirectory(
                        path=workspace.workdir,
                        source="session",
                    )

                # -------------------------------------------------------------
                # 1c. Resolve the channel binding ONCE; the toolkit and the
                # system-prompt attachment share it.
                # -------------------------------------------------------------
                channel = (
                    await self._channel_clients.get(
                        session_record.source_channel_id,
                    )
                    if session_record.source_channel_id
                    and self._channel_clients is not None
                    else None
                )
                channel_tools = (
                    await channel.list_tools(workspace)
                    if channel is not None
                    else []
                )

                # -------------------------------------------------------------
                # 2. Middlewares — framework-supplied first, then caller
                # extras. Background-tool completions deliver their results
                # via ``message_bus.inbox_push + enqueue_wakeup``, so the
                # dispatcher (any process) wakes an idle session — no
                # in-process retrigger plumbing is needed here.
                # -------------------------------------------------------------
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
                # Equip the member middleware for loop control
                if isinstance(team_ctx, _WorkerContext):
                    middlewares.append(
                        TeamMemberLoopMiddleware(
                            leader_name=team_ctx.leader_name,
                        ),
                    )

                if self._extra_agent_middlewares is not None:
                    factory_args: tuple = (user_id, agent_id, session_id)
                    if self._middlewares_take_workspace:
                        factory_args += (workspace,)
                    middlewares.extend(
                        await self._extra_agent_middlewares(*factory_args),
                    )

                # -------------------------------------------------------------
                # 2b. TTS middleware — inject when the session has a TTS
                # config.
                # -------------------------------------------------------------
                tts_cfg = session_record.config.tts_model_config
                if tts_cfg is not None:
                    tts_model = await get_tts_model(
                        user_id,
                        tts_cfg,
                        self._access,
                    )
                    middlewares.append(TTSMiddleware(tts_model))

                # -------------------------------------------------------------
                # 2c. Knowledge-base middleware — inject when the session has
                # KBs attached.  Each KB resolves to its own
                # :class:`KnowledgeBase` handle
                # (own embedding model + vector store), so the middleware can
                # retrieve across heterogeneous KBs in one fan-out.
                #
                # Each KB may be either owned by the caller or shared to them
                # via the resource access policy. We resolve the owner through
                # ``resolve_knowledge_base`` first and hand the KB manager the
                # true owner id — its own storage lookups stay owner-scoped
                # and unaware of sharing.
                # -------------------------------------------------------------
                kb_cfg = session_record.config.knowledge_config
                if (
                    kb_cfg is not None
                    and kb_cfg.knowledge_base_ids
                    and self._knowledge_base_manager is not None
                ):
                    knowledges: list[KnowledgeBase] = []
                    for kb_id in kb_cfg.knowledge_base_ids:
                        try:
                            kb_record = (
                                await self._access.resolve_knowledge_base(
                                    user_id,
                                    kb_id,
                                )
                            )
                            kb_manager = self._knowledge_base_manager
                            knowledge = await kb_manager.get_knowledge(
                                kb_record.user_id,
                                kb_id,
                            )
                        except Exception:  # pylint: disable=broad-except
                            # A KB the session referenced was deleted, its
                            # sharing revoked, or its credential is gone —
                            # log and skip so the chat turn can still run
                            # with the remaining KBs.
                            logger.exception(
                                "Skipping knowledge base %r for session %r: "
                                "failed to resolve runtime handle.",
                                kb_id,
                                session_id,
                            )
                            continue
                        knowledges.append(knowledge)
                    if knowledges:
                        middlewares.append(
                            RAGMiddleware(
                                knowledge_bases=knowledges,
                                parameters=RAGMiddleware.Parameters(
                                    **(kb_cfg.parameters or {}),
                                ),
                            ),
                        )

                # -------------------------------------------------------------
                # 3. Toolkit (workspace tools + planning + ToolStop +
                # schedule + team + extras + skills + mcps).
                # -------------------------------------------------------------
                toolkit = await get_toolkit(
                    storage=self._storage,
                    workspace=workspace,
                    workspace_manager=self._workspace_manager,
                    scheduler_manager=self._scheduler_manager,
                    background_task_manager=self._background_task_manager,
                    message_bus=self._message_bus,
                    middlewares=middlewares,
                    user_id=user_id,
                    agent_record=agent_record,
                    session_record=session_record,
                    resource_access_service=self._access,
                    extra_factory=self._extra_agent_tools,
                    sub_agent_templates=self._sub_agent_templates,
                    team_role=team_ctx.role if team_ctx else None,
                    channel_tools=channel_tools,
                )

                # -------------------------------------------------------------
                # 4. Model + fallback (resolved from session's config).
                # -------------------------------------------------------------
                model_cfg = session_record.config.chat_model_config
                if not model_cfg:
                    raise HTTPException(
                        status_code=404,
                        detail=(
                            f"No model configuration found for agent "
                            f"{agent_id}"
                        ),
                    )
                model = await get_model(user_id, model_cfg, self._access)

                fallback_cfg = session_record.config.fallback_chat_model_config
                fallback_model = (
                    await get_model(user_id, fallback_cfg, self._access)
                    if fallback_cfg is not None
                    else None
                )

                # -------------------------------------------------------------
                # 5. Assemble the Agent.
                # -------------------------------------------------------------
                attachment = f"You're within a session (id={session_id})."

                # Channel-bound sessions: tell the agent which chat it serves.
                if channel is not None:
                    tools = ", ".join(t.name for t in channel_tools)
                    chat_id = session_record.source_chat_id or ""
                    kind = await channel.chat_kind(chat_id)
                    name = (
                        session_record.source_chat_name
                        or await channel.chat_name(chat_id)
                    )
                    where = f' named "{name}"' if name else ""
                    attachment += (
                        f" This session is bound to a chat{where} (id "
                        f"{chat_id!r}) on the {channel.display_name} "
                        f"platform: the messages, images and files people "
                        f"send there are relayed to you here, and your "
                        f"replies are delivered back to that same chat."
                    )
                    if kind is ChatKind.GROUP:
                        attachment += (
                            " It is a group chat, so messages may come "
                            "from several different people; each incoming "
                            "user turn is labelled with its sender."
                        )
                    elif kind is ChatKind.PRIVATE:
                        attachment += (
                            " It is a one-to-one private chat with a "
                            "single user."
                        )
                    if tools:
                        attachment += (
                            f" You also have these {channel.display_name} "
                            f"tools available: {tools}. Pass this chat's id "
                            f"as their target to act on this chat."
                        )

                attachment = (
                    f"<system-notification>{attachment}</system-notification>"
                )
                system_prompt = (
                    agent_record.data.system_prompt + "\n\n" + attachment
                )

                agent_state = session_record.state
                agent_state.session_id = session_id
                agent = self._agent_cls(
                    name=agent_record.data.name,
                    system_prompt=system_prompt,
                    model=model,
                    toolkit=toolkit,
                    model_config=ModelConfig(fallback_model=fallback_model),
                    context_config=agent_record.data.context_config,
                    react_config=agent_record.data.react_config,
                    state=agent_state,
                    middlewares=middlewares,
                    offloader=workspace,
                )

                if self._skip_parked_wakeup(session_id, agent, input_msg):
                    return
            except Exception as e:  # pylint: disable=broad-except
                # Under the session lock, like the reply this run never got
                # to make: these events share a channel with a live reply's,
                # so publishing them unserialised would drop a "reply failed"
                # into the middle of an answer another run is streaming.
                await self._report_failure(
                    user_id,
                    session_id,
                    agent_id,
                    e,
                    team_ctx,
                    worker_name,
                )
                return

            # ----------------------------------------------------------------
            # 7. Run the agent (still under the session lock)
            # -----------------------------------------------------------------
            events_key = MessageBusKeys.session_events(session_id)
            # Channel-bound run: start streaming the reply back to the
            # platform chat. Delivery is plain REST, so this node does it
            # rather than handing the run to whichever one holds the
            # channel's connection. Covers scheduled / background wakes,
            # not just inbound channel messages.
            if (
                session_record.source == SessionSource.CHANNEL
                and session_record.source_channel_id
                and session_record.source_chat_id
                and self._channel_clients is not None
            ):
                await self._channel_clients.deliver(
                    session_id=session_id,
                    channel_id=session_record.source_channel_id,
                    chat_id=session_record.source_chat_id,
                    agent_id=agent_id,
                )
            reply_msg: Msg | None = None
            reply_msgs: list[Msg] = []
            released = False
            await register_inbox_consumer(self._message_bus, session_id)
            try:
                while True:
                    reply_msg = None
                    try:
                        if input_msg is None or isinstance(
                            input_msg,
                            (Msg, list),
                        ):
                            # Case A: new reply (user message(s), or
                            # retrigger with empty input)
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

                            async for event in agent.reply_stream(
                                inputs=input_msg,
                            ):
                                # Apply to reply_msg FIRST (sync — never
                                # interrupted), so an interrupt in the
                                # awaits below can't lose this event.
                                if isinstance(event, ReplyStartEvent):
                                    reply_msg = AssistantMsg(
                                        id=event.reply_id,
                                        name=event.name,
                                        content=[],
                                    )
                                elif reply_msg is not None:
                                    reply_msg.append_event(event)
                                try:
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
                                except asyncio.CancelledError:
                                    # Interrupt landed here, not at
                                    # ``__anext__``. Re-arm it so it is
                                    # redelivered into the agent at the
                                    # next ``__anext__`` (which runs its
                                    # interruption cleanup) instead of
                                    # abandoning the generator and
                                    # dropping that cleanup.
                                    current = asyncio.current_task()
                                    if current is not None:
                                        current.cancel()

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
                                    "Reply message %r not found in "
                                    "storage for session %r; tool-call "
                                    "state changes from the incoming "
                                    "event will not be persisted.",
                                    agent.state.reply_id,
                                    session_id,
                                )
                            elif input_msg:
                                reply_msg.append_event(input_msg)

                            # Broadcast the applied decision so
                            # observers that didn't make it (other tabs,
                            # the channel card) close it.
                            if isinstance(
                                input_msg,
                                (
                                    UserConfirmResultEvent,
                                    ExternalExecutionResultEvent,
                                ),
                            ):
                                await publish_session_event(
                                    self._message_bus,
                                    session_id,
                                    input_msg.model_dump(mode="json"),
                                )

                            # Emit a synthetic REPLY_START so SSE subscribers
                            # (frontend, channel gateway) can detect the
                            # continuation without requiring special handling.
                            #
                            # IMPORTANT: The frontend SSE handler must
                            # NOT clear its accumulated message buffer
                            # upon receiving a
                            # REPLY_START with the same reply_id as the current
                            # message. This event signals a continuation (e.g.
                            # after an approval flow), not a fresh reply.
                            continuation_start = ReplyStartEvent(
                                session_id=session_id,
                                reply_id=agent.state.reply_id,
                                name=agent_record.data.name,
                            )
                            await publish_session_event(
                                self._message_bus,
                                session_id,
                                continuation_start.model_dump(mode="json"),
                            )

                            async for event in agent.reply_stream(
                                inputs=input_msg,
                            ):
                                # Apply to the persisted reply FIRST
                                # (synchronous), then publish/project —
                                # see Case A above.
                                if reply_msg is not None:
                                    reply_msg.append_event(event)
                                try:
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
                                except asyncio.CancelledError:
                                    # See Case A: redirect an interrupt
                                    # landing here back into the agent
                                    # via the next ``__anext__``.
                                    current = asyncio.current_task()
                                    if current is not None:
                                        current.cancel()

                    except Exception as e:  # pylint: disable=broad-except
                        # CancelledError is a BaseException, so interrupts are
                        # unaffected. The lock is already held here, so the
                        # reporter is called directly.
                        if reply_msg is None:
                            # Failed before REPLY_START: nothing to close, so a
                            # fresh reply carries the failure instead.
                            await self._report_failure(
                                user_id,
                                session_id,
                                agent_id,
                                e,
                                team_ctx,
                                worker_name,
                            )
                        else:
                            await self._close_failed_reply(
                                session_id,
                                reply_msg,
                                e,
                            )

                        # A failed turn stops the run; whatever is still
                        # queued is handed to a fresh one by the
                        # ``released`` cleanup below.
                        if reply_msg is not None:
                            reply_msgs.append(reply_msg)
                        break

                    if reply_msg is not None:
                        reply_msgs.append(reply_msg)

                    # Still holding the session lock: anything that
                    # landed in the inbox after this turn's last drain
                    # is this run's to handle — no producer will have
                    # woken the session while it was registered as the
                    # consumer.
                    if not await has_pending_inbox_or_release(
                        self._message_bus,
                        session_id,
                    ):
                        released = True
                        break
                    input_msg = None

            finally:
                # An interrupt unwinds past the loop's own exit check, so
                # the run may still be registered as the inbox consumer.
                # Hand the registration back and wake the session when
                # payloads are still queued, otherwise they would wait
                # for an unrelated trigger.
                if not released:
                    await abandon_inbox_consumer(
                        self._message_bus,
                        user_id=user_id,
                        session_id=session_id,
                        agent_id=agent_id,
                    )

                # The last turn's reply is only in ``reply_msgs`` when the
                # loop reached its own bookkeeping — an interrupt lands
                # here instead, so add it now.
                if reply_msg is not None and (
                    not reply_msgs or reply_msgs[-1] is not reply_msg
                ):
                    reply_msgs.append(reply_msg)

                # All persistence in a single coroutine, shielded from
                # outer cancellation.  Must complete BEFORE the session
                # lock is released — otherwise another worker could
                # acquire the lock and load a stale state from storage
                # before this write lands.
                async def _persist() -> None:
                    try:
                        for msg in reply_msgs:
                            await self._storage.upsert_message(
                                user_id,
                                session_id,
                                msg,
                            )
                        await self._storage.update_session_state(
                            user_id=user_id,
                            agent_id=agent_id,
                            session_id=session_id,
                            state=agent.state,
                        )
                        await self._message_bus.log_trim(events_key)
                    finally:
                        # A worker whose turn died never reached
                        # ``TeamSay``. Sent from inside the shielded
                        # write so an interrupt cannot drop it, and from
                        # a ``finally`` so a storage failure cannot
                        # either — the leader has to learn about the
                        # dead turn even when recording it went wrong.
                        # COMPLETED / EXCEED_MAX_ITERS stay silent:
                        # those only escape the member middleware after
                        # a successful report.
                        for msg in reply_msgs:
                            if msg.finished_reason in (
                                ReplyFinishedReason.ERROR,
                                ReplyFinishedReason.INTERRUPTED,
                            ):
                                await self._notify_leader_of_failure(
                                    user_id,
                                    team_ctx,
                                    worker_name,
                                    msg.finished_reason,
                                    msg.error.message
                                    if msg.error
                                    else "The turn stopped before it "
                                    "finished.",
                                )

                persist_task = asyncio.create_task(_persist())
                try:
                    await asyncio.shield(persist_task)
                except asyncio.CancelledError:
                    # Await the shielded task so the lock is only
                    # released after storage is consistent, then
                    # propagate to honour asyncio semantics.
                    await persist_task
                    raise

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
