# -*- coding: utf-8 -*-
"""
.. _plan:

Plan
=========================

The Plan Module enables agents to formally break down complex tasks into manageable sub-tasks and execute them systematically. Key features include:

- Support **manual plan specification**
- Comprehensive plan management capabilities:
   - **Creating, modifying, abandoning, and restoring** plans
   - **Switching** between multiple plans
   - **Gracefully handling interruptions** by temporarily suspending plans to address user queries or urgent tasks
- **Real-time visualization and monitoring** of plan execution

.. note:: The current plan module has the following limitations, and we are working on improving them:

 - The subtasks in a plan must be executed sequentially

Specifically, the plan module works by

- providing tool functions for plan management
- inserting hint messages to guide the ReAct agent to complete the plan

The following figure illustrates how the plan module works with the ReAct agent:

.. figure:: ../../_static/images/plan.png
    :width: 90%
    :alt: Plan module
    :class: bordered-image
    :align: center

    How the plan module works with the ReAct agent

"""
import asyncio
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.plan import PlanNotebook, Plan, SubTask

# %%
# PlanNotebook
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# The `PlanNotebook` class is the core of the plan module, responsible for providing
#
# - plan-related tool functions
# - hint messages to guide the agent to finish the plan
#
# The `PlanNotebook` class can be instantiated with the following parameters:
#
# .. list-table:: Parameters of the `PlanNotebook` constructor
#   :header-rows: 1
#
#   * - Name
#     - Type
#     - Description
#   * - ``max_subtasks``
#     - ``int | None``
#     - The maximum number of subtasks allowed in a plan, infinite if None
#   * - ``plan_to_hint``
#     - ``Callable[[Plan | None], str | None] | None``
#     - The function to generate hint message based on the current plan. If not provided, a default `DefaultPlanToHint` object will be used.
#   * - ``storage``
#     - ``PlanStorageBase | None``
#     - The plan storage. If not provided, a default in-memory storage will be used.
#
# The ``plan_to_hint`` callable object is the most important part of the
# `PlanNotebook` class, also serves as the interface for prompt engineering.
# We have built a default `DefaultPlanToHint` class that can be used directly.
# Developers are encouraged to providing their own ``plan_to_hint`` function
# for better performance.
#
# The ``storage`` is to store historical plans, allowing agent to
# retrieve and restore historical plans. Developers are encouraged to
# implement their own plan storage by inheriting the ``PlanStorageBase`` class.
# If not provided, a default in-memory storage will be used.
#
# .. tip:: The ``PlanStorageBase`` class inherits from the ``StateModule``
#  class, so that the plan storage will also be saved and loaded by the
#  session management.
#
# The core attributes and methods of the `PlanNotebook` class are summarized
# as follows:
#
# .. list-table:: Core attributes and methods of the `PlanNotebook` class
#    :header-rows: 1
#
#    * - Type
#      - Name
#      - Description
#    * - attribute
#      - ``current_plan``
#      - The current plan that the agent is executing
#    * -
#      - ``storage``
#      - The storage for historical plans, used for retrieving and restoring historical plans
#    * -
#      - ``plan_to_hint``
#      - A callable object that takes the current plan as input and generates a hint message to guide the agent to finish the plan
#    * - method
#      - ``list_tools``
#      - List all the tool functions provided by the `PlanNotebook` class
#    * -
#      - ``get_current_hint``
#      - Get the hint message for the current plan, which will call the ``plan_to_hint`` function
#    * -
#      - | ``create_plan``,
#        | ``view_subtasks``,
#        | ``revise_current_plan``,
#        | ``update_subtask_state``,
#        | ``finish_subtask``,
#        | ``finish_plan``,
#        | ``view_historical_plans``,
#        | ``recover_historical_plan``
#      - The tool functions that allows the agent to manage the plan and subtasks
#    * -
#      - ``register_plan_change_hook``
#      - Register a hook function that will be called when the plan is changed, used to plan visualization and monitoring
#    * -
#      - ``remove_plan_change_hook``
#      - Remove a registered plan change hook function
#
# The ``list_tools`` method is a quick way to obtain all tool functions, so that you can register them to the agent's toolkit.

plan_notebook = PlanNotebook()


async def list_tools() -> None:
    """List the tool functions provided by PlanNotebook."""
    print("The tools provided by PlanNotebook:")
    for tool in plan_notebook.list_tools():
        print(tool.__name__)


asyncio.run(list_tools())


