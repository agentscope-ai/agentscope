# -*- coding: utf-8 -*-
"""Deciding whether a step is done.

A verifier is an object a step holds, the same way it holds its agent.
There is no enum of kinds and no registry to resolve a name through:
whatever ``verify`` does inside — call a model, run a check, wait on a
person, ask a ticketing system — is the subclass's business, and the
engine never learns which.

It answers with a :class:`~._run.VerificationRecord`, or with **nothing at
all** when it has no answer yet. That second case is what keeps a human
verifier from being a special case: it returns ``None``, posts its request
however it likes, and hands back a real verdict once it has one. The
engine asks again on its next pass, so waiting a second on a model and
waiting a day on a person take the same path through the code — and
neither holds a coroutine open.

Returning ``None`` rather than a record saying "not yet" is deliberate: a
verdict that has not happened is not a verdict, and should not sit in a
step's history pretending to be one.

Because a verifier is a live object, it can remember whether it has
already asked. **At this layer only** — an object does not survive the
process, so a verifier that has to outlive one keeps its pending state in
the driver's own storage, never on ``self``.

Whatever it needs from the outside, it takes at construction — a model, an
HTTP client, a workspace. That is the same rule the rest of this layer
follows, and it is why ``verify`` is handed no such thing::

    ws = LocalWorkspace(...)
    agent = Agent(..., offloader=ws)

    SOPStep(
        subject="Run the tests",
        agent=agent,
        verifier=CommandVerifier(workspace=ws, command="pytest"),
    )

The engine never learns what a workspace is, which is what keeps it
runnable without a service underneath.
"""
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, TYPE_CHECKING

from ._run import SOPRunState, StepRun, VerificationRecord

if TYPE_CHECKING:
    from ._sop import SOP, SOPStep


class VerifierBase(ABC):
    """Decides whether a step's submission is acceptable."""

    @abstractmethod
    async def verify(
        self,
        sop: "SOP",
        run: SOPRunState,
        step: "SOPStep",
        step_run: StepRun,
    ) -> VerificationRecord | None:
        """Judge what a step handed back.

        The four arguments are the whole picture, definition beside
        runtime: ``sop`` and ``run`` for everything that has happened so
        far, ``step`` and ``step_run`` for the one being judged. A verdict
        that has to look further than the submission — at what an earlier
        step produced, or at whether the agent really did the thing — has
        what it needs, since ``step.agent`` is the live agent and its
        state is right there.

        Args:
            sop (`SOP`):
                The definition being run. Needed to make sense of ``run``:
                it is what turns a step id back into a subject.
            run (`SOPRunState`):
                The run in progress, carrying every step's record.
            step (`SOPStep`):
                The step being judged. :attr:`~._sop.SOPStep.agent` is the
                agent that ran it.
            step_run (`StepRun`):
                Its record. :attr:`~._run.StepRun.submission` is what the
                agent handed back, and
                :attr:`~._run.StepRun.verifications` is every earlier
                verdict.

        Returns:
            `VerificationRecord | None`:
                The verdict, or ``None`` when there is no answer yet and
                the engine should ask again later.
        """


class CallbackVerifier(VerifierBase):
    """Hands the decision to a callable.

    Enough for a script that wants to judge in Python, or to prompt on the
    console and answer on the spot::

        CallbackVerifier(lambda sop, run, step, rec: VerificationRecord(
            passed="DONE" in rec.submission,
            message="say DONE once the report is written",
        ))

    Returning ``None`` from the callable parks the step, exactly as it
    would from any other verifier.
    """

    def __init__(
        self,
        decide: Callable[
            ["SOP", SOPRunState, "SOPStep", StepRun],
            VerificationRecord | None | Awaitable[VerificationRecord | None],
        ],
        name: str = "callback",
    ) -> None:
        """Initialise the verifier.

        Args:
            decide (`Callable`):
                Called with the same four arguments as
                :meth:`VerifierBase.verify`. May be sync or async, and may
                return ``None`` to be asked again later.
            name (`str`, defaults to ``"callback"``):
                Recorded as
                :attr:`~._run.VerificationRecord.verified_by` when the
                callable leaves it empty.
        """
        self._decide = decide
        self._name = name

    async def verify(
        self,
        sop: "SOP",
        run: SOPRunState,
        step: "SOPStep",
        step_run: StepRun,
    ) -> VerificationRecord | None:
        """Ask the callable, awaiting it if it is a coroutine."""
        result = self._decide(sop, run, step, step_run)
        if isinstance(result, Awaitable):
            result = await result
        if result is not None and not result.verified_by:
            result.verified_by = self._name
        return result
