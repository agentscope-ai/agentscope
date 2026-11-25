# -*- coding: utf-8 -*-
"""Select an accepted final reply and join the remaining agent tasks."""
import asyncio
import inspect
from collections.abc import AsyncGenerator, Callable, Sequence
from copy import deepcopy

from ..agent import Agent
from ..event import UserInterruptEvent
from ..message import Msg
from ..types import ReplyFinishedReason


class RacePipeline:
    """Race independent agents on a safely repeatable task.

    Only a completed reply accepted by ``accept`` wins. Failures and rejected
    results do not abort other participants. If multiple replies are ready in
    one scheduler batch, participant order breaks the tie. Before returning,
    cancel and await the remaining replies, including their cooperative
    asynchronous cleanup. Cancellation cannot undo external side effects, so
    use independent agents on retrieval/analysis tasks that may safely overlap.

    This pipeline consumes intermediate events rather than exposing speculative
    answers. ``reply_stream`` yields only the selected final message, retaining
    its sender and reply ID. It does not support HITL continuation: a
    participant
    that parks for confirmation or external execution is interrupted through
    the Agent API and treated as a failed candidate. Participant contexts are
    retained; this pipeline neither resets them nor restores them across runs.
    """

    def __init__(
        self,
        agents: Sequence[Agent],
        accept: Callable[[Msg], bool] | None = None,
        max_concurrency: int = 2,
    ) -> None:
        """Initialize a race.

        Args:
            agents (`Sequence[Agent]`): Independent, uniquely named agents.
            accept (`Callable[[Msg], bool] | None`, optional): Synchronous
                application acceptance predicate. Defaults to accepting every
                completed reply. Exceptions in the predicate abort the race.
            max_concurrency (`int`, optional): Maximum active agent replies.

        Raises:
            ValueError: If participants or the concurrency limit are invalid.
        """
        participants = tuple(agents)
        if not participants:
            raise ValueError("At least one agent is required.")
        if len({agent.name for agent in participants}) != len(participants):
            raise ValueError("Participant agent names must be unique.")
        if len({id(agent.state) for agent in participants}) != len(
            participants,
        ):
            raise ValueError(
                "Participants must have independent agent states.",
            )
        if type(max_concurrency) is not int or max_concurrency < 1:
            raise ValueError("max_concurrency must be a positive integer.")
        if accept is not None and not callable(accept):
            raise ValueError("accept must be a synchronous callable.")
        self.agents = participants
        self.accept = accept
        self.max_concurrency = max_concurrency
        self._running = False

    async def _run_candidate(
        self,
        agent: Agent,
        inputs: Msg | list[Msg],
        admission: asyncio.Semaphore,
    ) -> Msg:
        """Run one reply, closing parked replies through their public API."""
        async with admission:
            result = await agent.reply(deepcopy(inputs))
            if result.finished_reason is None:
                await agent.reply(UserInterruptEvent(reply_id=result.id))
                raise RuntimeError(
                    f"Agent {agent.name!r} requires external input; "
                    "a race cannot resume parked replies.",
                )
            if result.finished_reason != ReplyFinishedReason.COMPLETED:
                raise RuntimeError(
                    f"Agent {agent.name!r} ended with "
                    f"{result.finished_reason!r} instead of completing.",
                )
            return result

    @staticmethod
    async def _join(tasks: list[asyncio.Task]) -> None:
        """Cancel unfinished replies and retrieve all task outcomes."""
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def reply(self, inputs: Msg | list[Msg]) -> Msg:
        """Return the first accepted completed reply after loser cleanup.

        Args:
            inputs (`Msg | list[Msg]`): New input copied for every participant.

        Returns:
            `Msg`: The selected participant's final message.

        Raises:
            TypeError: If inputs are not messages or the predicate returns a
                non-boolean value. HITL control events cannot resume a race.
            RuntimeError: If this pipeline already has an active invocation.
            ExceptionGroup: If all participants fail or are rejected.
        """
        if not isinstance(inputs, Msg) and not (
            isinstance(inputs, list)
            and inputs
            and all(isinstance(msg, Msg) for msg in inputs)
        ):
            raise TypeError(
                "A race requires a message or nonempty message list.",
            )
        if self._running:
            raise RuntimeError("This race already has an active invocation.")
        self._running = True
        tasks: list[asyncio.Task] = []
        errors: dict[int, Exception] = {}
        try:
            admission = asyncio.Semaphore(self.max_concurrency)
            for agent in self.agents:
                tasks.append(
                    asyncio.create_task(
                        self._run_candidate(agent, inputs, admission),
                    ),
                )
            indices = {task: index for index, task in enumerate(tasks)}
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(
                    pending,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in sorted(done, key=indices.__getitem__):
                    index = indices[task]
                    if task.cancelled():
                        errors[index] = RuntimeError(
                            "Agent "
                            f"{self.agents[index].name!r} was cancelled.",
                        )
                        continue
                    error = task.exception()
                    if error is not None:
                        if not isinstance(error, Exception):
                            raise error
                        errors[index] = error
                        continue
                    result = task.result()
                    accepted = (
                        self.accept(result)
                        if self.accept is not None
                        else True
                    )
                    if not isinstance(accepted, bool):
                        if inspect.iscoroutine(accepted):
                            accepted.close()
                        raise TypeError(
                            "accept must return a bool synchronously.",
                        )
                    if accepted:
                        return result
                    errors[index] = RuntimeError(
                        f"Agent {self.agents[index].name!r} was rejected.",
                    )
            raise ExceptionGroup(
                "No participant produced an accepted completed reply.",
                [errors[index] for index in sorted(errors)],
            )
        finally:
            cleanup = asyncio.create_task(self._join(tasks))
            cancelled = False
            try:
                while not cleanup.done():
                    try:
                        await asyncio.shield(cleanup)
                    except asyncio.CancelledError:
                        cancelled = True
                cleanup.result()
            finally:
                self._running = False
            if cancelled:
                raise asyncio.CancelledError

    async def reply_stream(
        self,
        inputs: Msg | list[Msg],
    ) -> AsyncGenerator[Msg, None]:
        """Yield the selected final reply after every owned task has joined.

        Args:
            inputs (`Msg | list[Msg]`): New input for the independent replies.

        Yields:
            `Msg`: Exactly one accepted final message, without speculative
                participant events. Cancellation has the same cleanup contract
                as :meth:`reply`.
        """
        yield await self.reply(inputs)
