# -*- coding: utf-8 -*-
"""
.. _structured-output:

结构化输出
==========================

如下图所示，AgentScope 支持以下两种结构化输出方式：

- 工具 API：构造一个函数，其输入参数是所需要的结构化数据的字段，然后要求大模型调用该函数，从而获得结构化数据
- 文本解析：调用大模型 API 获得纯文本数据，然后本地从纯文本中解析出结构化数据

.. image:: https://img.alicdn.com/imgextra/i4/O1CN01TLx5qg1tcmx3cKCNN_!!6000000005923-55-tps-661-391.svg
   :width: 100%
   :alt: 两种不同结构化输出方式

两种方式的优缺点如下：

.. list-table::
    :header-rows: 1

    * - 方式
      - 优点
      - 缺点
    * - 工具 API
      - 1. 模型 **自主** 决定何时调用该函数/产生结构化输出，能够很好与 ReAct 算法结合
        2. 数据解析发生在大模型 API 提供方，本地开发难度低
        3. 支持基于 JSON Schema 的复杂约束条件
      - 需要支持工具调用的大模型 API
    * - 文本解析
      - 1. 简单易用
        2. 可以根据模型能力、所需结构化数据的不同，调整格式和解析方法
      - 1. 依赖模型能力，可能一直无法产生符合要求的结构化数据
        2. 模型 **被动** 产生结构化数据，由开发者决定何时提示大模型产生结构化数据


下面我们详细介绍 AgentScope 如何支持两种不同的解析方式。

工具 API
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

工具 API 方式将工具调用和结构化输出结合在一起，例如我们需要 `"thought"`，`"choice"`，`"number"`
三个字段，那么我们可以构建如下的函数
"""

from typing import Literal
from pydantic import BaseModel, Field
import json


def generate_response(
    thought: str,
    choice: Literal["apple", "banana"],
    number: int,
) -> None:
    pass


# %%
# 这里函数签名起到了提供约束条件的作用，模型在正确调用该函数时，我们就可以获得对应的结构化数据。
#
# 在了解基本思路后，进一步考虑到一些复杂的约束条件无法使用 Python 的类型注解来表达，
# 在 AgentScope 中，我们支持通过 Pydantic 的 `BaseModel` 的子类来定义复杂的约束条件。
# 例如下面的两个类


class Model1(BaseModel):
    name: str = Field(
        min_length=0,
        max_length=20,
        description="姓名",
    )
    description: str = Field(
        min_length=0,
        max_length=200,
        description="简短的介绍",
    )
    age: int = Field(
        ge=0,
        le=100,
        description="年龄",
    )


class Model2(BaseModel):
    choice: Literal["apple", "banana"] = Field(description="你的选择")


# %%
#
# AgentScope 中的 `ReActAgentV2` 会将 `BaseModel` 子类的 JSON Schema 和一个名
# 为 `generate_response` 函数的 schema 结合在一起，生成一个新的 schema，在模型调用
# 该函数的时候，可以利用该函数的 Schema 来约束模型的输出。
#
# 例如下面我们展示如何使用 `ReActAgentV2` 实现 ReAct 算法和结构化输出的结合

from agentscope.agents import ReActAgentV2
from agentscope.service import ServiceToolkit
from agentscope.message import Msg
import agentscope

agentscope.init(
    model_configs={
        "config_name": "my_config",
        "model_type": "dashscope_chat",
        "model_name": "qwen-max",
    },
)

toolkit = ServiceToolkit()

agent = ReActAgentV2(
    name="Friday",
    model_config_name="my_config",
    service_toolkit=toolkit,
)

msg1 = Msg("user", "介绍下爱因斯坦", "user")
res_msg = agent(msg1, structured_model=Model1)

print("结构化输出的内容: ", res_msg.metadata)

# %%
# 可以通过切换不同的 `structured_model` 来实现不同的结构化输出

msg2 = Msg("user", "选择一种水果", "user")
res_msg = agent(msg2, structured_model=Model2)

print("结构化输出的内容: ", res_msg.metadata)

# %%
# 为了观察 `ReActAgentV2` 中如何动态的构造函数的 schema，这里我们去掉一个清理结构化输出设置的
# hook 函数，从而能够打印出对应的加工后的函数 schema

# 清空记忆
agent.memory.clear()
# 去掉清理结构化输出的 hook 函数
agent.remove_hook("post_reply", "as_clear_structured_output")

