# -*- coding: utf-8 -*-
"""
.. _agent_skill:

Agent Skill
============================

`Agent skill <https://claude.com/blog/skills>`_ is an approach proposed by
Anthropic to improve agent capabilities on specific tasks.

AgentScope provides built-in support for Agent Skills through the ``Toolkit``
class, allowing users to easily register and manage agent skills.

The related APIs are as follows:

.. list-table:: Agent skill API in ``Toolkit`` class
    :header-rows: 1

    * - API
      - Description
    * - ``register_agent_skill``
      - Register agent skills from a given directory.
    * - ``remove_agent_skill``
      - Remove a registered agent skill by name.
    * - ``get_agent_skill_prompt``
      - Get the prompt for all registered agent skills, which can be
        attached to the system prompt for the agent.

In this section we demonstrate how to register agent skills and use them in an
ReAct agent.
"""
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit

# %%
# Registering Agent Skills
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# First, we need to prepare an agent skill directory, which follows the
# requirements specified in the `Anthropic blog <https://claude.com/blog/skills>`_.
#
# .. note:: The skill directory must contain a ``SKILL.md`` file containing
#  YAML frontmatter and instructions.
#
# Here, we fake an example skill directory ``sample_skill`` with the following files:
#
# .. code-block:: markdown
#
#   ---
#   name: sample_skill
#   description: A sample agent skill for demonstration.
#   ---
#
#   # Sample Skill
#   ...
#

os.makedirs("sample_skill", exist_ok=True)
with open("sample_skill/SKILL.md", "w", encoding="utf-8") as f:
    f.write(
        """---
name: sample_skill
description: A sample agent skill for demonstration.
---

# Sample Skill
...
""",
    )

# %%
# Then, we can register the skill using the ``register_agent_skill`` API of
# the ``Toolkit`` class.
#

toolkit = Toolkit()

toolkit.register_agent_skill("sample_skill")

# %%
# After that, we can get the prompt for all registered agent skills using the
# ``get_agent_skill_prompt`` API

agent_skill_prompt = toolkit.get_agent_skill_prompt()
print("Agent Skill Prompt:")
print(agent_skill_prompt)

# %%
# Of course, we can customize the prompt template when creating the ``Toolkit``
# instance.

toolkit = Toolkit(
    # The instruction that introduces how to use the skill to the agent/llm
    agent_skill_instruction="<system-info>You're provided a collection of skills, each in a directory and described by a SKILL.md file.</system-info>\n",
    # The template for formatting each skill's prompt, must contain
    # {name}, {description}, and {dir} fields
    agent_skill_template="- {name}({dir}): {description}",
)

toolkit.register_agent_skill("sample_skill")
agent_skill_prompt = toolkit.get_agent_skill_prompt()
print("Customized Agent Skill Prompt:")
print(agent_skill_prompt)

# %%
# Integrating Agent Skills with ReActAgent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# The `ReActAgent` class in AgentScope will attach the agent skill prompt to
# the system prompt automatically.
#
# We can create a ReAct agent with the registered agent skills as follows:
#
# .. important:: When using agent skills, the agent must be equipped with text
#  file reading or shell command tools to access the skill instructions in
#  `SKILL.md` files.
#

agent = ReActAgent(
    name="Friday",
    sys_prompt="You are a helpful assistant named Friday.",
    model=DashScopeChatModel(
        model_name="qwen3-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    memory=InMemoryMemory(),
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
)

print("Agent's System Prompt with Agent Skills:")
print(agent.sys_prompt)
