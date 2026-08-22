# -*- coding: utf-8 -*-
"""Running a SOP.

Two halves. The first is a set of pure functions that read a
:class:`~._sop.SOP` and a :class:`~._run.SOPRunState` and say what can
happen next — readiness, retries, when a run is finished or stuck. They
touch nothing and await nothing, which is what lets a service driver reach
the same conclusions as :class:`SOPEngine` without inheriting from it.

The second is the engine, shaped like an agent on purpose. It holds its
run the way an agent holds its state, and when something needs a person it
**ends the stream** rather than holding a coroutine open: no task pinned,
no lock held, nothing suspended. Come back with an answer and it picks up
from the run, because the run is where everything is.
"""
from dataclasses import dataclass
from typing import AsyncGenerator, Literal, Union

from ._run import (
    SOPRunState,
    SOPRunStatus,
    SOPStepState,
    StepRun,
    VerificationRecord,
)
from ._sop import SOP, SOPStep
from ..agent import Agent
from ..event import AgentEvent, EventBase, EventType, RequireUserConfirmEvent
from ..event import UserConfirmResultEvent
from ..message import DataBlock, Msg, TextBlock, UserMsg
from ..permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ..tool import ToolBase, ToolChunk
from .._logging import logger
from .._utils._common import _generate_timestamp

# ══════════════════════════════════════════════════════════════════════
# Events
# ══════════════════════════════════════════════════════════════════════


class StepStateEvent(EventBase):
    """A step changed state.

    Without this a watcher sees a flat stream of agent events and cannot
    tell which step produced them, or why one was sent back.
    """

    type: Literal[EventType.SOP_STEP_STATE] = EventType.SOP_STEP_STATE
    """Event type."""
    run_id: str
    """The run this belongs to."""
    step_id: str
    """The step that moved."""
    subject: str
    """Its subject, so the stream reads without the definition to hand."""
    state: SOPStepState
    """Where it now stands."""
    message: str = ""
    """Why, when it was refused."""


class RunSettledEvent(EventBase):
    """The run reached a terminal status."""

    type: Literal[EventType.SOP_RUN_SETTLED] = EventType.SOP_RUN_SETTLED
    """Event type."""
    run_id: str
    """The run that settled."""
    status: SOPRunStatus
    """How it ended."""
    reason: str = ""
    """Why, when it did not simply finish."""


SOPEvent = Union[StepStateEvent, RunSettledEvent]
"""An event about the procedure rather than about an agent."""


# ══════════════════════════════════════════════════════════════════════
# Actions — what a driver is being asked to carry out
# ══════════════════════════════════════════════════════════════════════


@dataclass
class Dispatch:
    """Hand a step to its agent and collect what it submits."""

    step_id: str
    """The step to run."""


@dataclass
class Judge:
    """Ask a step's verifier what it makes of the submission.

    Also issued for a step whose verifier has not answered yet — one that
    returns nothing is simply asked again, so waiting on a person and
    waiting on a model are one path.
    """

    step_id: str
    """The step to judge."""


@dataclass
class Settle:
    """Nothing is left to do; the run has reached ``status``."""

    status: SOPRunStatus
    """How the run ended."""

    reason: str = ""
    """Why, when it did not simply finish."""


Action = Union[Dispatch, Judge, Settle]
"""One thing a driver should do. See :func:`next_actions`."""


# ══════════════════════════════════════════════════════════════════════
# Decisions — pure, and shared by every driver
# ══════════════════════════════════════════════════════════════════════

_UNSETTLED = (
    SOPStepState.PENDING,
    SOPStepState.RUNNING,
    SOPStepState.VERIFYING,
)


def new_run(
    sop: SOP,
    inputs: list[TextBlock | DataBlock] | None = None,
) -> SOPRunState:
    """Start a run of ``sop``, with one pending record per step.

    Args:
        sop (`SOP`):
            The definition to run.
        inputs (`list[TextBlock | DataBlock] | None`, optional):
            What the run is started with.

    Returns:
        `SOPRunState`:
            A fresh run, not yet advanced.
    """
    return SOPRunState(
        sop_id=sop.id,
        inputs=list(inputs or []),
        steps={s.id: StepRun() for s in sop.steps},
    )