# 观察当前目标函数的 schema
print(
    json.dumps(
        toolkit.json_schemas[agent._finish_function],
        indent=4,
        ensure_ascii=False,
    ),
)

# %%
# 现在我们调用一次智能体，然后观察目标函数的 schema 变化情况

res_msg = agent(msg1, structured_model=Model1)

print(
    json.dumps(
        toolkit.json_schemas[agent._finish_function],
        indent=4,
        ensure_ascii=False,
    ),
)

# %%
# 这里我们可以看出，`generate_response` 函数的 schema 已经与 `Model1` 类的 schema 结合在了一起，
# 因此，当大模型调用该工具的时候，也就产生了对应的结构化数据。
#
# .. tip:: 更多实现细节可以参考 `ReActAgentV2` 的 `源码`_
#
# .. _源码: https://github.com/modelscope/agentscope/blob/main/src/agentscope/agents/_react_agent_v2.py
#
# 文本解析
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# AgentScope 中的 `parsers` 模块提供了多种不同的解析器类，开发者可以根据所需结构化数据的不同选用合适的解析器。
#
# 以 Markdown 格式为例，我们展示如何构建一个简单的智能体，并使用 `MarkdownJsonDictParser` 类从文本中解析出字典类型的结构化数据。
#
# 构建解析器
# -------------------
#
# `MarkdownJsonDictParser` 要求输入的文本将结构化数据包裹在 Markdown 的代码快中，以`\`\`\``作为开头和结尾，并从中解析出对应的字典数据。

from agentscope.model import ModelResponse
from agentscope.parsers import MarkdownJsonDictParser


parser = MarkdownJsonDictParser(
    content_hint='{"thought": "你的想法", "speak": "你对用户说的话"}',
    required_keys=["thought", "speak"],
)


# %%
# 解析器将根据你的输入生成一个格式说明。你可以在提示中使用 `format_instruction` 属性来指导 LLM 生成所需的输出。

print(parser.format_instruction)

# %%
# 解析输出
# -------------------
# 当从 LLM 接收到输出时，使用 `parse` 方法来提取结构化数据。
# 它接收一个 `agentscope.model.ModelResponse` 对象作为输入，解析 `text` 字段的值，并在 `parsed` 字段中返回解析后的字典。

dummy_response = ModelResponse(
    text="""```json
{
    "thought": "我应该向用户打招呼",
    "speak": "嗨!我能为您做些什么?"
}
```""",
)

print(f"解析前parsed字段: {dummy_response.parsed}")

parsed_response = parser.parse(dummy_response)

print(f"解析后parsed字段: {parsed_response.parsed}")
print(f"parsed字段类型: {type(parsed_response.parsed)}")

# %%
# 错误处理
# -------------------
# 如果LLM的输出与预期格式不匹配，解析器将抛出一个包含详细信息的错误。
# 开发者可以将错误信息反馈给 LLM，引导其生成正确格式的输出。
#

error_response = ModelResponse(
    text="""```json
{
    "thought": "我应该向用户打招呼"
}
```""",
)

try:
    parsed_response = parser.parse(error_response)
except Exception as e:
    print(e)

# %%
# 进阶用法
# -------------------
# 复杂结构化输出
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
# 要求 LLM 直接生成 JSON 字典可能具有挑战性，特别是当 JSON 内容很复杂时（例如代码片段、嵌套结构）。
# 在这种情况下，你可以使用更高级的解析器来指导 LLM 生成所需的输出。
# 这里是一个更复杂的解析器示例，可以处理代码片段。
#

from agentscope.parsers import RegexTaggedContentParser

parser = RegexTaggedContentParser(
    format_instruction="""按以下格式作答:
<thought>你的想法</thought>
<number>这里放一个随机数字</number>
<code>你的python代码</code>
""",
    try_parse_json=True,  # 会尝试将每个键值解析为JSON对象，如果失败则保留为字符串
    required_keys=[  # 解析字典中的必需键
        "thought",
        "number",
        "code",
    ],
)

print(parser.format_instruction)

# %%
# `RegexTaggedContentParser` 支持使用正则表达式匹配文本中的标记内容并返回解析后的字典。
#
# .. note:: `RegexTaggedContentParser` 的解析输出是一个字典，因此 key 必须唯一。
#  可以通过设置 `tagged_content_pattern` 参数来更改正则表达式模式。

import json

dummy_response = ModelResponse(
    text="""<thought>打印当前日期</thought>
<number>42</number>
<code>import datetime
print(datetime.datetime.now())
</code>
""",
)

