# -*- coding: utf-8 -*-
"""
.. _plan:

Plan
=========================

The plan module allows agents to decompose complex tasks into manageable
sub-tasks formally, and finish them step by step, which features:

- Support manual specification of plans
- Support agents to adjust (change/abandon/create new) plans dynamically, ask user for help, and restore the historical plan
- Support plan visualization and monitoring

.. note:: The current plan module has the following limitations, and we are working on improving them:
 - The plan must be executed sequentially
"""
from agentscope.plan import PlanNotebook

# %%
# Plan Notebook
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# The `PlanNotebook` class is the core of the plan module, responsible for providing
#
# - plan related tool functions
# - hint messages to guide the agent to finish the plan
#
# With the `list_tools` method, you can obtain all the tools provided by the `PlanNotebook` class.

plan_notebook = PlanNotebook()

print("The tools provided by PlanNotebook:")
for tool in plan_notebook.list_tools():
    print(tool.__name__)

#


# %%
# Working with ReActAgent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
#