def is_ready(sop: SOP, run: SOPRunState, step_id: str) -> bool:
    """Whether a pending step's blockers have all completed.

    Readiness is recomputed rather than stored, so there is never a stale
    flag to disagree with the states it came from.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRunState`):
            The run in progress.
        step_id (`str`):
            The step to test.

    Returns:
        `bool`:
            ``True`` when every blocker has completed.
    """
    step = find_step(sop, step_id)
    if step is None:
        return False
    return all(
        (r := run.steps.get(b)) is not None
        and r.state == SOPStepState.COMPLETED
        for b in step.blocked_by
    )


def overall_status(sop: SOP, run: SOPRunState) -> SOPRunStatus:
    """Work out where the run stands as a whole.

    A run is still ``RUNNING`` only while something can actually move. A
    step left pending behind a failure keeps no special state of its own,
    so this is also what tells a stranded run from a live one.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRunState`):
            The run in progress.

    Returns:
        `SOPRunStatus`:
            Where the run stands.
    """
    records = run.steps.values()
    if any(
        r.state in (SOPStepState.RUNNING, SOPStepState.VERIFYING)
        for r in records
    ):
        return SOPRunStatus.RUNNING
    if any(
        r.state == SOPStepState.PENDING and is_ready(sop, run, sid)
        for sid, r in run.steps.items()
    ):
        return SOPRunStatus.RUNNING
    if all(r.state == SOPStepState.COMPLETED for r in records):
        return SOPRunStatus.COMPLETED
    return SOPRunStatus.FAILED


def upstream_submissions(
    sop: SOP,
    run: SOPRunState,
    step_id: str,
) -> dict[str, str]:
    """Collect what a step's blockers submitted, keyed by their subject.

    This is the whole handover between steps: text, nothing else.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRunState`):
            The run in progress.
        step_id (`str`):
            The step about to be dispatched.

    Returns:
        `dict[str, str]`:
            Blocker subject → the text it submitted.
    """
    step = find_step(sop, step_id)
    if step is None:
        return {}
    out: dict[str, str] = {}
    for blocker_id in step.blocked_by:
        blocker = find_step(sop, blocker_id)
        record = run.steps.get(blocker_id)
        if blocker is not None and record is not None:
            out[blocker.subject] = record.submission
    return out


def feedback(run: SOPRunState, step_id: str) -> str:
    """Why the last attempt was refused, for the retry prompt.

    Args:
        run (`SOPRunState`):
            The run in progress.
        step_id (`str`):
            The step being retried.

    Returns:
        `str`:
            The refusal message, or ``""`` on a first attempt.
    """
    record = run.steps.get(step_id)
    if record is None or not record.verifications:
        return ""
    last = record.verifications[-1]
    return "" if last.passed else last.message


def next_actions(sop: SOP, run: SOPRunState) -> list[Action]:
    """Decide everything that can be done right now.

    An empty-handed run yields a single :class:`Settle`.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRunState`):
            The run in progress.

    Returns:
        `list[Action]`:
            What to do; one :class:`Settle` when the run is over; empty
            when something is in flight and only the outside world can
            move it.
    """
    actions: list[Action] = []
    for step in sop.steps:
        record = run.steps.get(step.id)
        if record is None:
            continue
        ready = is_ready(sop, run, step.id)
        if record.state == SOPStepState.PENDING and ready:
            actions.append(Dispatch(step.id))
        elif record.state == SOPStepState.VERIFYING:
            actions.append(Judge(step.id))

    if actions:
        return actions

    status = overall_status(sop, run)
    if status == SOPRunStatus.RUNNING:
        # Something is in flight — an agent stopped for permission, or a
        # verifier still waiting. Nothing to do, but nothing to settle.
        return []
    if status == SOPRunStatus.FAILED and any(
        r.state in _UNSETTLED for r in run.steps.values()
    ):
        # Steps left, yet none can move: a blocker cycle, an upstream
        # failure, or a blocked_by naming a step not in this run.
        return [Settle(status, "no step can proceed")]
    return [Settle(status)]


def mark_dispatched(run: SOPRunState, step_id: str) -> None:
    """Note that a step has been handed to its agent.

    Args:
        run (`SOPRunState`):
            The run in progress.
        step_id (`str`):
            The step being dispatched.
    """
    if (record := run.steps.get(step_id)) is not None:
        record.state = SOPStepState.RUNNING


