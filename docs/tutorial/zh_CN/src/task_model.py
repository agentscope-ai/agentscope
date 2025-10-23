# -*- coding: utf-8 -*-
"""
.. _model:

模型
====================

在本教程中，我们介绍 AgentScope 中集成的模型 API、如何使用它们，以及如何集成新的模型 API。
AgentScope 目前支持的模型 API 和模型提供商包括：

.. list-table::
    :header-rows: 1

    * - API
      - 类
      - 兼容
      - 流式
      - 工具
      - 视觉
      - 推理
    * - OpenAI
      - ``OpenAIChatModel``
      - vLLM, DeepSeek
      - ✅
      - ✅
      - ✅
      - ✅
    * - DashScope
      - ``DashScopeChatModel``
      -
      - ✅
      - ✅
      - ✅
      - ✅
    * - Anthropic
      - ``AnthropicChatModel``
      -
      - ✅
      - ✅
      - ✅
      - ✅
    * - Gemini
      - ``GeminiChatModel``
      -
      - ✅
      - ✅
      - ✅
      - ✅
    * - Ollama
      - ``OllamaChatModel``
      -
      - ✅
      - ✅
      - ✅
      - ✅

.. note:: 当使用 vLLM 时，需要在部署时为不同模型配置相应的工具调用参数，例如 ``--enable-auto-tool-choice``、``--tool-call-parser`` 等参数。更多详情请参考 `vLLM 官方文档 <https://docs.vllm.ai/en/latest/features/tool_calling.html>`_。

.. note:: 兼容 OpenAI API 的模型（例如 vLLM 部署的模型），推荐使用 ``OpenAIChatModel``，并通过 ``client_args={"base_url": "http://your-api-endpoint"}`` 参数指定 API 端点。例如：

    .. code-block:: python

        OpenAIChatModel(client_args={"base_url": "http://localhost:8000/v1"})

.. note:: 模型的行为参数（如温度、最大长度等）可以通过 ``generate_kwargs`` 参数在构造函数中提前设定。例如：

    .. code-block:: python

        OpenAIChatModel(generate_kwargs={"temperature": 0.3, "max_tokens": 1000})

为了提供统一的模型接口，上述所有类均被统一为：

- ``__call__`` 函数的前三个参数是 ``messages``，``tools`` 和 ``tool_choice``，分别是输入消息，工具函数的 JSON schema，以及工具选择的模式。
- 非流式返回时，返回类型是 ``ChatResponse`` 实例；流式返回时，返回的是 ``ChatResponse`` 的异步生成器。

.. note:: 不同的模型 API 在输入消息格式上有所不同，AgentScope 通过 formatter 模块处理消息的转换，请参考 :ref:`format`。

``ChatResponse`` 包含大模型生成的推理/文本/工具使用内容、身份、创建时间和使用信息。
"""
import asyncio
import json
import os

from agentscope.message import TextBlock, ToolUseBlock, ThinkingBlock, Msg
from agentscope.model import ChatResponse, DashScopeChatModel

response = ChatResponse(
    content=[
        ThinkingBlock(
            type="thinking",
            thinking="我应该在 Google 上搜索 AgentScope。",
        ),
        TextBlock(type="text", text="我将在 Google 上搜索 AgentScope。"),
        ToolUseBlock(
            type="tool_use",
            id="642n298gjna",
            name="google_search",
            input={"query": "AgentScope"},
        ),
    ],
)

print(response)

# %%
# 以 ``DashScopeChatModel`` 为例，调用和返回结果如下：


async def example_model_call() -> None:
    """使用 DashScopeChatModel 的示例。"""
    model = DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        stream=False,
    )

    res = await model(
        messages=[
            {"role": "user", "content": "你好！"},
        ],
    )

    # 您可以直接使用响应内容创建 ``Msg`` 对象
    msg_res = Msg("Friday", res.content, "assistant")

    print("LLM 返回结果:", res)
    print("作为 Msg 的响应:", msg_res)


asyncio.run(example_model_call())

# %%
# 流式返回
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 要启用流式返回，请在模型的构造函数中将 ``stream`` 参数设置为 ``True``。
# 流式返回中，``__call__`` 方法将返回一个 **异步生成器**，该生成器迭代返回 ``ChatResponse`` 实例。
#
# .. note:: AgentScope 中的流式返回结果为 **累加式**，这意味着每个 chunk 中的内容包含所有之前的内容加上新生成的内容。
#


async def example_streaming() -> None:
    """使用流式模型的示例。"""
    model = DashScopeChatModel(
        model_name="qwen-max",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        stream=True,
    )

    generator = await model(
        messages=[
            {
                "role": "user",
                "content": "从 1 数到 20，只报告数字，不要任何其他信息。",
            },
        ],
    )
    print("响应的类型:", type(generator))

    i = 0
    async for chunk in generator:
        print(f"块 {i}")
        print(f"\t类型: {type(chunk.content)}")
        print(f"\t{chunk}\n")
        i += 1


asyncio.run(example_streaming())

# %%
# 推理模型
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 通过提供 ``ThinkingBlock`` 来支持推理模型。
#


async def example_reasoning() -> None:
    """使用推理模型的示例。"""
    model = DashScopeChatModel(
        model_name="qwen-turbo",
        api_key=os.environ["DASHSCOPE_API_KEY"],
        enable_thinking=True,
    )

    res = await model(
        messages=[
            {"role": "user", "content": "我是谁？"},
        ],
    )

    last_chunk = None
    async for chunk in res:
        last_chunk = chunk
    print("最终响应:")
    print(last_chunk)


asyncio.run(example_reasoning())

# %%
# 工具 API
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 不同的模型提供商在工具 API 方面有所不同，例如工具 JSON schema、工具调用/响应格式。
# 为了提供统一的接口，AgentScope 通过以下方式解决了这个问题：
#
# - 提供了统一的工具调用结构 block :ref:`ToolUseBlock <tool-block>` 和工具响应结构 :ref:`ToolResultBlock <tool-block>`。
# - 在模型类的 ``__call__`` 方法中提供统一的工具接口 ``tools``，接受工具 JSON schema 列表，如下所示：
#

json_schemas = [
    {
        "type": "function",
        "function": {
            "name": "google_search",
            "description": "在 Google 上搜索查询。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索查询。",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# %%
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`message`
# - :ref:`prompt`
#
