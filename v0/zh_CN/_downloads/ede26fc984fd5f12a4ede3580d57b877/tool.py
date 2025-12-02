# -*- coding: utf-8 -*-
"""
.. _tools:

工具
====================

背景介绍
--------------------

工具调用根据实现方式不同分为两种类型：

.. image:: https://img.alicdn.com/imgextra/i3/O1CN01iizvjY1UjKCE3q5FR_!!6000000002553-0-tps-1830-792.jpg
   :align: center
   :alt: 两种不同实现方式
   :width: 80%

- 基于 prompt 的工具调用: 开发人员在 prompt 中介绍工具，并从 LLM 响应中手动提取工具调用。
- 基于 API 的工具调用：开发人员提供 JSON Schema 格式的工具描述，LLM API 将直接返回特定格
 式的工具调用。

AgentScope 同时兼容两种方式，在本教程中，将展示如何使用 AgentScope 中内置的工具函数，
以及如何创建自定义工具。
"""
import json

import agentscope
from agentscope.message import Msg
from agentscope.models import DashScopeChatWrapper

# %%
# 内置工具函数
# --------------------------
# AgentScope 提供了一个 `ServiceToolkit` 模块，支持以下功能:
#
# - 自动解析工具函数为 JSON Schema 格式
# - 提供一套默认的调用格式,
# - 参数检查和调用函数
#
# 在使用 `ServiceToolkit` 之前,我们可以先看一下 `agentscope.service` 模块中可用的工具。
#

from agentscope.service import get_help, ServiceResponse, ServiceExecStatus

get_help()

# %%
# 以上所有函数都是用 Python 实现的，可以通过调用 `add` 方法注册到 `ServiceToolkit` 中。
# `ServiceToolkit` 将自动将工具函数解析为 JSON Schema 格式。
#

from agentscope.service import ServiceToolkit
from agentscope.service import bing_search, execute_shell_command

toolkit = ServiceToolkit()
toolkit.add(execute_shell_command)

# %%
# 注意,一些工具函数的参数（例如 api_key）应该由开发人员处理。
# 你可以直接在 add 方法中以关键字参数的形式传递这些参数，保留其他参数留给智能体填写。

toolkit.add(bing_search, api_key="xxx")

print("工具说明:")
print(toolkit.tools_instruction)

# %%
# 内置的默认调用格式:
#

print(toolkit.tools_calling_format)

# %%
# 自动生成的工具函数 JSON Schema 格式说明:
#
print(json.dumps(toolkit.json_schemas, indent=2))


# %%
# 基于 prompt 的工具调用
# --------------------------
# 在基于 prompt 的工具调用中，开发人员需要:
# - 在 prompt 中介绍工具和调用格式
# - 从 LLM 响应中手动提取工具调用
#
# 关于从字符串中提取工具调用，可以参考 :ref:`structured-output` 章节中的解析器介绍。
# `ServiceToolkit.parse_and_call_func`方法接受的工具调用格式如下：

from agentscope.message import ToolUseBlock

tool_call = ToolUseBlock(
    type="tool_use",
    id="xxx",
    name="bing_search",
    input={"query": "AgentScope"},
)

# %%
# `ServiceToolkit` 注册工具函数后，可以将其集成到智能体中。
#
# AgentScope 提供了 `ReActAgent` 智能体类来使用工具，只需要将 `ServiceToolkit` 对象传递给这个智能体。
# 有关该智能体的实现细节，请参阅 :ref:`builtin_agent`。
#
# .. note:: `ReActAgent` 采用的是 prompt 本地拼接和解析的方式调用工具，而不是通过
#  模型 API 的 tools API 进行调用。关于 tools API 的使用，请参考 :ref:`tools-api`.

from agentscope.agents import ReActAgent

agentscope.init(
    model_configs={
        "config_name": "my-qwen-max",
        "model_type": "dashscope_chat",
        "model_name": "qwen-max",
    },
)

agent = ReActAgent(
    name="Friday",
    model_config_name="my-qwen-max",
    service_toolkit=toolkit,
    sys_prompt="你是一个名为 Friday 的助手。",
)

msg_task = Msg("user", "帮我计算一下 1615114134*4343434343", "user")

res = agent(msg_task)