def mark_submitted(run: SOPRunState, step_id: str, submission: str) -> None:
    """Record what a step's agent handed back, ready to be judged.

    Args:
        run (`SOPRunState`):
            The run in progress.
        step_id (`str`):
            The step that finished working.
        submission (`str`):
            The text it submitted.
    """
    if (record := run.steps.get(step_id)) is not None:
        record.submission = submission
        record.state = SOPStepState.VERIFYING


def record_verification(
    sop: SOP,
    run: SOPRunState,
    step_id: str,
    verdict: VerificationRecord,
) -> None:
    """Apply a verdict and move the step accordingly.

    Passing completes the step. Being refused sends it back to its agent
    with the reason, unless that was its
    :attr:`~._sop.SOPStep.max_attempts` refusal, in which case the step
    fails — and everything behind it simply never runs.

    The attempt count is not tracked separately: it is how many verdicts
    have been recorded.

    Args:
        sop (`SOP`):
            The definition being run.
        run (`SOPRunState`):
            The run in progress.
        step_id (`str`):
            The step that was judged.
        verdict (`VerificationRecord`):
            What its verifier concluded.
    """
    record, step = run.steps.get(step_id), find_step(sop, step_id)
    if record is None or step is None:
        return

    record.verifications.append(verdict)
    if verdict.passed:
        record.state = SOPStepState.COMPLETED
    elif len(record.verifications) >= step.max_attempts:
        record.state = SOPStepState.FAILED
    else:
        # Back in the queue rather than still running: the agent is not
        # working right now, it is waiting to be asked again.
        record.state = SOPStepState.PENDING


def find_step(sop: SOP, step_id: str) -> SOPStep | None:
    """Look a step up in the definition.

    Args:
        sop (`SOP`):
            The definition to search.
        step_id (`str`):
            The id to find.

    Returns:
        `SOPStep | None`:
            The step, or ``None`` when the definition has no such id.
    """
    return next((s for s in sop.steps if s.id == step_id), None)


# ══════════════════════════════════════════════════════════════════════
# The submit tool
# ══════════════════════════════════════════════════════════════════════


class SubmitStepResult(ToolBase):
    """The one way a step's agent hands its result on.

    Attached before a step runs and taken away after, so an agent only
    ever sees it while it is working on a step.
    """

    name: str = "SubmitStepResult"

    description: str = """Submit the result of the step you are working on.

Call this once, when you are finished. Whatever you pass is the ONLY thing
the following steps will see — they cannot read your files, your tools' \
output, or this conversation. Write it so that someone who has seen none \
of your work can carry on from it.

Include what you produced, where you left anything you created, and \
anything the next step would otherwise have to guess."""

    input_schema: dict = {
        "type": "object",
        "properties": {
            "result": {
                "type": "string",
                "description": "What you are handing on to the next steps.",
            },
        },
        "required": ["result"],
    }

    is_concurrency_safe: bool = False

    is_read_only: bool = True

    def __init__(self, sink: list[str]) -> None:
        """Initialise the tool.

        Args:
            sink (`list[str]`):
                Where the submission is put for the engine to read.
        """
        super().__init__()
        self._sink = sink

    async def call(self, result: str) -> ToolChunk:
        """Record the submission."""
        self._sink.append(result)
        return ToolChunk(
            content=[TextBlock(text="Submitted. The step is now verified.")],
        )

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        """Handing on a result never needs confirming."""
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is always allowed.",
        )


# ══════════════════════════════════════════════════════════════════════
# The engine
# ══════════════════════════════════════════════════════════════════════


