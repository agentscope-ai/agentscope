# -*- coding: utf-8 -*-
"""The SOP definition — what a person writes, and can read back.

A step declares **what it is and what it must prove**; how it gets there
is its own business. :class:`SOPStep` is the shape almost everything
wants — an executor does the work, a verifier judges it — but the engine
never looks inside: it reads
:attr:`~._state.SOPStepRunState.verifications` and nothing else. Anything
that fills that in on time is a step, so subclass :class:`SOPStepBase`
and do as you like.
"""
from abc import ABC, abstractmethod
from typing import (
    AsyncGenerator,
    Protocol,
    Type,
    TypeAlias,
    runtime_checkable,
)

from pydantic import BaseModel, Field

from ._state import SOPStepRunState, SOPStepState, VerificationResult
from ..event import (
    AgentEvent,
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import Msg, UserMsg
from ..state import Task
from ..types import ReplyFinishedReason
from .._utils._common import _generate_id

StepInputs: TypeAlias = (
    Msg
    | list[Msg]
    | UserConfirmResultEvent
    | UserInterruptEvent
    | ExternalExecutionResultEvent
    | None
)


@runtime_checkable
class AgentLike(Protocol):
    """What a step needs of whatever does its work or judges it.

    Declared as a plain ``def`` returning an async generator: such a
    function is called, not awaited, so an ``async def`` here would be
    satisfied by :class:`~..agent.Agent` least of all.
    """

    def reply_stream(
        self,
        inputs: StepInputs = None,
        structured_schema: Type[BaseModel] | None = None,
        yield_final_msg: bool = False,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Reply to the given inputs and stream what happens."""


class SOPStepBase(ABC):
    """One milestone: a name, what it must prove, and how many tries.

    Subclasses put whatever they like in :meth:`reply_stream` — the
    contract is only that **one call is one attempt**, and that it either
    parks or leaves a verdict in
    :attr:`~._state.SOPStepRunState.verifications`.
    """

    def __init__(
        self,
        subject: str,
        description: str,
        step_id: str | None = None,
        max_attempts: int = 3,
        tasks: list[Task] | None = None,
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
            tasks (`list[Task] | None`, optional):
                Planning tasks to seed the executor's own list with.
        """
        self.subject = subject
        self.description = description
        self.id = step_id or _generate_id()
        self.max_attempts = max_attempts
        self.tasks = tasks or []
        self.state = SOPStepRunState(step_id=self.id)

    @abstractmethod
    def reply_stream(
        self,
        inputs: StepInputs = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Make one attempt at this step, streaming what happens."""

    def record(
        self,
        passed: bool,
        message: str = "",
        verifier: str = "",
    ) -> None:
        """File a verdict on the current attempt.

        A refusal clears the submission, so the next attempt starts from
        the work rather than from the judging. Whether there is a next
        attempt is the engine's call.
        """
        self.state.verifications.append(
            VerificationResult(
                passed=passed,
                message=message,
                verifier=verifier,
            ),
        )
        if passed:
            self.state.state = SOPStepState.COMPLETED
        else:
            self.state.submission = None
            self.state.state = SOPStepState.PENDING


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
        tasks: list[Task] | None = None,
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
            tasks (`list[Task] | None`, optional):
                Planning tasks to seed the executor's own list with.
        """
        super().__init__(
            subject,
            description,
            step_id,
            max_attempts,
            tasks,
        )
        self.executor = executor
        self.verifier = verifier

    async def reply_stream(  # pylint: disable=invalid-overridden-method
        self,
        inputs: StepInputs = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Make one attempt: do the work, then have it judged."""
        self.state.state = SOPStepState.RUNNING
        if isinstance(inputs, (Msg, list)):
            self.state.given = (
                [inputs] if isinstance(inputs, Msg) else list(inputs)
            )

        if self.state.submission is None:
            handover = None
            async for event in self.executor.reply_stream(
                inputs=self._brief(inputs),
                structured_schema=_Handover,
                yield_final_msg=True,
            ):
                yield event
                if _parked(event):
                    self.state.state = SOPStepState.AWAITING
                    return
                if _output := _structured(event):
                    handover = _output.get("handover")

            if handover is None:
                # Structured output makes this unlikely, but a model can
                # still burn its turns without producing one.
                self.record(
                    False,
                    "Your turn ended without a structured output, so "
                    "nothing was handed on.",
                    "sop",
                )
                return
            self.state.submission = handover
            inputs = None

        if self.verifier is None:
            self.record(True)
            return

        verdict = None
        async for event in self.verifier.reply_stream(
            inputs=self._question(inputs),
            structured_schema=_Verdict,
            yield_final_msg=True,
        ):
            yield event
            if _parked(event):
                self.state.state = SOPStepState.AWAITING
                return
            if _output := _structured(event):
                verdict = _output

        if verdict is None:
            self.record(False, "The verifier reached no verdict.", "sop")
            return
        self.record(
            verdict["passed"],
            verdict.get("message", ""),
            getattr(self.verifier, "name", "verifier"),
        )

    def _brief(self, inputs: StepInputs) -> StepInputs:
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
        if self.state.verifications:
            last = self.state.verifications[-1]
            text += (
                f"\nYour last attempt was not accepted:\n{last.message}\n"
                f"This is attempt {len(self.state.verifications) + 1} of "
                f"{self.max_attempts}."
            )
        text += "</system-reminder>"

        brief = UserMsg(name="sop", content=text)
        if inputs is None:
            return [brief]
        return [brief, *(inputs if isinstance(inputs, list) else [inputs])]

    def _question(self, inputs: StepInputs) -> StepInputs:
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
            *self.state.given,
            UserMsg(
                name="sop",
                content=(
                    f"<submission>\n{self.state.submission}\n</submission>"
                ),
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
        description: str,
        steps: list[SOPStepBase],
        sop_id: str | None = None,
    ) -> None:
        """Initialize the procedure.

        Args:
            name (`str`):
                The SOP name.
            description (`str`):
                What this procedure is for.
            steps (`list[SOPStepBase]`):
                The steps, in the order they run.
            step_id (`str | None`, optional):
                The SOP identifier, generated when omitted.
        """
        self.name = name
        self.description = description
        self.steps = steps
        self.id = sop_id or _generate_id()