parsed_response = parser.parse(dummy_response)

print("解析响应的类型: ", type(parsed_response.parsed))
print("number的类型: ", type(parsed_response.parsed["number"]))
print(json.dumps(parsed_response.parsed, indent=4, ensure_ascii=False))

# %%
# 自动后处理
# ^^^^^^^^^^^^^^^^^^^^
#
# 在解析后的字典中，不同的键可能需要不同的后处理步骤。
# 例如，在狼人杀游戏中，LLM 扮演预言家的角色，输出应该包含以下键值:
#
# - `thought`: 预言家的想法
# - `speak`: 预言家的发言
# - `use_ability`: 一个布尔值，表示预言家是否应该使用其能力
#
# 在这种情况下，`thought` 和 `speak` 内容应该存储在智能体的记忆中，以确保智能体行为/策略的一致性。
# `speak` 内容应该暴露给其它智能体或玩家。
# `use_ability` 键应该能在主流程中访问到，从而确定游戏下一步的操作（例如是否使用能力）。
#
# AgentScope 通过以下参数来自动对解析后的字典进行后处理。
#
# - `keys_to_memory`: 应存储在智能体记忆中的键
# - `keys_to_content`: 应存储在返回消息的 content 字段中的键，会暴露给其它智能体
# - `keys_to_metadata`: 应存储在返回消息的元数据（metadata）字段中的键
#
# .. note:: 如果提供了一个字符串，解析器将从解析后的字典中提取给定键的值。如果提供了一个字符串列表，将创建一个包含给定键的子字典。
#
# 下面是使用 `MarkdownJsonDictParser` 自动后处理解析后字典的示例。
#

parser = MarkdownJsonDictParser(
    content_hint='{"thought": "你的想法", "speak": "你对用户说的话", "use_ability": "是否使用能力"}',
    keys_to_memory=["thought", "speak"],
    keys_to_content="speak",
    keys_to_metadata="use_ability",
)

dummy_response = ModelResponse(
    text="""```json
{
    "thought": "我应该...",
    "speak": "我不会使用我的能力",
    "use_ability": false
}```
""",
)

parsed_response = parser.parse(dummy_response)

print("解析后的响应: ", parsed_response.parsed)
print("存储到记忆", parser.to_memory(parsed_response.parsed))
print("存储到消息 content 字段: ", parser.to_content(parsed_response.parsed))
print("存储到消息 metadata 字段: ", parser.to_metadata(parsed_response.parsed))

# %%
# 这里我们展示如何创建一个智能体，它在 `reply` 方法中通过以下步骤实现自动化的后处理。
#
# 1. 在提示中放入格式说明，以指导 LLM 生成所需的输出
# 2. 解析 LLM 的返回值
# 3. 使用 `to_memory`、`to_content` 和 `to_metadata` 方法后处理解析后的字典
#
# .. tip:: 通过更改不同的解析器，智能体可以适应不同的场景，并以各种格式生成结构化输出。
#

from agentscope.model import DashScopeChatWrapper
from agentscope.agents import AgentBase


class Agent(AgentBase):
    def __init__(self):
        self.name = "Alice"
        super().__init__(name=self.name)

        self.sys_prompt = f"你是一个名为{self.name}的有用助手。"

        self.model = DashScopeChatWrapper(
            config_name="_",
            model_name="qwen-max",
        )

        self.parser = MarkdownJsonDictParser(
            content_hint='{"thought": "你的想法", "speak": "你对用户说的话", "use_ability": "是否使用能力"}',
            keys_to_memory=["thought", "speak"],
            keys_to_content="speak",
            keys_to_metadata="use_ability",
        )

        self.memory.add(Msg("system", self.sys_prompt, "system"))

    def reply(self, msg):
        self.memory.add(msg)

        prompt = self.model.format(
            self.memory.get_memory(),
            # 指示模型按要求的格式作答
            Msg("system", self.parser.format_instruction, "system"),
        )

        response = self.model(prompt)

        parsed_response = self.parser.parse(response)

        self.memory.add(
            Msg(
                name=self.name,
                content=self.parser.to_memory(parsed_response.parsed),
                role="assistant",
            ),
        )

        return Msg(
            name=self.name,
            content=self.parser.to_content(parsed_response.parsed),
            role="assistant",
            metadata=self.parser.to_metadata(parsed_response.parsed),
        )