class SOPEngine:
    """Drives a SOP, one pass at a time."""

    def __init__(self, sop: SOP, run: SOPRunState | None = None) -> None:
        """Initialise the engine.

        Args:
            sop (`SOP`):
                The procedure to run. Its steps already hold their agents
                and verifiers, so the engine needs nothing else — no
                model, no workspace, no approval channel.
            run (`SOPRunState | None`, optional):
                A run to pick back up. A fresh one is started when
                omitted, which is the usual case; a service hands in a
                stored one to resume after a restart.
        """
        self.sop = sop
        self.run = run if run is not None else new_run(sop)

    @property
    def status(self) -> SOPRunStatus:
        """Where the run stands. Derived, never stored."""
        return overall_status(self.sop, self.run)

    async def run_stream(
        self,
        inputs: list[TextBlock | DataBlock]
        | UserConfirmResultEvent
        | None = None,
    ) -> AsyncGenerator[AgentEvent | SOPEvent | Msg, None]:
        """Advance the run as far as it will go, streaming what happens.

        The stream ends when the run settles, or when a whole pass moved
        nothing — an agent stopped for permission, or a verifier still
        waiting on someone. Both look the same from out here: the stream
        is over, :attr:`run` says where it stopped, call again to carry
        on.

        Args:
            inputs (`list[TextBlock | DataBlock] | UserConfirmResultEvent \
            | None`, optional):
                Content starts a **new** run, replacing :attr:`run`. A
                :class:`~..event.UserConfirmResultEvent` is delivered to
                whichever step's agent is stopped on it — found by asking
                the agents, not from a table the engine keeps. ``None``
                simply carries on, which is also how a verifier waiting on
                the outside world gets asked again.

        Yields:
            `AgentEvent | SOPEvent | Msg`:
                Everything the agents emit, with a
                :class:`StepStateEvent` before and after each step so the
                stream can be read without the definition to hand.
        """
        if isinstance(inputs, UserConfirmResultEvent):
            async for event in self._resume(inputs):
                yield event
        elif inputs:
            self.run = new_run(self.sop, inputs)

        while True:
            actions = next_actions(self.sop, self.run)

            if not actions:
                # Waiting on the outside world. Let go of the stream
                # rather than spin — nothing here stays suspended.
                return

            if isinstance(actions[0], Settle):
                settle = actions[0]
                yield RunSettledEvent(
                    run_id=self.run.id,
                    status=settle.status,
                    reason=settle.reason,
                )
                return

            moved = False
            for action in actions:
                if isinstance(action, Dispatch):
                    parked = False
                    async for event in self._dispatch(action.step_id):
                        yield event
                        parked = parked or isinstance(
                            event,
                            RequireUserConfirmEvent,
                        )
                    if parked:
                        return
                    moved = True
                elif isinstance(action, Judge):
                    async for event in self._judge(action.step_id):
                        yield event
                        moved = moved or isinstance(event, StepStateEvent)

            if not moved:
                # A verifier is still waiting on something outside. Let go
                # rather than spin; the caller comes back when it can.
                return

    # ------------------------------------------------------------------
    # Step execution — the half a service driver replaces
    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        step_id: str,
    ) -> AsyncGenerator[AgentEvent | SOPEvent | Msg, None]:
        """Run one step and collect what it submits.

        Ends early, leaving the step running, if its agent stops for
        permission.

        Args:
            step_id (`str`):
                The step to run.

        Yields:
            `AgentEvent | SOPEvent | Msg`:
                The agent's events, headed by a :class:`StepStateEvent`.
        """
        step = find_step(self.sop, step_id)
        if step is None:
            return
        first_time = not self.run.steps[step_id].verifications
        mark_dispatched(self.run, step_id)
        yield self._state_event(step)

        if first_time and step.tasks:
            step.agent.state.tasks_context.tasks = [
                task.model_copy(deep=True) for task in step.tasks
            ]

        async for event in self._drive(step, self._build_prompt(step)):
            yield event

    async def _resume(
        self,
        answer: UserConfirmResultEvent,
    ) -> AsyncGenerator[AgentEvent | SOPEvent | Msg, None]:
        """Deliver a confirmation to whichever agent is stopped on it.

        The recipient is worked out, not looked up: the reply the answer
        belongs to names itself, and each running step's agent knows
        whether it is the one waiting.

        Args:
            answer (`UserConfirmResultEvent`):
                The confirmation to deliver.

        Yields:
            `AgentEvent | SOPEvent | Msg`:
                Whatever the resumed agent goes on to emit.
        """
        step = self._parked_step(answer.reply_id)
        if step is None:
            logger.warning(
                "No step is waiting on reply %s; the confirmation is "
                "dropped.",
                answer.reply_id,
            )
            return
        async for event in self._drive(step, answer):
            yield event

    async def _drive(
        self,
        step: SOPStep,
        inputs: Msg | UserConfirmResultEvent,
    ) -> AsyncGenerator[AgentEvent | SOPEvent | Msg, None]:
        """Run a step's agent until it submits or stops for permission.

        Args:
            step (`SOPStep`):
                The step being run.
            inputs (`Msg | UserConfirmResultEvent`):
                What to feed its agent — a prompt, or a confirmation it
                was waiting for.

        Yields:
            `AgentEvent | SOPEvent | Msg`:
                The agent's events, then a :class:`StepStateEvent` if the
                step submitted.
        """
        sink: list[str] = []
        await step.agent.toolkit.add_tool(SubmitStepResult(sink))
        try:
            async for event in step.agent.reply_stream(inputs):
                yield event
        finally:
            await step.agent.toolkit.remove_tool(SubmitStepResult.name)

        if sink:
            mark_submitted(self.run, step.id, sink[-1])
            yield self._state_event(step)

    async def _judge(
        self,
        step_id: str,
    ) -> AsyncGenerator[SOPEvent, None]:
        """Ask a step's verifier for a verdict and apply it.

        Yields nothing when the verifier has no answer yet, which is how
        the caller learns the pass moved nothing.

        Args:
            step_id (`str`):
                The step to judge.

        Yields:
            `SOPEvent`:
                A :class:`StepStateEvent`, once a verdict lands.
        """
        step = find_step(self.sop, step_id)
        record = self.run.steps.get(step_id)
        if step is None or record is None:
            return

        if step.verifier is None:
            verdict = VerificationRecord(passed=True, verified_by="unverified")
        else:
            started_at = _generate_timestamp()
            verdict = await step.verifier.verify(
                self.sop,
                self.run,
                step,
                record,
            )
            if verdict is None:
                return
            verdict.created_at = started_at

        record_verification(self.sop, self.run, step_id, verdict)
        message = "" if verdict.passed else verdict.message
        yield self._state_event(step, message)

    # ------------------------------------------------------------------
    # Prompting — the other half worth overriding
    # ------------------------------------------------------------------

    def _build_prompt(self, step: SOPStep) -> Msg:
        """What a step's agent is actually asked.

        Its own description, whatever the run was started with if nothing
        precedes it, what its blockers handed over, and — on a retry — why
        the last attempt was refused.

        Args:
            step (`SOPStep`):
                The step about to run.

        Returns:
            `Msg`:
                The message to hand its agent.
        """
        content: list[TextBlock | DataBlock] = [
            TextBlock(text=f"## {step.subject}\n\n{step.description}".strip()),
        ]

        if not step.blocked_by and self.run.inputs:
            content.append(TextBlock(text="\n## What you were given\n"))
            content.extend(self.run.inputs)

        handovers = upstream_submissions(self.sop, self.run, step.id)
        if handovers:
            joined = "\n\n".join(
                f"### {subject}\n{text}" for subject, text in handovers.items()
            )
            content.append(
                TextBlock(
                    text=f"\n## Handed to you by earlier steps\n{joined}",
                ),
            )

        if refused := feedback(self.run, step.id):
            content.append(
                TextBlock(
                    text=(
                        "\n## Your last attempt was not accepted\n"
                        f"{refused}\n\n"
                        "Address this, then submit again."
                    ),
                ),
            )

        content.append(
            TextBlock(
                text=(
                    f"\nWhen you are done, call {SubmitStepResult.name} — "
                    "it is the only thing the following steps will see."
                ),
            ),
        )
        return UserMsg(name="sop", content=content)

    # ------------------------------------------------------------------

    def _parked_step(self, reply_id: str) -> SOPStep | None:
        """Find the running step whose agent is waiting on ``reply_id``."""
        for step in self.sop.steps:
            record = self.run.steps.get(step.id)
            if record is None or record.state != SOPStepState.RUNNING:
                continue
            state = step.agent.state
            if state.reply_id == reply_id and state.has_awaiting_tool_calls(
                step.agent.name,
            ):
                return step
        return None

    def _state_event(self, step: SOPStep, message: str = "") -> StepStateEvent:
        """Announce where a step now stands."""
        return StepStateEvent(
            run_id=self.run.id,
            step_id=step.id,
            subject=step.subject,
            state=self.run.steps[step.id].state,
            message=message,
        )
