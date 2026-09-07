# -*- coding: utf-8 -*-
"""Running a SOP.

The engine does three things and knows nothing else: it walks the steps
in order, hands resumption events to whichever one parked, and spends the
attempt budget. It has no idea what a verifier is — it reads
:attr:`~._state.SOPStepRunState.verifications` and decides from that
alone.
"""
from typing import AsyncGenerator

from ._schema import SOP, StepInputs
from ._state import (
    SOPRunState,
    SOPRunStatus,
    SOPStepState,
)
from ..event import (
    AgentEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import Msg, UserMsg
from .._utils._common import _generate_id, _generate_timestamp


class SOPEngine:
    """Runs a :class:`~._schema.SOP`, shaped like an agent.

    Feed it, watch the events, and when something needs a person the
    stream simply ends — nothing stays suspended. Come back with the
    answer and it picks up from the state.
    """

    def __init__(self, sop: SOP, state: SOPRunState | None = None) -> None:
        """Initialize the engine.

        Args:
            sop (`SOP`):
                The procedure to run.
            state (`SOPRunState | None`, optional):
                A stored run to carry on from, as handed out by
                :attr:`state`. Omit to start a new one. Only the SOP's
                own state is restored: an executor that keeps state of
                its own — an :class:`~..agent.Agent` does — is restored
                by whoever built it, before the SOP is handed here.

        Raises:
            `ValueError`:
                If the state belongs to a different SOP.
        """
        self.sop = sop
        self._id = _generate_id()
        self._created_at = _generate_timestamp()
        self._inputs: list[Msg] = []
        if state is not None:
            if state.sop_id != sop.id:
                raise ValueError(
                    f"State belongs to SOP {state.sop_id}, not {sop.id}.",
                )
            self._id = state.id
            self._inputs = list(state.inputs)
            self._created_at = state.created_at
            for step in sop.steps:
                if (record := state.steps.get(step.id)) is not None:
                    step.state = record

    @property
    def state(self) -> SOPRunState:
        """The run so far, gathered from the steps.

        A live view rather than a copy: the step records in it are the
        steps' own. Call ``model_dump()`` on it for a snapshot to store.
        """
        return SOPRunState(
            sop_id=self.sop.id,
            id=self._id,
            inputs=self._inputs,
            steps={step.id: step.state for step in self.sop.steps},
            created_at=self._created_at,
        )

    @property
    def status(self) -> SOPRunStatus:
        """Where the run stands overall."""
        return self.state.status

    async def reply_stream(
        self,
        inputs: StepInputs = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Run the procedure, streaming what happens.

        Named after :meth:`~..agent.Agent.reply_stream` so a SOP goes
        wherever an agent goes.

        Args:
            inputs (`StepInputs`, optional):
                What starts the run, or the answer a parked step was
                waiting for.

        Yields:
            `AgentEvent | Msg`:
                Everything its steps produced on the way.
        """
        resuming = isinstance(
            inputs,
            (
                UserConfirmResultEvent,
                UserInterruptEvent,
                ExternalExecutionResultEvent,
            ),
        )
        if not resuming and inputs is not None:
            self._inputs = (
                [inputs] if isinstance(inputs, Msg) else list(inputs)
            )

        for index, step in enumerate(self.sop.steps):
            if step.state.state is SOPStepState.COMPLETED:
                continue
            if step.state.state is SOPStepState.FAILED:
                return

            while True:
                # The answer goes to the step that parked; everyone else
                # gets what the run knows so far.
                async for event in step.reply_stream(
                    inputs if resuming else self._handover(index),
                ):
                    yield event
                inputs, resuming = None, False

                if step.state.state is SOPStepState.AWAITING:
                    # Let go of the stream rather than hold a coroutine
                    # open; the caller comes back with an answer.
                    return
                if step.state.state is SOPStepState.COMPLETED:
                    break
                if len(step.state.verifications) >= step.max_attempts:
                    step.state.state = SOPStepState.FAILED
                    return

    def _handover(self, index: int) -> list[Msg]:
        """What a step is given to work from.

        The run's own inputs for the first step, and what the one before
        handed over for the rest. Nothing else crosses: a step reads its
        predecessor's account, not its files or its conversation.
        """
        if index == 0:
            return list(self._inputs)
        previous = self.sop.steps[index - 1]
        return [
            UserMsg(
                name="sop",
                content=(
                    f'<handover from="{previous.subject}">\n'
                    f"{previous.state.submission}\n</handover>"
                ),
            ),
        ]
