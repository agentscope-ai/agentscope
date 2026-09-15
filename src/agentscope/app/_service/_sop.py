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
from typing import Any, AsyncGenerator, Awaitable, Callable, Self

from ..storage import (
    AgentVerifier,
    ChatModelConfig,
    HumanVerifier,
    SessionConfig,
    SOPOrigin,
    SOPRunRecord,
    SOPStepDataV1,
    SOPWorkspaceGrain,
    StorageBase,
)
from .._tool import SubmitVerdict
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
from ..._utils._common import _generate_id
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
        message_bus: MessageBus,
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
            message_bus (`MessageBus`):
                Records the turn as this run's while it is in flight,
                which is what gives that turn its submit tool.
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
        self._message_bus = message_bus
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
        dispatch = f"{self._sop_run_id}:{self._index}"
        try:
            # Handed to the turn rather than left where the session can
            # see it: a claim lying about would be picked up by whoever
            # takes the session lock first, and that may be a person who
            # happened to be typing into the same conversation.
            await self._chat.run(
                self._user_id,
                session_id,
                ref.agent_id,
                inputs if resuming else opening,
                sop_dispatch=dispatch,
            )

            stored = await self._storage.get_sop_run(
                self._user_id,
                self._sop_run_id,
            )
            if stored is None:
                # Deleted mid-turn; nothing left to record against.
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
                "Your turn ended without submitting anything, so the "
                "step has nothing to show for it.",
                "sop",
            )
        finally:
            # A parked turn is unfinished, not over: whoever answers it
            # resumes this same attempt through the ordinary chat
            # endpoint, which knows nothing of this step, so what it is
            # owed is left where that turn will find it. Every other
            # ending is the step letting go of the session.
            if state.phase is SOPPhase.AWAITING:
                await self._message_bus.registry_set(
                    MessageBusKeys.sop_dispatch(session_id),
                    MessageBusKeys.SOP_DISPATCH_FIELD,
                    dispatch,
                )
            else:
                await self._message_bus.registry_drop(
                    MessageBusKeys.sop_dispatch(session_id),
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
    """Start runs of a stored procedure, and carry them forward.

    Enter it as a context manager for the life of the application:
    an advance outlives the request that set it going, and on the way
    out it has to be stopped before the storage and bus it is writing
    to are closed under it.
    """

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
        # asyncio keeps only a weak reference to a running task, so a
        # detached advance has to live somewhere. Keyed by run so that
        # deleting one can stop what is carrying it on first.
        self._advancing: dict[str, set[asyncio.Task]] = {}

    async def create_run(
        self,
        user_id: str,
        sop_id: str,
        inputs: list[Msg] | None = None,
    ) -> SOPRunRecord:
        """Open a run: its conversations, its workspace, its record.

        Every conversation the procedure names is opened up front, so
        dispatching a step never has to ask whether one exists yet.

        Args:
            user_id (`str`):
                The owner user id.
            sop_id (`str`):
                The procedure to run. Read under its own lock and copied
                into the run, so neither editing nor deleting it
                afterwards can strand what this opens.
            inputs (`list[Msg] | None`, optional):
                What the run is started with, read by its first step.

        Returns:
            `SOPRunRecord`:
                The opened run, already persisted.

        Raises:
            `KeyError`:
                If the user has no such procedure, or a step of it names
                a conversation it never configured.
        """
        async with self._message_bus.acquire_lock(
            MessageBusKeys.sop_lock(sop_id),
            ttl_secs=MessageBusKeys.SOP_RUN_TTL_SECS,
        ):
            return await self._open_run(user_id, sop_id, inputs)

    async def _open_run(
        self,
        user_id: str,
        sop_id: str,
        inputs: list[Msg] | None,
    ) -> SOPRunRecord:
        """Open the run, with the procedure's lock already held."""
        sop_record = await self._storage.get_sop(user_id, sop_id)
        if sop_record is None:
            raise KeyError(f"SOP {sop_id!r} not found.")
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

        # Minted here rather than asked of the workspace manager. Its
        # ``assign_workspace_id`` answers under the deployment's
        # isolation policy, which under the default grain hands back a
        # workspace the agent already had — so two runs would share one,
        # and two keys on one agent would too. The grain is the author
        # saying how their procedure passes work along, and a field that
        # says that has to mean it.
        shared = _generate_id()
        for key, agent_id in agents.items():
            settings = data.session_settings[key]
            fallback = settings.fallback_chat_model_config
            workspace_id = (
                shared
                if data.workspace_grain is SOPWorkspaceGrain.RUN
                else _generate_id()
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

    async def __aenter__(self) -> Self:
        """Enter the service's lifetime."""
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Stop every advance still going, before its storage closes."""
        going = [task for tasks in self._advancing.values() for task in tasks]
        for task in going:
            task.cancel()
        await asyncio.gather(*going, return_exceptions=True)
        self._advancing.clear()

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
        going = self._advancing.setdefault(sop_run_id, set())
        going.add(task)
        task.add_done_callback(going.discard)

    async def delete_sop(self, user_id: str, sop_id: str) -> bool:
        """Delete a procedure, its runs, and the sessions they opened.

        Under the procedure's lock, so a run being opened cannot slip in
        behind the cascade, and under each run's, so a step in flight
        finishes writing before its record goes. Whatever is carrying a
        run on in this process is cancelled first — waiting for the lock
        alone would mean waiting out the rest of the procedure.

        Args:
            user_id (`str`):
                The owner user id.
            sop_id (`str`):
                The procedure to delete.

        Returns:
            `bool`:
                Whether there was one to delete.
        """
        async with self._message_bus.acquire_lock(
            MessageBusKeys.sop_lock(sop_id),
            ttl_secs=MessageBusKeys.SOP_RUN_TTL_SECS,
        ):
            for run in await self._storage.list_sop_runs(
                user_id,
                sop_id=sop_id,
            ):
                await self.delete_run(user_id, run.id)
            return await self._storage.delete_sop(user_id, sop_id)

    async def delete_run(self, user_id: str, sop_run_id: str) -> bool:
        """Delete a run and the conversations it opened.

        Under the run's lock, and after stopping whatever is carrying it
        on in this process: an advance reads the run, changes it in
        memory and writes it back, so one that finishes after the delete
        would put the record back with its sessions already gone.

        Args:
            user_id (`str`):
                The owner user id.
            sop_run_id (`str`):
                The run to delete.

        Returns:
            `bool`:
                Whether there was one to delete.
        """
        going = list(self._advancing.get(sop_run_id, ()))
        for task in going:
            task.cancel()
        await asyncio.gather(*going, return_exceptions=True)
        async with self._message_bus.acquire_lock(
            MessageBusKeys.sop_run_lock(sop_run_id),
            ttl_secs=MessageBusKeys.SOP_RUN_TTL_SECS,
        ):
            return await self._storage.delete_sop_run(user_id, sop_run_id)

    async def record_verdict(
        self,
        user_id: str,
        sop_run_id: str,
        step_index: int,
        passed: bool,
        message: str = "",
    ) -> SOPRunRecord:
        """File a person's verdict on a step that was waiting for one.

        Under the run's lock, because a run's own advance writes the
        whole run state back when it stops — a verdict landing between
        that advance's read and its write would be overwritten, and the
        step would sit waiting for an answer that had already been
        given.

        Args:
            user_id (`str`):
                The owner user id.
            sop_run_id (`str`):
                The run being judged.
            step_index (`int`):
                Which step, by its position.
            passed (`bool`):
                Whether the attempt is accepted.
            message (`str`, defaults to `""`):
                Why it was refused.

        Returns:
            `SOPRunRecord`:
                The run with the verdict recorded on it.

        Raises:
            `KeyError`:
                If the user has no such run, or it has no such step.
            `ValueError`:
                If that step is not one a person was asked to judge, or
                is not waiting to be.
        """
        async with self._message_bus.acquire_lock(
            MessageBusKeys.sop_run_lock(sop_run_id),
            ttl_secs=MessageBusKeys.SOP_RUN_TTL_SECS,
        ):
            record = await self._storage.get_sop_run(user_id, sop_run_id)
            if record is None:
                raise KeyError(f"SOP run {sop_run_id!r} not found.")
            if not 0 <= step_index < len(record.definition.steps):
                raise KeyError(
                    f"Step {step_index} is not part of this run.",
                )
            step = record.definition.steps[step_index]
            if not isinstance(step.verifier, HumanVerifier):
                raise ValueError(
                    f"Step {step_index} is not judged by a person.",
                )
            if record.state.steps[step_index].phase is not SOPPhase.AWAITING:
                raise ValueError(
                    f"Step {step_index} is not waiting to be judged.",
                )

            # The same tool an agent reviewer calls, so a verdict is one
            # thing however it was reached.
            await SubmitVerdict(
                storage=self._storage,
                user_id=user_id,
                sop_run_id=sop_run_id,
                step_index=step_index,
                verifier=user_id,
            )(passed=passed, message=message)

            updated = await self._storage.get_sop_run(user_id, sop_run_id)

        if updated is None:
            raise KeyError(f"SOP run {sop_run_id!r} not found.")
        return updated

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

        .. note::
            ``inputs`` accepts a :class:`UserInterruptEvent` because the
            engine does, but :class:`SessionSOPStep` does not yet act on
            one — the SDK's own step abandons the attempt and leaves it
            at ``PENDING``, and this has no equivalent. Nothing passes
            one today. Stopping a run is its own piece of work: it has
            to reach a turn already in flight, which means going through
            :class:`~agentscope.app._manager.ChatRunRegistry` rather
            than calling :meth:`ChatService.run` directly, and the stop
            cannot take the run lock it would be interrupting.

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
                        message_bus=self._message_bus,
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
