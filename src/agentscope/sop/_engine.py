# -*- coding: utf-8 -*-
"""Running a SOP.

The engine does three things and knows nothing else: it walks the steps
in order, hands resumption events to whichever one parked, and spends the
attempt budget. It has no idea what a verifier is — it reads each step's
:class:`~._state.SOPStepRunState` and decides from that alone.

It also owns the run. A definition is handed in and never written to; a
step gets its own slice of the run on every call and nothing more.
"""
from typing import AsyncGenerator

from ._schema import SOP
from ._state import SOPPhase, SOPRunState
from ..event import (
    AgentEvent,
    CustomEvent,
    ExternalExecutionResultEvent,
    UserConfirmResultEvent,
    UserInterruptEvent,
)
from ..message import Msg, TextBlock, UserMsg


class SOPEngine:
    """One run of a :class:`~._schema.SOP`, shaped like an agent.

    Feed it, watch the events, and when something needs a person the
    stream simply ends — nothing stays suspended. Come back with the
    answer and it picks up from the state.
    """

    def __init__(self, sop: SOP, state: SOPRunState | None = None) -> None:
        """Initialize the engine.

        Args:
            sop (`SOP`):
                The procedure to run. Never written to.
            state (`SOPRunState | None`, optional):
                A stored run to carry on from. Omit to start a new one.
                Only the SOP's own state is restored: an executor that
                keeps state of its own — an :class:`~..agent.Agent` does
                — is restored by whoever built it, before the SOP is
                handed here.

        Raises:
            `ValueError`:
                If the state belongs to a different SOP.
        """
        if state is not None and state.sop_id != sop.id:
            raise ValueError(
                f"State belongs to SOP {state.sop_id}, not {sop.id}.",
            )
        self.sop = sop
        self.state = state or SOPRunState(sop_id=sop.id)
        for step in sop.steps:
            # A stored run was read back as the base record; a step that
            # keeps more than that gets it back through its own type.
            stored = self.state.steps.get(step.id)
            self.state.steps[step.id] = (
                step.state_type.model_validate(stored.model_dump())
                if stored is not None
                else step.state_type(step_id=step.id)
            )

    @property
    def phase(self) -> SOPPhase:
        """Where the run stands overall."""
        return self.state.phase

    async def reply_stream(
        self,
        inputs: Msg
        | list[Msg]
        | UserConfirmResultEvent
        | UserInterruptEvent
        | ExternalExecutionResultEvent
        | None = None,
    ) -> AsyncGenerator[AgentEvent | Msg, None]:
        """Run the procedure, streaming what happens.

        Named after :meth:`~..agent.Agent.reply_stream` so a SOP goes
        wherever an agent goes.

        Args:
            inputs:
                What starts the run, or the answer a parked step was
                waiting for.

        Yields:
            `AgentEvent | Msg`:
                Everything its steps produced on the way.
        """
        interrupting = isinstance(inputs, UserInterruptEvent)
        resuming = interrupting or isinstance(
            inputs,
            (UserConfirmResultEvent, ExternalExecutionResultEvent),
        )
        if not resuming and inputs is not None:
            self.state.inputs = (
                [inputs] if isinstance(inputs, Msg) else list(inputs)
            )

        for index, step in enumerate(self.sop.steps):
            record = self.state.steps[step.id]
            if record.phase is SOPPhase.COMPLETED:
                continue
            if record.phase is SOPPhase.FAILED:
                return

            while True:
                # The answer goes to the step that parked; a fresh
                # attempt gets what the run knows so far.
                if not resuming:
                    record.given = self._handover(index)
                yield CustomEvent(
                    name="sop.step.started",
                    value={
                        "step_id": step.id,
                        "attempt": len(record.verifications) + 1,
                    },
                )
                async for event in step.reply_stream(
                    inputs if resuming else record.given,
                    record,
                ):
                    yield event
                yield CustomEvent(
                    name="sop.step.ended",
                    value={"step_id": step.id, "phase": record.phase.value},
                )
                inputs, resuming = None, False

                if interrupting:
                    # The parked reply was closed; nothing is retried.
                    return
                if record.phase is SOPPhase.AWAITING:
                    # Let go of the stream rather than hold a coroutine
                    # open; the caller comes back with an answer.
                    return
                if record.phase is SOPPhase.COMPLETED:
                    break
                if len(record.verifications) >= step.max_attempts:
                    record.phase = SOPPhase.FAILED
                    return

    def _handover(self, index: int) -> list[Msg]:
        """What a step is given to work from.

        The run's own inputs for the first step, and what the one before
        handed over for the rest. Nothing else crosses: a step reads its
        predecessor's account, not its files or its conversation.
        """
        if index == 0:
            return list(self.state.inputs)
        previous = self.sop.steps[index - 1]
        return [
            UserMsg(
                name="sop",
                content=[
                    TextBlock(text=f'<handover from="{previous.subject}">'),
                    *(self.state.steps[previous.id].submission or []),
                    TextBlock(text="</handover>"),
                ],
            ),
        ]
