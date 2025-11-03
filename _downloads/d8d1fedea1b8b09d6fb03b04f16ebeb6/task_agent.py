# -*- coding: utf-8 -*-
"""
.. _agent:

Agent
=========================

In this tutorial, we first focus on introducing the ReAct agent in AgentScope,
then we briefly introduce how to customize your own agent from scratch.

ReAct Agent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In AgentScope, the ``ReActAgent`` class integrates various features into a final implementation, including

.. list-table:: Features of ``ReActAgent``
    :header-rows: 1

    * - Feature
      - Reference
    * - Support realtime steering
      -
    * - Support parallel tool calls
      -
    * - Support structured output
      -
    * - Support fine-grained MCP control
      - :ref:`mcp`
    * - Support agent-controlled tools management (Meta tool)
      - :ref:`tool`
    * - Support self-controlled long-term memory
      - :ref:`long-term-memory`
    * - Support automatic state management
      - :ref:`state`


Due to limited space, in this tutorial we only demonstrate the first three
features of ``ReActAgent`` class, leaving the others to the corresponding sections
listed above.

"""

import asyncio
import json
import os
from datetime import datetime
import time

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import TextBlock, Msg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, ToolResponse


# %%
# Realtime Steering
# ---------------------------------------
#
# The realtime steering allows user to interrupt the agent's reply at any time,
# which is implemented based on the asyncio cancellation mechanism.
#
# Specifically, when calling the ``interrupt`` method of the agent, it will
# cancel the current reply task, and execute the ``handle_interrupt`` method
# for postprocessing.
#
# .. hint:: With the feature of supporting streaming tool results in
#  :ref:`tool`, users can interrupt the tool execution if it takes too long or
#  deviates from user expectations by Ctrl+C in the terminal or calling the
#  ``interrupt`` method of the agent in your code.
#
# The interruption logic has been implemented in the ``AgentBase`` class as a
# basic feature, leaving a ``handle_interrupt`` method for users to customize the
# post-processing of interruption as follows:
#
# .. code-block:: python
#
#     # code snippet of AgentBase
#     class AgentBase:
#         ...
#         async def __call__(self, *args: Any, **kwargs: Any) -> Msg:
#             ...
#             reply_msg: Msg | None = None
#             try:
#                 self._reply_task = asyncio.current_task()
#                 reply_msg = await self.reply(*args, **kwargs)
#
#             except asyncio.CancelledError:
#                 # Catch the interruption and handle it by the handle_interrupt method
#                 reply_msg = await self.handle_interrupt(*args, **kwargs)
#
#             ...
#
#         @abstractmethod
#         async def handle_interrupt(self, *args: Any, **kwargs: Any) -> Msg:
#             pass
#
#
# In ``ReActAgent`` class, we return a fixed message "I noticed that you have
# interrupted me. What can I do for you?" as follows:
#
# .. figure:: ../../_static/images/interruption_en.gif
#     :width: 100%
#     :align: center
#     :class: bordered-image
#     :alt: Example of interruption
#
#     Example of interruption
#
# You can override it with your own implementation, for example, calling the LLM
# to generate a simple response to the interruption.
#
#
# Parallel Tool Calls
# ----------------------------------------
# ``ReActAgent`` supports parallel tool calls by providing a ``parallel_tool_calls``
# argument in its constructor.
# When multiple tool calls are generated, and ``parallel_tool_calls`` is set to ``True``,
# they will be executed in parallel by the ``asyncio.gather`` function.
#
# .. note:: The parallel tool execution in ``ReActAgent`` is implemented based on ``asyncio.gather``. Therefore, to maximize the effect of parallel tool execution, both the tool function itself and the logic within it must be asynchronous.
#
# .. note:: When running, please ensure that parallel tool calling is supported at the model level and the corresponding parameters are set correctly (can be passed through ``generate_kwargs``). For example, for the DashScope API, you need to set ``parallel_tool_calls`` to ``True``, otherwise parallel tool calling will not be possible.


# prepare a tool function
async def example_tool_function(tag: str) -> ToolResponse:
    """A sample example tool function"""
    start_time = datetime.now().strftime("%H:%M:%S.%f")

    # Sleep for 3 seconds to simulate a long-running task
    await asyncio.sleep(3)

    end_time = datetime.now().strftime("%H:%M:%S.%f")
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text=f"Tag {tag} started at {start_time} and ended at {end_time}. ",
            ),
        ],
    )