# %%
# 基于 API 的工具调用
# -------------------------
# 基于 API 的工具调用中，开发者只需要提供 JSON Schema 格式的工具描述。但是不同的模型 API
# - 要求的 JSON Schema 格式不同
# - 将工具调用，执行结果集成到prompt中的方式也不同
#
# .. image:: ./api_based_tool_calling.png
#    :align: center
#    :alt: API-based tool calling
#    :width: 80%
#
# 上图以 OpenAI 为例展示了基于 API 的工具调用如何工作。通过`agentscope.formatter`和
# `ModelResponse`的封装，我们隐藏了 API 的格式要求和转化细节，开发者只需要专注应用的开发。
# 开发者需要了解的是：
# - `ServiceToolkit` 支持自动将函数解析为标准 JSON Schema 格式
# - `Formatter` 模块支持将 JSON Schema 和消息列表转化成 API 需要的格式
# - 不同模型 API 返回的工具调用会在 `ModelResponse` 中统一格式（`ToolUseBlock`)
#
# .. tip:: 新的智能体类 `ReActAgentV2` 已上线，使用基于 API 的工具调用方法。
#  其使用方法与 `ReActAgent` 保持高度一致。
#
# .. note:: 目前 `Formatter` 模块只有 `format_chat` 函数支持工具调用，
# `format_multi_agent` 函数的支持还在开发中。
#
# .. note:: tools API目前还不支持流式返回，相关功能处于开发中。
#
# 我们以 DashScope API 为例展示如何使用 tools API.

from agentscope.formatters import DashScopeFormatter
from agentscope.message import TextBlock, ToolUseBlock, ToolResultBlock

model = DashScopeChatWrapper(
    config_name="_",
    model_name="qwen-max",
)

# %%
# 图中的 3 -> 4 步，将 JSON schemas 和 messages 转化成模型 API 需要的格式:
msgs = [
    Msg("user", "Help me to execute shell cmd 'whoami'", "user"),
]

formatted_msgs = DashScopeFormatter.format_chat(msgs)
formatted_schemas = DashScopeFormatter.format_tools_json_schemas(
    toolkit.json_schemas,
)
print(json.dumps(formatted_msgs, indent=4, ensure_ascii=False))

# %%
# 图中 5 -> 6 -> 7 步，获取模型响应:

response = model(formatted_msgs, tools=formatted_schemas)
print("tool_calls:", json.dumps(response.tool_calls, indent=4))

# %%
# 图中 8 步，创建一个包含工具调用的新消息:


# 创建带有工具调用的新消息
content = []
if response.text:
    content.append(TextBlock(type="text", text=response.text))
if response.tool_calls:
    content.extend(response.tool_calls)
msgs.append(Msg("assistant", content, "assistant", echo=True))

# 执行工具调用
msg_execution = toolkit.parse_and_call_func(
    response.tool_calls,
    tools_api_mode=True,  # 基于 API 的工具调用必须为 True
)

# %%
# 图中 9 步，将执行结果添加到消息列表中:

msgs.append(msg_execution)

# %%
# 在完成了一轮工具调用之后，让我们再次尝试将包含工具调用和结果的消息列表格式化:

formatted_msgs = DashScopeFormatter.format_chat(msgs)
print(json.dumps(formatted_msgs, indent=4, ensure_ascii=False))

# %%
# 目前，我们已经完成了基于 API 的工具调用全过程。在智能体内的具体实现可以参
# 考 `agentscope.agents.ReActAgentV2`。

# %%
# 在 ServiceToolkit 中使用 MCP
# -------------------------------
# AgentScope 支持集成 MCP (Model Context Protocol) 服务器，使模型和工具具有增强的功能。
# 您可以使用 `add_mcp_servers` 方法将 MCP 服务器添加到 `ServiceToolkit` 中，并在其中指定每个服务器的配置。
# 请注意，MCP 要求 Python 版本>=3.10。

configs = {
    "mcpServers": {
        "puppeteer": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-puppeteer"],
        },
    },
}

# %%
# 将 MCP 服务器配置添加到 ServiceToolkit
# `toolkit.add_mcp_servers(server_configs=configs)`
#
# 创建工具函数
# --------------------------
# 自定义工具函数必须遵循以下规则:
#
# - 参数使用 typing 指定类型
# - 使用 Google 风格书写完整的 docstring
# - 函数返回值必须用 `ServiceResponse` 包装
#


def new_function(arg1: str, arg2: int) -> ServiceResponse:
    """简单介绍该函数。

    Args:
        arg1 (`str`):
            对 arg1 的简单描述
        arg2 (`int`):
            对 arg2 的简单描述
    """
    return ServiceResponse(
        status=ServiceExecStatus.SUCCESS,
        content="完成!",
    )
