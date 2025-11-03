# -*- coding: utf-8 -*-
"""
.. _prompt:

提示词格式化
=========================

AgentScope 中的格式化器（formatter）模块负责

- 将 Msg 对象转换为不同 LLM API 要求的格式，
- （可选）截断消息以适应 max_token 的限制，
- （可选）执行提示工程，例如对长对话进行总结。

后两个功能是可选的，开发者也可以选择在记忆（memory）或智能体（agent）层面进行处理和实现。

在 AgentScope 中，有两种类型的格式化器："ChatFormatter" 和 "MultiAgentFormatter"，它们根据输入消息中的“身份实体”进行区分。

- **ChatFormatter**：专为标准的用户-助手场景（聊天机器人）设计，使用 ``role`` 字段来识别用户和助手。
- **MultiAgentFormatter**：专为多智能体场景设计，使用 ``name`` 字段来识别不同的实体，在格式化的过程中会将多智能体的对话历史合并为单个消息。

AgentScope 内置的格式化器如下所列

.. list-table:: AgentScope 中的内置格式化器
    :header-rows: 1

    * - API 提供商
      - 用户-助手场景
      - 多智能体场景
    * - OpenAI
      - ``OpenAIChatFormatter``
      - ``OpenAIMultiAgentFormatter``
    * - Anthropic
      - ``AnthropicChatFormatter``
      - ``AnthropicMultiAgentFormatter``
    * - DashScope
      - ``DashScopeChatFormatter``
      - ``DashScopeMultiAgentFormatter``
    * - Gemini
      - ``GeminiChatFormatter``
      - ``GeminiChatFormatter``
    * - Ollama
      - ``OllamaChatFormatter``
      - ``OllamaMultiAgentFormatter``
    * - DeedSeek
      - ``DeepSeekChatFormatter``
      - ``DeepSeekMultiAgentFormatter``
    * - vLLM
      - ``OpenAIFormatter``
      - ``OpenAIFormatter``

.. tip:: OpenAI API 支持 `name` 字段，因此 `OpenAIFormatter` 也可以用于多智能体场景。也可以使用 `OpenAIMultiAgentFormatter` 代替，它会将对话历史合并为单个用户消息。

此外，内置格式化器对于不同的消息块（message blocks）的支持情况如下表所示：

.. list-table:: 内置格式化器中支持的消息块
    :header-rows: 1

    * - 格式化器
      - tool_use/result
      - image
      - audio
      - video
      - thinking
    * - ``OpenAIChatFormatter``
      - ✅
      - ✅
      - ✅
      - ❌
      -
    * - ``DashScopeChatFormatter``
      - ✅
      - ✅
      - ✅
      - ❌
      -
    * - ``DashScopeMultiAgentFormatter``
      - ✅
      - ✅
      - ✅
      - ❌
      -
    * - ``AnthropicChatFormatter``
      - ✅
      - ✅
      - ❌
      - ❌
      - ✅
    * - ``AnthropicMultiAgentFormatter``
      - ✅
      - ✅
      - ❌
      - ❌
      - ✅
    * - ``GeminiChatFormatter``
      - ✅
      - ✅
      - ✅
      - ✅
      -
    * - ``GeminiMultiAgentFormatter``
      - ✅
      - ✅
      - ✅
      - ✅
      -
    * - ``OllamaChatFormatter``
      - ✅
      - ✅
      - ❌
      - ❌
      -
    * - ``OllamaMultiAgentFormatter``
      - ✅
      - ✅
      - ❌
      - ❌
      -
    * - ``DeepSeekChatFormatter``
      - ✅
      - ❌
      - ❌
      - ❌
      -
    * - ``DeepSeekMultiAgentFormatter``
      - ✅
      - ❌
      - ❌
      - ❌
      -

.. note:: 如 `官方文档 <https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#preserving-thinking-blocks>`_ 所述，只有 Anthropic 建议在输入的提示词中保留推理的部分（thinking blocks）。对于其它格式化器，我们忽略输入消息中包含的 ``ThinkingBlock``。

面向 ReAct 的格式化
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
内置的 formatter 均面向 ReAct 智能体进行设计，其中输入消息由交替的 **对话历史** 和 **工具调用序列** 组成。

在用户-助手场景中，对话历史是用户和助手的消息，我们直接将它们转换为所期望的格式。
然而，在多智能体场景中，对话历史是来自不同智能体的消息列表，如下所示：

.. figure:: ../../_static/images/multiagent_msgs.png
    :alt: 多智能体消息示例
    :width: 85%
    :align: center

    *多智能体消息示例*


因此，我们必须将对话历史合并为带有标签 "<history>" 和 "</history>" 的单个用户消息。
以 DashScope 为例，格式化后的消息将如下所示：
"""

from agentscope.token import HuggingFaceTokenCounter
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.message import Msg, ToolResultBlock, ToolUseBlock, TextBlock
import asyncio, json


