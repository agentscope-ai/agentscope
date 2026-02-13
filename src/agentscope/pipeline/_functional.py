# -*- coding: utf-8 -*-
"""Functional counterpart for Pipeline"""
import asyncio
from copy import deepcopy
from typing import Any, AsyncGenerator, Tuple, Coroutine
from ..agent import AgentBase
from ..message import Msg, AudioBlock


async def sequential_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
) -> Msg | list[Msg] | None:
    """An async syntactic sugar pipeline that executes a sequence of agents
    sequentially. The output of the previous agent will be passed as the
    input to the next agent. The final output will be the output of the
    last agent.

    Example:
        .. code-block:: python

            agent1 = ReActAgent(...)
            agent2 = ReActAgent(...)
            agent3 = ReActAgent(...)

            msg_input = Msg("user", "Hello", "user")

            msg_output = await sequential_pipeline(
                [agent1, agent2, agent3],
                msg_input
            )

    Args:
        agents (`list[AgentBase]`):
            A list of agents.
        msg (`Msg | list[Msg] | None`, defaults to `None`):
            The initial input that will be passed to the first agent.
    Returns:
        `Msg | list[Msg] | None`:
            The output of the last agent in the sequence.
    """
    for agent in agents:
        msg = await agent(msg)
    return msg


async def fanout_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
    enable_gather: bool = True,
    **kwargs: Any,
) -> list[Msg]:
    """A fanout pipeline that distributes the same input to multiple agents.
    This pipeline sends the same message (or a deep copy of it) to all agents
    and collects their responses. Agents can be executed either concurrently
    using asyncio.gather() or sequentially depending on the enable_gather
    parameter.

    Example:
        .. code-block:: python

            agent1 = ReActAgent(...)
            agent2 = ReActAgent(...)
            agent3 = ReActAgent(...)

            msg_input = Msg("user", "Hello", "user")

            # Concurrent execution (default)
            results = await fanout_pipeline(
                [agent1, agent2, agent3],
                msg_input
            )

            # Sequential execution
            results = await fanout_pipeline(
                [agent1, agent2, agent3],
                msg_input,
                enable_gather=False
            )

    Args:
        agents (`list[AgentBase]`):
            A list of agents.
        msg (`Msg | list[Msg] | None`, defaults to `None`):
            The initial input that will be passed to all agents.
        enable_gather (`bool`, defaults to `True`):
            Whether to execute agents concurrently using `asyncio.gather()`.
            If False, agents are executed sequentially.
        **kwargs (`Any`):
            Additional keyword arguments passed to each agent during execution.

    Returns:
        `list[Msg]`:
            A list of response messages from each agent.
    """
    if enable_gather:
        tasks = [
            asyncio.create_task(agent(deepcopy(msg), **kwargs))
            for agent in agents
        ]

        return await asyncio.gather(*tasks)
    else:
        return [await agent(deepcopy(msg), **kwargs) for agent in agents]


async def stream_printing_messages(
    agents: list[AgentBase],
    coroutine_task: Coroutine,
    queue: asyncio.Queue | None = None,
    end_signal: str = "[END]",
    yield_speech: bool = False,
) -> AsyncGenerator[
    Tuple[Msg, bool] | Tuple[Msg, bool, AudioBlock | list[AudioBlock] | None],
    None,
]:
    """This pipeline will gather the printing messages from agents when
    execute the given coroutine task, and yield them one by one.
    Only the messages that are printed by `await self.print(msg)` in the agent
    will be forwarded to the message queue and yielded by this pipeline.

    .. note:: The boolean in the yielded tuple indicates whether the message
     is the last **chunk** for a streaming message, not the last message
     returned by the agent. That means, there'll be multiple tuples with
     `is_last_chunk=True` if the agent prints multiple messages.

    .. note:: The messages with the same ``id`` is considered as the same
     message, e.g., the chunks of a streaming message.

    Args:
        agents (`list[AgentBase]`):
            A list of agents whose printing messages will be gathered and
            yielded.
        coroutine_task (`Coroutine`):
            The coroutine task to be executed. This task should involve the
            execution of the provided agents, so that their printing messages
            can be captured and yielded.
        queue (`asyncio.Queue | None`, optional):
            Use this queue instead of creating a new one if provided.
        end_signal (`str`, defaults to `"[END]"`):
            A special signal to indicate the end of message streaming. When
            this signal is received from the message queue, the generator will
            stop yielding messages and exit the loop.
        yield_speech (`bool`, defaults to `False`):
            Whether to yield speech associated with the messages, if any.
            If `True` and a speech is attached when calling `await
            self.print()` in the agent, the yielded tuple will include the
            speech as the third element. If `False`, only the message and
            the boolean flag will be yielded.

    Yields:
        `Tuple[Msg, bool] | Tuple[Msg, bool, AudioBlock | list[AudioBlock] | \
        None]`:
            A tuple containing the message, a boolean indicating whether
            it's the last chunk in a streaming message, and optionally
            the associated speech (if `yield_speech` is `True`).
    """

    # Enable the message queue to get the intermediate messages
    queue = queue or asyncio.Queue()
    for agent in agents:
        # Use one queue to gather messages from all agents
        agent.set_msg_queue_enabled(True, queue)

    # Execute the agent asynchronously
    task = asyncio.create_task(coroutine_task)

    if task.done():
        await queue.put(end_signal)
    else:
        task.add_done_callback(lambda _: queue.put_nowait(end_signal))

    # Receive the messages from the agent's message queue
    while True:
        # The message obj, and a boolean indicating whether it's the last chunk
        # in a streaming message
        printing_msg = await queue.get()

        # Check if this is the end signal
        if isinstance(printing_msg, str) and printing_msg == end_signal:
            break

        if yield_speech:
            yield printing_msg
        else:
            msg, last, _ = printing_msg
            yield msg, last

    # Check exception after processing all messages
    exception = task.exception()
    if exception is not None:
        raise exception from None