# %%
# Working with ReActAgent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# The `ReActAgent` in AgentScope has integrated the plan module by a ``plan_notebook`` parameter in its constructor.
# Once provided, the agent will
#
# - be equipped with the plan management tool functions, and
# - be inserted with the hint messages at the beginning of each reasoning step
#
# There are two ways to use the plan module with the `ReActAgent`:
#
# - Manual plan specification: Users can manually create a plan by calling the ``create_plan`` tool function, and initialize the `ReActAgent` with the plan notebook.
# - Agent-managed plan execution: The agent will create and manage the plan by itself, by calling the plan management tool functions.
#
# Manual Plan Specification
# ---------------------------------
# Manually creating a plan is straightforward by calling the ``create_plan`` tool function.
# The following is an example of manually creating a plan to conduct a comprehensive research on the LLM-empowered agent.
#
async def manual_plan_specification() -> None:
    """Manual plan specification example."""
    await plan_notebook.create_plan(
        name="Research on Agent",
        description="Conduct a comprehensive research on the LLM-empowered agent.",
        expected_outcome="A Markdown format report answer three questions: 1. What's agent? 2. What's the current state of the art of agent? 3. What's the future trend of agent?",
        subtasks=[
            SubTask(
                name="Search agent-related survey papers",
                description=(
                    "Search for survey parers on multiple sources, including "
                    "Google Scholar, arXiv, and Semantic Scholar. Must be "
                    "published after 2021 and have more than 50 citations."
                ),
                expected_outcome="A paper list in Markdown format",
            ),
            SubTask(
                name="Read and summarize the papers",
                description=(
                    "Read the papers found in the previous step, and "
                    "summarize the key points, including the definition, "
                    "taxonomy, challenges, and key directions."
                ),
                expected_outcome="A summary of the key points in Markdown format",
            ),
            SubTask(
                name="Research on recent advances of large company",
                description=(
                    "Research on the recent advances of large companies, "
                    "including Google, Microsoft, OpenAI, Anthropic, Alibaba "
                    "and Meta. Find the official blogs or news articles."
                ),
                expected_outcome="A recent advances of large company ",
            ),
            SubTask(
                name="Write a report",
                description=(
                    "Write a report based on the previous steps, and answer "
                    "the three questions in the expected outcome."
                ),
                expected_outcome=(
                    "A Markdown format report answer three questions: 1. "
                    "What's agent? 2. What's the current state of the art of "
                    "agent? 3. What's the future trend of agent?"
                ),
            ),
        ],
    )

    print("The current hint message:\n")
    msg = await plan_notebook.get_current_hint()
    print(f"{msg.name}: {msg.content}")


asyncio.run(manual_plan_specification())

# %%
# After creating the plan, you can initialize the `ReActAgent` with the
# plan notebook as follows:

agent = ReActAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant.",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    formatter=DashScopeChatFormatter(),
    plan_notebook=plan_notebook,
)

# %%
# Agent-Managed Plan Execution
# ---------------------------------
# Agent can also create and manage the plan by itself, by calling the plan management tool functions.
# We just need to initialize the `ReActAgent` with the plan notebook as follows:
#

agent = ReActAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant.",
    model=DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    formatter=DashScopeChatFormatter(),
    plan_notebook=PlanNotebook(),
)

# %%
# After that, we can build a loop to interact with the agent as follows.
# Once the task is complex, the agent will create a plan by itself and
# execute the plan step by step.
#
# .. code-block:: python
#     :caption: Build conversation with the plan agent
#
#     async def interact_with_agent() -> None:
#         """Interact with the plan agent."""
#         user = UserAgent(name="user")
#
#         msg = None
#         while True:
#             msg = await user(msg)
#             if msg.get_text_content() == "exit":
#                 break
#             msg = await agent(msg)
#
#     asyncio.run(interact_with_agent())
#
#
# Plan Visualization and Monitoring
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# AgentScope supports real-time visualization and monitoring of the plan
# execution by the plan change hook function.
#
# They will be triggered when the plan is changed by calling the tool
# functions. A template of the plan change hook function is as follows:
#


def plan_change_hook_template(self: PlanNotebook, plan: Plan) -> None:
    """A template of the plan change hook function.

    Args:
        self (`PlanNotebook`):
            The PlanNotebook instance.
        plan (`Plan`):
            The current plan instance (after the change).
    """
    # Forward the plan to the frontend for visualization or other processing