input_msgs = [
    # 系统提示
    Msg("system", "你是一个名为 Friday 的有用助手", "system"),
    # 对话历史
    Msg("Bob", "你好，Alice，你知道最近的图书馆在哪里吗？", "assistant"),
    Msg(
        "Alice",
        "抱歉，我不知道。Charlie，你有什么想法吗？",
        "assistant",
    ),
    Msg(
        "Charlie",
        "没有，我们问问 Friday 吧。Friday，帮我找到最近的图书馆。",
        "assistant",
    ),
    # 工具序列
    Msg(
        "Friday",
        [
            ToolUseBlock(
                type="tool_use",
                name="get_current_location",
                id="1",
                input={},
            ),
        ],
        "assistant",
    ),
    Msg(
        "system",
        [
            ToolResultBlock(
                type="tool_result",
                name="get_current_location",
                id="1",
                output=[TextBlock(type="text", text="104.48, 36.30")],
            ),
        ],
        "system",
    ),
    Msg(
        "Friday",
        [
            ToolUseBlock(
                type="tool_use",
                name="search_around",
                id="2",
                input={"location": [104.48, 36.30], "keyword": "library"},
            ),
        ],
        "assistant",
    ),
    Msg(
        "system",
        [
            ToolResultBlock(
                type="tool_result",
                name="search_around",
                id="2",
                output=[TextBlock(type="text", text="[...]")],
            ),
        ],
        "system",
    ),
    # 对话历史继续
    Msg("Friday", "最近的图书馆是...", "assistant"),
    Msg("Bob", "谢谢，Friday！", "user"),
    Msg("Alice", "我们一起去吧。", "user"),
]


async def run_formatter_example() -> list[dict]:
    """多智能体消息格式化示例。"""
    formatter = DashScopeMultiAgentFormatter()
    formatted_message = await formatter.format(input_msgs)
    print("格式化后的消息：")
    print(json.dumps(formatted_message, indent=4, ensure_ascii=False))
    return formatted_message


formatted_message = asyncio.run(run_formatter_example())

# %%
# 具体来说，对话历史被格式化为：
#
print("第一段对话历史：")
print(formatted_message[1]["content"])

print("\n第二段对话历史：")
print(formatted_message[-1]["content"])

# %%
# 基于截断的格式化
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# 通过 AgentScope 中的 token 模块，内置格式化器支持通过 **删除最旧的消息**（除了系统提示消息）在 token 超过限制时截断输入消息。
#
# 以 OpenAIFormatter 为例，我们首先计算输入消息的总 token 数。
#


async def run_token_counter() -> int:
    """计算输入消息的 token 数量。"""
    # 我们使用 huggingface token 计数器用于 dashscope 模型。
    token_counter = HuggingFaceTokenCounter(
        "Qwen/Qwen2.5-VL-3B-Instruct",
        use_mirror=False,
    )

    return await token_counter.count(formatted_message)


n_tokens = asyncio.run(run_token_counter())
print("格式化消息中的 token 数量为：", n_tokens)


# %%
# 然后我们将最大 token 限制设置为比总 token 数少 20 个，并运行格式化器。
#


async def run_truncated_formatter() -> None:
    """带截断的消息格式化示例。"""
    token_counter = HuggingFaceTokenCounter(
        pretrained_model_name_or_path="Qwen/Qwen2.5-VL-3B-Instruct",
        use_mirror=False,
    )
    formatter = DashScopeMultiAgentFormatter(
        token_counter=token_counter,
        max_tokens=n_tokens - 20,
    )
    truncated_formatted_message = await formatter.format(input_msgs)
    n_truncated_tokens = await token_counter.count(truncated_formatted_message)
    print("截断后的 token 数量：", n_truncated_tokens)

    print("\n截断后的对话历史：")
    print(truncated_formatted_message[1]["content"])


asyncio.run(run_truncated_formatter())

# %%
# 我们可以看到来自 Bob 和 Alice 的前两条消息被删除以适应 ``max_tokens`` 的限制。
#
#
# 自定义格式化器
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope 提供了两个基类 ``FormatterBase`` 和其子类 ``TruncatedFormatterBase``。
# 其中 ``TruncatedFormatterBase`` 类提供了 FIFO（First In First Out）截断策略，所有内置格式化器都继承自它。
#
# .. list-table:: AgentScope 中格式化器的基类
#   :header-rows: 1
#
#   * - 类
#     - 抽象方法
#     - 描述
#   * - ``FormatterBase``
#     - ``format``
#     - 将输入的 ``Msg`` 对象格式化为目标 API 所期望的格式
#   * - ``TruncatedFormatterBase``
#     - ``_format_agent_message``
#     - 格式化智能体消息，在多智能体场景中可能包含多个身份
#   * -
#     - ``_format_tool_sequence``
#     - 将工具使用和结果序列格式化为所期望的格式
#   * -
#     - ``_format`` (可选)
#     - 将输入的 ``Msg`` 对象格式化为目标 API 所期望的格式
#
# .. tip:: ``TruncatedFormatterBase`` 中的 ``_format`` 将输入消息分组为智能体消息和工具序列，然后分别通过调用 ``_format_agent_message`` 和 ``_format_tool_sequence`` 来格式化它们。开发者可以重写两个函数来实现自己的格式化策略。
#
# .. tip:: 可选地，开发者可以重写 ``TruncatedFormatterBase`` 中的 ``_truncate`` 方法来实现自己的截断策略。
#
# 进一步阅读
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# - :ref:`token`
# - :ref:`model`
#
