# -*- coding: utf-8 -*-
"""
.. _pipeline:

Pipeline
========================

For multi-agent orchestration, AgentScope provides the ``agentscope.pipeline`` module
as syntax sugar for chaining agents together, including

- **MsgHub**: a message hub for broadcasting messages among multiple agents
- **sequential_pipeline** and **SequentialPipeline**: a functional and class-based implementation that chains agents in a sequential manner
- **fanout_pipeline** and **FanoutPipeline**: a functional and class-based implementation that distributes the same input to multiple agents
- **stream_printing_messages**: a utility function that convert the printing messages from agent(s) into an async generator

"""

import os, asyncio

from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.agent import ReActAgent
from agentscope.pipeline import MsgHub, stream_printing_messages


# %%
# Broadcasting with MsgHub
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# The ``MsgHub`` class is an **async context manager**, receiving a list of agents as its participants.
# When one participant generates a replying message, all other participants will receive this message by calling their ``observe`` method.
#
# That means within a ``MsgHub`` context, developers don't need to manually send a replying message from one agent to another.
# The broadcasting is automatically handled.
#
# Here we create four agents: Alice, Bob, Charlie and David.
# Then we start a meeting with Alice, Bob and Charlie by introducing themselves.
# Note David is not included in this meeting.


def create_agent(name: str, age: int, career: str) -> ReActAgent:
    """Create agent object by the given information."""
    return ReActAgent(
        name=name,
        sys_prompt=f"You're {name}, a {age}-year-old {career}",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
        ),
        formatter=DashScopeMultiAgentFormatter(),
    )


alice = create_agent("Alice", 50, "teacher")
bob = create_agent("Bob", 35, "engineer")
charlie = create_agent("Charlie", 28, "designer")
david = create_agent("David", 30, "developer")

# %%
# Then we start a meeting and let them introduce themselves without manual message passing:
#
# .. hint:: The message in ``announcement`` will be broadcasted to all participants when entering the ``MsgHub`` context.
#


async def example_broadcast_message():
    """Example of broadcasting messages with MsgHub."""

    # Create a message hub
    async with MsgHub(
        participants=[alice, bob, charlie],
        announcement=Msg(
            "user",
            "Now introduce yourself in one sentence, including your name, age and career.",
            "user",
        ),
    ) as hub:
        # Group chat without manual message passing
        await alice()
        await bob()
        await charlie()


asyncio.run(example_broadcast_message())

# %%
# Now let's check if Bob, Charlie and David received Alice's message.
#


async def check_broadcast_message():
    """Check if the messages are broadcast correctly."""
    user_msg = Msg(
        "user",
        "Do you know who's Alice, and what she does? Answer me briefly.",
        "user",
    )

    await bob(user_msg)
    await charlie(user_msg)
    await david(user_msg)


asyncio.run(check_broadcast_message())

# %%
# Now we observe that Bob and Charlie know Alice and her profession, while David has no idea
# about Alice since he is not included in the ``MsgHub`` context.
#
#
# Dynamic Participant Management
# ---------------------------------------
# Additionally, ``MsgHub`` supports to dynamically manage participants by the following methods:
#
# - ``add``: add one or multiple agents as new participants
# - ``delete``: remove one or multiple agents from participants, and they will no longer receive broadcasted messages
# - ``broadcast``: broadcast a message to all current participants
#
# .. note:: The newly added participants will not receive the previous messages.
#
# .. code-block:: python
#
#       async with MsgHub(participants=[alice]) as hub:
#           # Add new participants
#           hub.add(david)
#
#           # Remove participants
#           hub.delete(alice)
#
#           # Broadcast to all current participants
#           await hub.broadcast(
#               Msg("system", "Now we begin to ...", "system"),
#           )
#
#
# Pipeline
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Pipeline serves as a syntax sugar for multi-agent orchestration.
#
# Currently, AgentScope provides three main pipeline implementations:
#
# 1. **Sequential Pipeline**: Execute agents one by one in a predefined order
# 2. **Fanout Pipeline**: Distribute the same input to multiple agents and collect their responses
# 3. **Stream Printing Messages**: Convert the printing messages from an agent into an async generator
#
# Sequential Pipeline
# ------------------------
# The sequential pipeline executes agents one by one, where the output of the previous agent
# becomes the input of the next agent.
#
# For example, the two following code snippets are equivalent:
#
#
# .. code-block:: python
#     :caption: Code snippet 1: Manually call agents one by one
#
#     msg = None
#     msg = await alice(msg)
#     msg = await bob(msg)
#     msg = await charlie(msg)
#     msg = await david(msg)
#
#
# .. code-block:: python
#     :caption: Code snippet 2: Use sequential pipeline
#
#     from agentscope.pipeline import sequential_pipeline
#     msg = await sequential_pipeline(
#         # List of agents to be executed in order
#         agents=[alice, bob, charlie, david],
#         # The first input message, can be None
#         msg=None
#     )
#

