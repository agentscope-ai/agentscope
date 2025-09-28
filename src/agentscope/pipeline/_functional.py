# -*- coding: utf-8 -*-
"""Functional counterpart for Pipeline"""
import asyncio
from copy import deepcopy
from typing import Any, AsyncGenerator, Tuple
from ..agent import AgentBase
from ..message import Msg


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
    agent: AgentBase,
    msg: Msg | list[Msg] | None = None,
    **kwargs: Any,
) -> AsyncGenerator[Tuple[Msg, bool], None]:
    """An async generator pipeline that yields the printing messages from the
    agent. Only the messages that are printed by `await self.print(msg)` will
    be forwarded to the message queue and yielded by this pipeline.

    .. note:: The boolean in the yielded tuple indicates whether the message
     is the last **chunk** for a streaming message, not the last message
     returned by the agent. That means, there'll be multiple tuples with
     `is_last_chunk=True` if the agent prints multiple messages.

    .. note:: The messages with the same ``id`` is considered as the same
     message, e.g., the chunks of a streaming message.

    Args:
        agent (`AgentBase`):
            An agent instance.
        msg (`Msg | list[Msg] | None`, optional):
            The message that will be passed to the agent.
        **kwargs (`Any`):
            Additional keyword arguments passed to the agent during execution.

    Returns:
        `AsyncGenerator[Tuple[Msg, bool], None]`:
            An async generator that yields tuples of (message, is_last_chunk).
            The `is_last_chunk` boolean indicates whether the message is the
            last chunk in a streaming message.
    """

    # Enable the message queue to get the intermediate messages
    agent.set_msg_queue_enabled(True)

    # Execute the agent asynchronously
    asyncio.create_task(
        agent(msg, **kwargs),
    )

    # Receive the messages from the agent's message queue
    while True:
        # The message obj, and a boolean indicating whether it's the last chunk
        # in a streaming message
        printing_msg = await agent.msg_queue.get()

        # End the loop when the message is None
        if printing_msg is None:
            break

        yield printing_msg
