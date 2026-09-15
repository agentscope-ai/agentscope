# -*- coding: utf-8 -*-
"""Running a stored procedure.

A step here does not drive an agent itself — it hands the turn to
:class:`~._chat.ChatService`, which already owns everything a turn
needs: the session lock, the toolkit, event fan-out, message
persistence. What comes back is read out of the run state rather than
out of the reply, because the step's agent wrote it there through its
submit tool.
"""
import asyncio
from typing import Any, AsyncGenerator, Awaitable, Callable

from ..storage import (
    AgentVerifier,
    ChatModelConfig,
    HumanVerifier,
    SessionConfig,
    SOPOrigin,
    SOPRecord,
    SOPRunRecord,
    SOPStepDataV1,
    SOPWorkspaceGrain,
    StorageBase,
)
from ..message_bus import MessageBus, MessageBusKeys
from ..workspace_manager import WorkspaceManagerBase
from ..._logging import logger
from ._session import SessionService, SessionStatus
from ...event import (
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ...message import Msg, TextBlock, UserMsg
from ...sop import (
    SOP,
    SOPEngine,
    SOPPhase,
    SOPRunState,
    SOPStepBase,
    SOPStepRunState,
)
from ...permission import PermissionContext, PermissionMode
from ...state import AgentState

_PARKED = (
    SessionStatus.AWAITING_PERMISSION,
    SessionStatus.AWAITING_EXTERNAL_RESULT,
)


class SessionSOPStep(SOPStepBase):
    """One milestone, worked on in ordinary chat sessions.

    One call hands one turn to one session — the author's, or the
    reviewer's — and reads back what that turn filed. The half of the
    step being played is told by whether anything has been handed over
    yet, so the engine's own loop carries the step from working to being
    judged without either side knowing about the other.
    """

    def __init__(
        self,
        data: SOPStepDataV1,
        index: int,
        user_id: str,
        sop_run_id: str,
        sessions: dict[str, str],
        chat: Any,
        storage: StorageBase,
        persist: Callable[[], Awaitable[None]],
    ) -> None:
        """Bind the step to the run it belongs to.

        Args:
            data (`SOPStepDataV1`):
                The milestone as the procedure describes it.
            index (`int`):
                Its position, which is also its identity in the run.
            user_id (`str`):
                The owner user id.
            sop_run_id (`str`):
                The run this step is part of.
            sessions (`dict[str, str]`):
                The run's conversations, keyed the way the steps name
                them.
            chat (`ChatService`):
                Where a turn is actually taken.
            storage (`StorageBase`):
                Read back what the turn filed.
            persist (`Callable[[], Awaitable[None]]`):
                Writes the whole run state. Called before a turn is
                handed over, because the submit tool finds its step by
                reading which one the stored run says is running.
        """
        super().__init__(data.subject, data.description, data.max_attempts)
        self._data = data
        self._index = index
        self._user_id = user_id
        self._sop_run_id = sop_run_id
        self._sessions = sessions
        self._chat = chat
        self._storage = storage
        self._persist = persist

    async def reply_stream(  # pylint: disable=invalid-overridden-method
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None,
        state: SOPStepRunState,
    ) -> AsyncGenerator[Any, None]:
        """Hand one turn to one session and read back what it filed.

        Nothing is ever yielded: a step's agent publishes into its own
        session, which is where anyone watching a run is already
        looking. The empty loop is what makes this a generator at all.
        """
        for event in ():
            yield event

        verifier = self._data.verifier
        if state.submission is None:
            ref, opening = self._data.executor, self._brief(state)
        elif verifier is None:
            # A step that only had to happen.
            self.record(state, True)
            return
        elif isinstance(verifier, HumanVerifier):
            # Nobody to dispatch to: the verdict arrives as a write from
            # whoever is asked, and the run is picked up again after.
            state.phase = SOPPhase.AWAITING
            return
        else:
            ref, opening = verifier.agent, self._question(state, verifier)

        session_id = self._sessions[ref.session_key]
        state.phase = SOPPhase.RUNNING
        # The submit tool locates its step by reading which one the
        # stored run says is running, so this has to land first.
        await self._persist()

        # A fresh attempt is briefed; a resumption is the answer its
        # session is already waiting for, and goes through untouched.
        # The engine hands over ``given`` for the former, which the
        # briefing already carries.
        resuming = inputs is not None and not isinstance(inputs, (Msg, list))
        await self._chat.run(
            self._user_id,
            session_id,
            ref.agent_id,
            inputs if resuming else opening,
        )

        stored = await self._storage.get_sop_run(
            self._user_id,
            self._sop_run_id,
        )
        if stored is None:
            # Deleted mid-turn; there is nothing left to record against.
            state.phase = SOPPhase.FAILED
            return
        filed = stored.state.steps[self._index]
        state.submission = filed.submission
        verdicts = list(filed.verifications)

        if len(verdicts) > len(state.verifications):
            state.verifications = verdicts
            if verdicts[-1].passed:
                state.phase = SOPPhase.COMPLETED
            else:
                state.submission = None
                state.phase = SOPPhase.PENDING
            return

        if state.submission is not None:
            # Handed over; the same step is judged on the next pass.
            state.phase = SOPPhase.RUNNING
            return

        session = await self._storage.get_session(
            self._user_id,
            ref.agent_id,
            session_id,
        )
        if session is not None and (
            SessionService.derive_parked_status(session.state.context)
            in _PARKED
        ):
            state.phase = SOPPhase.AWAITING
            return

        self.record(
            state,
            False,
            "Your turn ended without submitting anything, so the step "
            "has nothing to show for it.",
            "sop",
        )

    def _brief(self, state: SOPStepRunState) -> list[Msg]:
        """What the author is asked at the start of an attempt."""
        text = (
            f"<system-reminder>You are running one step of the SOP.\n\n"
            f"## {self.subject}\n\n{self.description}\n"
        )
        if state.verifications:
            text += (
                f"\nYour last attempt was not accepted:\n"
                f"{state.verifications[-1].message}\n"
            )
        text += "</system-reminder>"
        return [UserMsg(name="sop", content=text), *state.given]

    def _question(
        self,
        state: SOPStepRunState,
        verifier: AgentVerifier,
    ) -> list[Msg]:
        """What the reviewer is asked, once there is something to judge."""
        text = (
            f"<system-reminder>Judge the work below against what this "
            f"step had to prove.\n\n## {self.subject}\n\n{self.description}"
        )
        if verifier.criteria:
            text += f"\n\n{verifier.criteria}"
        return [
            UserMsg(name="sop", content=text + "</system-reminder>"),
            *state.given,
            UserMsg(
                name="sop",
                content=[
                    TextBlock(type="text", text="<submission>"),
                    *(state.submission or []),
                    TextBlock(type="text", text="</submission>"),
                ],
            ),
        ]


class SOPService:
    """Start runs of a stored procedure, and carry them forward."""

    def __init__(
        self,
        storage: StorageBase,
        workspace_manager: WorkspaceManagerBase,
        message_bus: MessageBus,
        chat: Any,
    ) -> None:
        """Initialize the service.

        Args:
            storage (`StorageBase`):
                Application storage.
            workspace_manager (`WorkspaceManagerBase`):
                Assigns the workspace a run's conversations share.
            message_bus (`MessageBus`):
                Serialises advances of one run against each other.
            chat (`ChatService`):
                Where each step's turn is taken.
        """
        self._storage = storage
        self._workspace_manager = workspace_manager
        self._message_bus = message_bus
        self._chat = chat
        # asyncio holds only a weak reference to a running task, so a
        # detached advance has to be kept alive here or it can be
        # collected mid-step.
        self._advancing: set[asyncio.Task] = set()

    async def create_run(
        self,
        user_id: str,
        sop_record: SOPRecord,
        inputs: list[Msg] | None = None,
    ) -> SOPRunRecord:
        """Open a run: its conversations, its workspace, its record.

        Every conversation the procedure names is opened up front, so
        dispatching a step never has to ask whether one exists yet.

        Args:
            user_id (`str`):
                The owner user id.
            sop_record (`SOPRecord`):
                The procedure to run. Copied into the run, so editing it
                afterwards leaves this run alone.
            inputs (`list[Msg] | None`, optional):
                What the run is started with, read by its first step.

        Returns:
            `SOPRunRecord`:
                The opened run, already persisted.

        Raises:
            `KeyError`:
                If a step names a conversation the procedure never
                configured.
        """
        data = sop_record.data
        run = SOPRunRecord(
            user_id=user_id,
            sop_id=sop_record.id,
            definition=data,
            state=SOPRunState(
                inputs=inputs or [],
                steps=[SOPStepRunState() for _ in data.steps],
            ),
        )

        # Which agent each conversation belongs to. A key named twice
        # is one conversation, which is how a step says "the same one
        # again" — and why the settings hang off the key, not the step.
        agents: dict[str, str] = {}
        for step in data.steps:
            agents[step.executor.session_key] = step.executor.agent_id
            if isinstance(step.verifier, AgentVerifier):
                agents[
                    step.verifier.agent.session_key
                ] = step.verifier.agent.agent_id

        shared = await self._workspace_manager.assign_workspace_id(
            user_id=user_id,
            agent_id=data.steps[0].executor.agent_id if data.steps else "",
            session_id=run.id,
        )
        for key, agent_id in agents.items():
            settings = data.session_settings[key]
            fallback = settings.fallback_chat_model_config
            workspace_id = shared
            if data.workspace_grain is not SOPWorkspaceGrain.RUN:
                workspace_id = (
                    await self._workspace_manager.assign_workspace_id(
                        user_id=user_id,
                        agent_id=agent_id,
                        session_id=f"{run.id}:{key}",
                    )
                )
            session = await self._storage.upsert_session(
                user_id=user_id,
                agent_id=agent_id,
                config=SessionConfig(
                    workspace_id=workspace_id,
                    name=f"{data.name} / {key}",
                    chat_model_config=ChatModelConfig(
                        **settings.chat_model_config,
                    ),
                    fallback_chat_model_config=(
                        ChatModelConfig(**fallback) if fallback else None
                    ),
                ),
                state=AgentState(
                    permission_context=PermissionContext(
                        mode=PermissionMode(settings.permission_mode),
                    ),
                ),
                origin=SOPOrigin(sop_run_id=run.id, session_key=key),
            )
            run.sessions[key] = session.id

        return await self._storage.upsert_sop_run(user_id, run)

    def advance_later(self, user_id: str, sop_run_id: str) -> None:
        """Set a run going without waiting for it to stop again.

        A run is many chat turns long — often the whole rest of the
        procedure — so whatever asked for this (a request handler, a
        finished chat turn) must not be held open for it. Failures are
        logged: there is no caller left to raise at.

        Args:
            user_id (`str`):
                The owner user id.
            sop_run_id (`str`):
                The run to carry on.
        """

        async def _run() -> None:
            try:
                await self.run(user_id, sop_run_id)
            except Exception:  # pylint: disable=broad-except
                logger.exception(
                    "Advancing SOP run %r failed.",
                    sop_run_id,
                )

        task = asyncio.create_task(_run(), name=f"sop-run:{sop_run_id}")
        self._advancing.add(task)
        task.add_done_callback(self._advancing.discard)

    async def run(
        self,
        user_id: str,
        sop_run_id: str,
        inputs: UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> SOPRunState:
        """Carry a run as far as it goes this time.

        Returns when the run finishes, gives up, or stops for someone —
        a person to answer a step's question, or a tool call to be
        approved in one of its sessions.

        Args:
            user_id (`str`):
                The owner user id.
            sop_run_id (`str`):
                The run to drive.
            inputs (optional):
                The answer a parked session was waiting for, passed
                through to whichever step is holding it.

        Returns:
            `SOPRunState`:
                How the run stands now.

        Raises:
            `KeyError`:
                If the user has no such run.
        """
        async with self._message_bus.acquire_lock(
            MessageBusKeys.sop_run_lock(sop_run_id),
            ttl_secs=MessageBusKeys.SOP_RUN_TTL_SECS,
        ):
            return await self._advance(user_id, sop_run_id, inputs)

    async def _advance(
        self,
        user_id: str,
        sop_run_id: str,
        inputs: UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None,
    ) -> SOPRunState:
        """Drive the run, with its lock already held."""
        record = await self._storage.get_sop_run(user_id, sop_run_id)
        if record is None:
            raise KeyError(f"SOP run {sop_run_id!r} not found.")

        state = record.state

        async def _persist() -> None:
            await self._storage.update_sop_run(user_id, sop_run_id, state)

        engine = SOPEngine(
            SOP(
                name=record.definition.name,
                description=record.definition.description,
                steps=[
                    SessionSOPStep(
                        data=step,
                        index=index,
                        user_id=user_id,
                        sop_run_id=sop_run_id,
                        sessions=record.sessions,
                        chat=self._chat,
                        storage=self._storage,
                        persist=_persist,
                    )
                    for index, step in enumerate(record.definition.steps)
                ],
            ),
            state,
        )
        async for _ in engine.reply_stream(inputs):
            pass
        await _persist()
        return engine.state
