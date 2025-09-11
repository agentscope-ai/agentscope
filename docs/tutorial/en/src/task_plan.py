# -*- coding: utf-8 -*-
"""
.. _plan:

Plan
=========================

The plan module allows agents to decompose complex tasks into manageable
sub-tasks formally, and finish them step by step, which features:

- Support **manual specification** of plans
- Support agents to adjust (change/abandon/create new) plans **dynamically**, ask user for help, and restore the historical plan
- Support plan **visualization and monitoring**

.. note:: The current plan module has the following limitations, and we are working on improving them:
 - The plan must be executed sequentially
"""
from agentscope.plan import PlanNotebook, Plan

# %%
# Plan Notebook
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# The `PlanNotebook` class is the core of the plan module, responsible for providing
#
# - plan related tool functions
# - hint messages to guide the agent to finish the plan
#
# The `PlanNotebook` class supports to customize the plan instance by the
# following parameters in its constructor:
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
# The plan storage is used to store historical plans, which allows the agent to
# retrieve and restore historical plans when needed.
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
#      - The tool functions that allows the agent to manage the plan and subtasks
#    * -
#      - ``register_plan_change_hook``
#      - Register a hook function that will be called when the plan is changed, used to plan visualization and monitoring
#    * -
#      - ``remove_plan_change_hook``
#      - Remove a registered plan change hook function
#
# The ``list_tools`` method is a quick way to obtain all tool functions, so that you can register them to the agent's toolkit.
#

plan_notebook = PlanNotebook()

print("The tools provided by PlanNotebook:")
for tool in plan_notebook.list_tools():
    print(tool.__name__)

# %%
# Working with ReActAgent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~



# %%
# Plan Visualization and Monitoring
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
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

