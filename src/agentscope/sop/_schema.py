# -*- coding: utf-8 -*-
"""The SOP definition — what a person writes, and can read back.

A step declares **what it is and what it must prove**; how it gets there
is its own business. :class:`SOPStep` is the shape almost everything
wants — an executor does the work, a verifier judges it — but the engine
never looks inside: it reads the run state and nothing else. Anything
that fills that in on time is a step, so subclass :class:`SOPStepBase`
and do as you like.

A definition holds no run state. The engine owns the run and hands each
step its own :class:`~._state.SOPStepRunState` on every call, so one
definition can drive any number of runs, at once if need be.
"""
from abc import ABC, abstractmethod
from typing import AsyncGenerator, ClassVar, Protocol, Type, runtime_checkable

from pydantic import BaseModel, Field

from ._state import SOPPhase, SOPStepRunState, VerificationResult
from ..event import (
    AgentEvent,
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import Msg, TextBlock, UserMsg
from ..pipeline import PipelineProtocol
from ..types import ReplyFinishedReason
from .._utils._common import _generate_id


@runtime_checkable
class AgentLike(PipelineProtocol, Protocol):
    """A pipeline that can also be handed a typed task.

    What a step needs of whatever does its work or judges it: everything
    :class:`~..pipeline.PipelineProtocol` promises, plus a reply that can
    be asked to end in structured output. A whole SOP satisfies the base
    protocol but deliberately not this one — an outside schema has no
    place in a procedure whose steps each carry their own.
    """

    def reply_stream(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
        structured_schema: Type[BaseModel] | None = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Reply to the given inputs and stream what happens."""


class SOPStepBase(ABC):
    """One milestone: a name, what it must prove, and how many tries.

    Subclasses put whatever they like in :meth:`reply_stream` — the
    contract is only that **one call is one attempt**, and that it either
    parks or files a verdict on the state it was handed.
    """

    state_type: ClassVar[type[SOPStepRunState]] = SOPStepRunState
    """What this step's run state looks like. Subclass
    :class:`~._state.SOPStepRunState` and name it here when a step has
    to remember more than the engine's three fields."""

    def __init__(
        self,
        subject: str,
        description: str,
        step_id: str | None = None,
        max_attempts: int = 3,
    ) -> None:
        """Initialize the step.

        Args:
            subject (`str`):
                A brief, actionable name.
            description (`str`):
                What this step must achieve — the destination, not the
                route.
            step_id (`str | None`, optional):
                The step identifier, generated when omitted.
            max_attempts (`int`, defaults to `3`):
                How many refusals before the run gives up on it. Enforced
                by the engine, not here.
        """
        self.subject = subject
        self.description = description
        self.id = step_id or _generate_id()
        self.max_attempts = max_attempts

    @abstractmethod
    def reply_stream(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None,
        state: SOPStepRunState,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Make one attempt at this step, streaming what happens.

        Args:
            inputs:
                What the engine handed over for a fresh attempt, or the
                answer a parked attempt was waiting for.
            state (`SOPStepRunState`):
                This step's record in the run. Read it to pick up where
                the last call stopped; write its phase, submission and
                verdicts as the attempt goes.
        """

    def record(
        self,
        state: SOPStepRunState,
        passed: bool,
        message: str = "",
        verifier: str = "",
    ) -> None:
        """File a verdict on the current attempt.

        A refusal clears the submission, so the next attempt starts from
        the work rather than from the judging. Whether there is a next
        attempt is the engine's call.
        """
        state.verifications.append(
            VerificationResult(
                passed=passed,
                message=message,
                verifier=verifier,
            ),
        )
        if passed:
            state.phase = SOPPhase.COMPLETED
        else:
            state.submission = None
            state.phase = SOPPhase.PENDING


class _Handover(BaseModel):
    """What an executor hands on when it is done."""

    handover: str = Field(
        description=(
            "What you are handing to the following steps. They cannot see "
            "your files, your tools' output or this conversation — only "
            "this. Write it for someone who has seen none of your work."
        ),
    )


class _Verdict(BaseModel):
    """What a verifier answers."""

    passed: bool = Field(
        description="Whether the work meets what the step had to prove.",
    )
    message: str = Field(
        default="",
        description=(
            "If it does not pass, exactly what is wrong and what to do "
            "about it. This is handed to the executor verbatim, so name "
            "the specific claims, files or values at fault."
        ),
    )


class SOPStep(SOPStepBase):
    """The usual shape: someone does the work, someone else judges it.

    One call is one attempt — work, then judgement. Parking in either
    half ends the call; the next one picks up where it stopped, told
    apart by whether anything was handed over yet.
    """

    def __init__(
        self,
        subject: str,
        description: str,
        executor: AgentLike,
        verifier: AgentLike | None = None,
        step_id: str | None = None,
        max_attempts: int = 3,
    ) -> None:
        """Initialize the step.

        Args:
            subject (`str`):
                A brief, actionable name.
            description (`str`):
                What this step must achieve.
            executor (`AgentLike`):
                Does the work. Reuse one across steps and they share its
                context; give each its own and they do not.
            verifier (`AgentLike | None`, optional):
                Judges it. ``None`` accepts whatever comes back, which is
                right for a step that only has to happen.
            step_id (`str | None`, optional):
                The step identifier, generated when omitted.
            max_attempts (`int`, defaults to `3`):
                How many refusals before the run gives up on it.
        """
        super().__init__(subject, description, step_id, max_attempts)
        self.executor = executor
        self.verifier = verifier

    async def reply_stream(  # pylint: disable=invalid-overridden-method
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None,
        state: SOPStepRunState,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Make one attempt: do the work, then have it judged."""
        state.phase = SOPPhase.RUNNING

        if state.submission is None:
            handover = None
            async for event in self.executor.reply_stream(
                inputs=self._brief(inputs, state),
                structured_schema=_Handover,
                yield_final_msg=True,
            ):
                yield event
                if _parked(event):
                    state.phase = SOPPhase.AWAITING
                    return
                if _output := _structured(event):
                    handover = _output.get("handover")

            if handover is None:
                # Structured output makes this unlikely, but a model can
                # still burn its turns without producing one.
                self.record(
                    state,
                    False,
                    "Your turn ended without a structured output, so "
                    "nothing was handed on.",
                    "sop",
                )
                return
            state.submission = [TextBlock(text=handover)]
            inputs = None

        if self.verifier is None:
            self.record(state, True)
            return

        verdict = None
        async for event in self.verifier.reply_stream(
            inputs=self._question(inputs, state),
            structured_schema=_Verdict,
            yield_final_msg=True,
        ):
            yield event
            if _parked(event):
                state.phase = SOPPhase.AWAITING
                return
            if _output := _structured(event):
                verdict = _output

        if verdict is None:
            self.record(
                state,
                False,
                "The verifier reached no verdict.",
                "sop",
            )
            return
        self.record(
            state,
            verdict["passed"],
            verdict.get("message", ""),
            getattr(self.verifier, "name", "verifier"),
        )

    def _brief(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None,
        state: SOPStepRunState,
    ) -> (
        list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
    ):
        """What the executor is asked, on top of whatever came in.

        Resumption events are passed straight through — the executor is
        mid-reply and expects the answer, not a fresh instruction.
        """
        if not isinstance(inputs, (Msg, list, type(None))):
            return inputs

        text = (
            f"<system-reminder>You are running one step of the SOP.\n\n"
            f"## {self.subject}\n\n{self.description}\n"
        )
        if state.verifications:
            last = state.verifications[-1]
            text += (
                f"\nYour last attempt was not accepted:\n{last.message}\n"
                f"This is attempt {len(state.verifications) + 1} of "
                f"{self.max_attempts}."
            )
        text += "</system-reminder>"

        brief = UserMsg(name="sop", content=text)
        if inputs is None:
            return [brief]
        return [brief, *(inputs if isinstance(inputs, list) else [inputs])]

    def _question(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None,
        state: SOPStepRunState,
    ) -> (
        list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
    ):
        """What the verifier is asked, on top of whatever came in.

        It sees what the step was given as well as what came back — a
        draft can only be judged against what was asked for.
        """
        if inputs is not None:
            return inputs
        return [
            UserMsg(
                name="sop",
                content=(
                    "<system-reminder>Judge the work below against what "
                    f"this step had to prove.\n\n## {self.subject}\n\n"
                    f"{self.description}</system-reminder>"
                ),
            ),
            *state.given,
            UserMsg(
                name="sop",
                content=[
                    TextBlock(text="<submission>"),
                    *(state.submission or []),
                    TextBlock(text="</submission>"),
                ],
            ),
        ]


def _parked(event: AgentEvent | Msg) -> bool:
    """Whether this event means someone outside has to answer."""
    return isinstance(
        event,
        (RequireUserConfirmEvent, RequireExternalExecutionEvent),
    )


def _structured(event: AgentEvent | Msg) -> dict | None:
    """The structured output of a finished reply, if this is one."""
    if (
        isinstance(event, Msg)
        and event.finished_reason == ReplyFinishedReason.COMPLETED
    ):
        return event.structured_output
    return None


class SOP:
    """A fixed sequence of milestones, each verified before the next."""

    def __init__(
        self,
        name: str,
        steps: list[SOPStepBase],
        description: str = "",
        sop_id: str | None = None,
    ) -> None:
        """Initialize the procedure.

        Args:
            name (`str`):
                The SOP name.
            steps (`list[SOPStepBase]`):
                The steps, in the order they run.
            description (`str`, optional):
                What this procedure is for.
            sop_id (`str | None`, optional):
                The SOP identifier, generated when omitted.
        """
        self.name = name
        self.steps = steps
        self.description = description
        self.id = sop_id or _generate_id()