toolkit = Toolkit()
toolkit.register_tool_function(example_tool_function)

# Create an ReAct agent
agent = ReActAgent(
    name="Jarvis",
    sys_prompt="You're a helpful assistant named Jarvis.",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        # Preset the generation kwargs to enable parallel tool calls
        generate_kwargs={
            "parallel_tool_calls": True,
        },
    ),
    memory=InMemoryMemory(),
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
    parallel_tool_calls=True,
)


async def example_parallel_tool_calls() -> None:
    """Example of parallel tool calls"""
    # prompt the agent to generate two tool calls at once
    await agent(
        Msg(
            "user",
            "Generate two tool calls of the 'example_tool_function' function with tag as 'tag1' and 'tag2' AT ONCE so that they can execute in parallel.",
            "user",
        ),
    )


asyncio.run(example_parallel_tool_calls())

# %%
# Structured Output
# ----------------------------------------
# To generate a structured output, the ``ReActAgent`` instance receives a child class
# of the ``pydantic.BaseModel`` as the ``structured_model`` argument in its ``__call__`` function.
# Then we can get the structured output from the ``metadata`` field of the returned message.
#
#
# Taking introducing Einstein as an example:
#

# Create an ReAct agent
agent = ReActAgent(
    name="Jarvis",
    sys_prompt="You're a helpful assistant named Jarvis.",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        # Preset the generation kwargs to enable parallel tool calls
        generate_kwargs={
            "parallel_tool_calls": True,
        },
    ),
    memory=InMemoryMemory(),
    formatter=DashScopeChatFormatter(),
    toolkit=Toolkit(),
    parallel_tool_calls=True,
)


# The structured model
class Model(BaseModel):
    name: str = Field(description="The name of the person")
    description: str = Field(
        description="A one-sentence description of the person",
    )
    age: int = Field(description="The age")
    honor: list[str] = Field(description="A list of honors of the person")


async def example_structured_output() -> None:
    """The example structured output"""
    res = await agent(
        Msg(
            "user",
            "Introduce Einstein",
            "user",
        ),
        structured_model=Model,
    )
    print("\nThe structured output:")
    print(json.dumps(res.metadata, indent=4))


asyncio.run(example_structured_output())

# %%
# Customizing Agent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope provides two base classes, ``AgentBase`` and ``ReActAgentBase``, which
# differ in the abstract methods they define and the hooks they support.
# Specifically, the ``ReActAgentBase`` extends ``AgentBase`` with additional ``_reasoning`` and ``_acting``
# abstract methods, as well as their pre- and post- hooks.
#
# Developers can choose to inherit from either of these base classes based on their needs.
# We summarize the agent under ``agentscope.agent`` module as follows:
#
# .. list-table:: Agent classes in AgentScope
#     :header-rows: 1
#
#     * - Class
#       - Abstract Method
#       - Support Hooks
#       - Description
#     * - ``AgentBase``
#       - | ``reply``
#         | ``observe``
#         | ``print``
#         | ``handle_interrupt``
#       - | pre\_/post_reply
#         | pre\_/post_observe
#         | pre\_/post_print
#       - The base class for all agents, providing the basic interface and hooks.
#     * - ``ReActAgentBase``
#       - | ``reply``
#         | ``observe``
#         | ``print``
#         | ``handle_interrupt``
#         | ``_reasoning``
#         | ``_acting``
#       - | pre\_/post_reply
#         | pre\_/post_observe
#         | pre\_/post_print
#         | pre\_/post_reasoning
#         | pre\_/post_acting
#       - The abstract class for ReAct agent, extending ``AgentBase`` with reasoning and acting abstract methods and their hooks.
#     * - ``ReActAgent``
#       - \-
#       - | pre\_/post_reply
#         | pre\_/post_observe
#         | pre\_/post_print
#         | pre\_/post_reasoning
#         | pre\_/post_acting
#       - An implementation of ``ReActAgentBase``
#     * - ``UserAgent``
#       -
#       -
#       - A special agent that represents the user, used to interact with the agent
#
#
#
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`tool`
# - :ref:`hook`
#
