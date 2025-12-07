# -*- coding: utf-8 -*-
"""
.. _agent_skill:

智能体技能
============================

`智能体技能（Agent skill） <https://claude.com/blog/skills>`_ 是 Anthropic 提出的一种提升智能体在特定任务上能力的方法。

AgentScope 通过 ``Toolkit`` 类提供了对智能体技能的内置支持，让开发者可以注册和管理智能体技能。

相关 API 如下：

.. list-table:: ``Toolkit`` 类中的智能体技能 API
    :header-rows: 1

    * - API
      - 描述
    * - ``register_agent_skill``
      - 从指定目录注册智能体技能
    * - ``remove_agent_skill``
      - 根据名称移除已注册的智能体技能
    * - ``get_agent_skill_prompt``
      - 获取所有已注册智能体技能的提示词，可以附加到智能体的系统提示词中

本节将演示如何注册智能体技能并在 ReActAgent 类中使用它们。
"""
import os

from agentscope.agent import ReActAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit

# %%
# 注册智能体技能
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# 首先，我们需要准备一个智能体技能目录，该目录需要遵循 `Anthropic blog <https://claude.com/blog/skills>`_ 中指定的要求。
#
# .. note:: 技能目录必须包含一个 ``SKILL.md`` 文件，其中包含 YAML 前置元数据和指令说明。
#
# 这里我们创建一个示例技能目录 ``sample_skill``，包含以下文件：
#
# .. code-block:: markdown
#
#   ---
#   name: sample_skill
#   description: 用于演示的示例智能体技能
#   ---
#
#   # 示例技能
#   ...
#

os.makedirs("sample_skill", exist_ok=True)
with open("sample_skill/SKILL.md", "w", encoding="utf-8") as f:
    f.write(
        """---
name: sample_skill
description: 用于演示的示例智能体技能
---

# 示例技能
...
""",
    )

# %%
# 然后，我们可以使用 ``Toolkit`` 类的 ``register_agent_skill`` API 注册技能。
#

toolkit = Toolkit()

toolkit.register_agent_skill("sample_skill")

# %%
# 之后，我们可以使用 ``get_agent_skill_prompt`` API 获取所有已注册智能体技能的提示词

agent_skill_prompt = toolkit.get_agent_skill_prompt()
print("智能体技能提示词:")
print(agent_skill_prompt)

# %%
# 当然，我们也可以在创建 ``Toolkit`` 实例时自定义提示词模板。

custom_toolkit = Toolkit(
    # 向智能体/大语言模型介绍如何使用技能的指令
    agent_skill_instruction="<system-info>为你提供了一组技能，每个技能都在一个目录中，并由 SKILL.md 文件进行描述。</system-info>",
    # 用于格式化每个技能提示词的模板，必须包含 {name}、{description} 和 {dir} 字段
    agent_skill_template="- {name}(in directory '{dir}'): {description}",
)

custom_toolkit.register_agent_skill("sample_skill")
agent_skill_prompt = custom_toolkit.get_agent_skill_prompt()
print("自定义智能体技能提示词:")
print(agent_skill_prompt)

# %%
# 在 ReActAgent 中集成智能体技能
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# AgentScope 中的 `ReActAgent` 类会自动将智能体技能提示词附加到系统提示词中。
#
# 我们可以按如下方式创建一个带有已注册智能体技能的 ReAct 智能体：
#
# .. important:: 使用智能体技能时，智能体必须配备文本文件读取或 shell 命令工具，以便访问 `SKILL.md` 文件中的技能指令。
#

agent = ReActAgent(
    name="Friday",
    sys_prompt="你是一个名为 Friday 的智能助手。",
    model=DashScopeChatModel(
        model_name="qwen3-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
    ),
    memory=InMemoryMemory(),
    formatter=DashScopeChatFormatter(),
    toolkit=toolkit,
)

print("带有智能体技能的系统提示词:")
print(agent.sys_prompt)