# %%
# Fanout Pipeline
# ------------------------
# The fanout pipeline distributes the same input message to multiple agents simultaneously and collects all their responses. This is useful when you want to gather different perspectives or expertise on the same topic.
#
# For example, the two following code snippets are equivalent:
#
#
# .. code-block:: python
#     :caption: Code snippet 3: Manually call agents one by one
#
#     from copy import deepcopy
#
#     msgs = []
#     msg = None
#     for agent in [alice, bob, charlie, david]:
#         msgs.append(await agent(deepcopy(msg)))
#
#
# .. code-block:: python
#     :caption: Code snippet 4: Use fanout pipeline
#
#     from agentscope.pipeline import fanout_pipeline
#     msgs = await fanout_pipeline(
#         # List of agents to be executed in order
#         agents=[alice, bob, charlie, david],
#         # The first input message, can be None
#         msg=None,
#         enable_gather=False,
#     )
#
# .. note::
#     The ``enable_gather`` parameter controls the execution mode of the fanout pipeline:
#
#     - ``enable_gather=True`` (default): Executes all agents **concurrently** using ``asyncio.gather()``. This provides better performance for I/O-bound operations like API calls, as agents run in parallel.
#     - ``enable_gather=False``: Executes agents **sequentially** one by one. This is useful when you need deterministic execution order or want to avoid overwhelming external services with concurrent requests.
#
#     Choose concurrent execution for better performance, or sequential execution for predictable ordering and resource control.
#
# .. tip::
#     By combining ``MsgHub`` and ``sequential_pipeline`` or ``fanout_pipeline``, you can create more complex workflows very easily.
#
#
# Stream Printing Messages
# -------------------------------------
# The ``stream_printing_messages`` function converts the printing messages from agent(s) into an async generator.
# It will help you to obtain the intermediate messages from the agent(s) in a streaming way.
#
# It accepts a list of agents and a coroutine task, then returns an async generator that yields tuples of ``(Msg, bool)``,
# containing the printing message during execution of the coroutine task.
#
# Note the messages with the same ``id`` are considered as the same message, and the ``last`` flag indicates whether it's the last chunk of this message.
#
# Taking the following code snippet as an example:


async def run_example_pipeline() -> None:
    """Run an example of streaming printing messages."""
    agent = create_agent("Alice", 20, "student")

    # We disable the terminal printing to avoid messy outputs
    agent.set_console_output_enabled(False)

    async for msg, last in stream_printing_messages(
        agents=[agent],
        coroutine_task=agent(
            Msg("user", "Hello, who are you?", "user"),
        ),
    ):
        print(msg, last)
        if last:
            print()


asyncio.run(run_example_pipeline())


# %%
# Advanced Pipeline Features
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# Additionally, for reusability, we also provide a class-based implementation:
#
# .. code-block:: python
#     :caption: Using SequentialPipeline class
#
#     from agentscope.pipeline import SequentialPipeline
#
#     # Create a pipeline object
#     pipeline = SequentialPipeline(agents=[alice, bob, charlie, david])
#
#     # Call the pipeline
#     msg = await pipeline(msg=None)
#
#     # Reuse the pipeline with different input
#     msg = await pipeline(msg=Msg("user", "Hello!", "user"))
#
#
# .. code-block:: python
#     :caption: Using FanoutPipeline class
#
#     from agentscope.pipeline import FanoutPipeline
#
#     # Create a pipeline object
#     pipeline = FanoutPipeline(agents=[alice, bob, charlie, david])
#
#     # Call the pipeline
#     msgs = await pipeline(msg=None)
#
#     # Reuse the pipeline with different input
#     msgs = await pipeline(msg=Msg("user", "Hello!", "user"))
#
