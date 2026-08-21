# -*- coding: utf-8 -*-
"""Deciding whether a step is done.

A verifier is an object a step holds, the same way it holds its agent.
There is no enum of kinds and no registry to resolve a name through:
whatever ``verify`` does inside — call a model, run a check, wait on a
person, ask a ticketing system — is the subclass's business, and the
engine never learns which.

The only thing the engine needs back is one of three answers, and the
third is what makes a human verifier stop being a special case::

    passed   the step is done
    failed   it is not, and `message` says what to fix
    pending  no answer yet — ask again later

A verifier that needs a person returns ``pending`` on the first call,
posts its request however it likes, and returns the real answer once it
has one. The engine simply asks again on its next pass, so waiting an hour
for a human and waiting a second for a model take exactly the same path
through the code.

Because a verifier is a live object, it can also remember whether it has
already asked. Anything that must outlive the process belongs in the
driver's own storage, not here.
"""
from abc import ABC, abstractmethod
from typing import Awaitable, Callable, Literal, TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from ._model import SOPStep
    from ._run import SOPRun, StepRun

VerifyStatus = Literal["passed", "failed", "pending"]
"""The three answers a verifier can give. See :mod:`._verifier`."""


class VerifyResult(BaseModel):
    """What a verifier concluded."""

    status: VerifyStatus
    """Whether the step is done, is not, or cannot be judged yet."""

    message: str = ""
    """Why it was refused. This goes back to the agent verbatim, so it has
    to say what is missing rather than that something is."""

    verified_by: str = ""
    """Who decided — a model name, a person, an external system."""


class VerifierBase(ABC):
    """Decides whether a step's submission is acceptable."""

    @abstractmethod
    async def verify(
        self,
        step: "SOPStep",
        run: "SOPRun",
        step_run: "StepRun",
    ) -> VerifyResult:
        """Judge what a step handed back.

        Args:
            step (`SOPStep`):
                The step being judged. Its own configuration lives on this
                verifier, not here.
            run (`SOPRun`):
                The run in progress, for context.
            step_run (`StepRun`):
                The step's record. :attr:`~.StepRun.submission` is what
                the agent handed back, and
                :attr:`~.StepRun.verifications` is every earlier verdict.

        Returns:
            `VerifyResult`:
                ``passed``, ``failed`` with a reason, or ``pending`` when
                the answer has to come from somewhere else.
        """


class CallbackVerifier(VerifierBase):
    """Hands the decision to a callable.

    Enough for a script that wants to judge in Python, or to prompt on the
    console and block until a person answers::

        CallbackVerifier(lambda step, run, rec: VerifyResult(
            status="passed" if "DONE" in rec.submission else "failed",
            message="say DONE when the report is written",
        ))
    """

    def __init__(
        self,
        decide: Callable[
            ["SOPStep", "SOPRun", "StepRun"],
            VerifyResult | Awaitable[VerifyResult],
        ],
        name: str = "callback",
    ) -> None:
        """Initialise the verifier.

        Args:
            decide (`Callable`):
                Called with the step, the run and the step's record. May
                be sync or async, and may return ``pending`` to be asked
                again later.
            name (`str`, defaults to ``"callback"``):
                Recorded as :attr:`VerifyResult.verified_by` when the
                callable leaves it empty.
        """
        self._decide = decide
        self._name = name

    async def verify(
        self,
        step: "SOPStep",
        run: "SOPRun",
        step_run: "StepRun",
    ) -> VerifyResult:
        """Ask the callable, awaiting it if it is a coroutine."""
        result = self._decide(step, run, step_run)
        if isinstance(result, Awaitable):
            result = await result
        if not result.verified_by:
            result.verified_by = self._name
        return result
