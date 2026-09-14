# -*- coding: utf-8 -*-
"""The tools a SOP step ends its turn with.

A step's result is a row, not a sentence: the executor's handover and
the verifier's verdict both go straight into the run state, so nothing
downstream has to parse a reply to find out what happened. Which of the
two an agent gets is decided by the role its session plays in the run.
"""
from typing import Any, TYPE_CHECKING

from pydantic import Field

from ...message import TextBlock, ToolResultState
from ...permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionDecision,
)
from ...sop import SOPPhase, VerificationResult
from ...tool import ToolBase, ToolChunk, ParamsBase

if TYPE_CHECKING:
    from ..storage import StorageBase


class _SubmitHandoverParams(ParamsBase):
    """Parameters for :class:`SubmitHandover`."""

    handover: str = Field(
        description=(
            "What you are handing to the following steps. They cannot "
            "see your files, your tools' output or this conversation — "
            "only this. Write it for someone who has seen none of your "
            "work."
        ),
    )


class _SubmitVerdictParams(ParamsBase):
    """Parameters for :class:`SubmitVerdict`."""

    passed: bool = Field(
        description="Whether the work meets what the step had to prove.",
    )
    message: str = Field(
        default="",
        description=(
            "If it does not pass, exactly what is wrong and what to do "
            "about it. This is handed to the author verbatim on their "
            "next attempt, so name the specific claims, files or values "
            "at fault."
        ),
    )


class _SOPSubmitBase(ToolBase):
    """Shared plumbing for the two submission tools.

    Both are built at agent assembly time with the run and step the
    session is working on, and both write the run state directly —
    there is no service in between, the same way the team tools reach
    storage themselves.
    """

    name: str
    description: str
    input_schema: dict[str, Any]
    is_concurrency_safe: bool = False
    is_read_only: bool = False
    is_state_injected: bool = False
    is_external_tool: bool = False
    is_mcp: bool = False
    mcp_name: str | None = None

    def __init__(
        self,
        storage: "StorageBase",
        user_id: str,
        sop_run_id: str,
        step_index: int,
    ) -> None:
        """Bind the run this tool writes to.

        Args:
            storage (`StorageBase`):
                Application storage.
            user_id (`str`):
                The owner user id.
            sop_run_id (`str`):
                The run the calling session belongs to.
            step_index (`int`):
                Which of the run's steps it is working on.
        """
        self._storage = storage
        self._user_id = user_id
        self._sop_run_id = sop_run_id
        self._step_index = step_index

    async def check_permissions(
        self,
        tool_input: dict[str, Any],
        context: PermissionContext,
    ) -> PermissionDecision:
        """Always allow — a step only ever carries the tool it needs.

        Args:
            tool_input (`dict[str, Any]`):
                The arguments the agent passed; ignored here.
            context (`PermissionContext`):
                The active permission context; ignored here.

        Returns:
            `PermissionDecision`:
                An ``ALLOW`` decision.
        """
        return PermissionDecision(
            behavior=PermissionBehavior.ALLOW,
            message=f"{self.name} is how a step reports, so it never asks.",
        )

    async def _write(self, apply: Any) -> ToolChunk | None:
        """Read the run, let *apply* change the step, and write it back.

        Returns an error chunk when the run has gone, and ``None`` when
        the write went through.
        """
        record = await self._storage.get_sop_run(
            self._user_id,
            self._sop_run_id,
        )
        if record is None or self._step_index >= len(record.state.steps):
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"{self.name}: run {self._sop_run_id!r} is no "
                            f"longer there to submit to."
                        ),
                    ),
                ],
                state=ToolResultState.ERROR,
            )
        apply(record.state.steps[self._step_index])
        await self._storage.update_sop_run(
            self._user_id,
            self._sop_run_id,
            record.state,
        )
        return None


class SubmitHandover(_SOPSubmitBase):
    """Hand this step's result to the steps that follow it."""

    name: str = "SubmitHandover"
    description: str = """Hand over the result of this step.

## When to Use This Tool
- You have finished what this step had to achieve. Call this as the \
last thing you do — your turn is not over until you have.

## Important
- The steps after this one cannot see your files, your tool output or \
this conversation. Whatever they need to know has to be in the handover \
text itself.
- Calling this does not mean the work is accepted; it is judged after \
you hand it over, and you may be asked again with a reason.
"""

    input_schema: dict = _SubmitHandoverParams.model_json_schema()

    async def __call__(self, handover: str) -> ToolChunk:
        """Record the handover on this step's run state.

        Args:
            handover (`str`):
                What the following steps are told.

        Returns:
            `ToolChunk`:
                A confirmation, or an error if the run has gone.
        """

        def _apply(step: Any) -> None:
            step.submission = [TextBlock(type="text", text=handover)]

        failure = await self._write(_apply)
        return failure or ToolChunk(
            content=[TextBlock(type="text", text="Handed over.")],
            state=ToolResultState.SUCCESS,
        )


class SubmitVerdict(_SOPSubmitBase):
    """Accept or refuse the work of the step under review."""

    name: str = "SubmitVerdict"
    description: str = """Decide whether this step's work is accepted.

## When to Use This Tool
- You have read the submission and judged it against what the step had \
to prove. Call this as the last thing you do — your turn is not over \
until you have.

## Important
- A refusal is handed to the author verbatim and is the only thing they \
get to work from, so say what is missing rather than that something is.
- Refusing sends the step back to be attempted again. Do not refuse over \
something the step was never asked to do.
"""

    input_schema: dict = _SubmitVerdictParams.model_json_schema()

    def __init__(self, *args: Any, verifier: str = "", **kwargs: Any) -> None:
        """Bind who the verdict is recorded under.

        Args:
            verifier (`str`, defaults to `""`):
                The reviewing agent's name, recorded on the verdict.
            *args:
                Forwarded to :class:`_SOPSubmitBase`.
            **kwargs:
                Forwarded to :class:`_SOPSubmitBase`.
        """
        super().__init__(*args, **kwargs)
        self._verifier = verifier

    async def __call__(self, passed: bool, message: str = "") -> ToolChunk:
        """File the verdict on this step's current attempt.

        Args:
            passed (`bool`):
                Whether the attempt is accepted.
            message (`str`, defaults to `""`):
                Why it was refused.

        Returns:
            `ToolChunk`:
                A confirmation, or an error if the run has gone.
        """

        def _apply(step: Any) -> None:
            step.verifications.append(
                VerificationResult(
                    passed=passed,
                    message=message,
                    verifier=self._verifier,
                ),
            )
            if passed:
                step.phase = SOPPhase.COMPLETED
            else:
                # The next attempt starts from the work, not from the
                # judging — the same rule ``SOPStepBase.record`` keeps.
                step.submission = None
                step.phase = SOPPhase.PENDING

        failure = await self._write(_apply)
        return failure or ToolChunk(
            content=[
                TextBlock(
                    type="text",
                    text="Accepted." if passed else "Sent back.",
                ),
            ],
            state=ToolResultState.SUCCESS,
        )
