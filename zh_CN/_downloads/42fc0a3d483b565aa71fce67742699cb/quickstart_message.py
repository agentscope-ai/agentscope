# -*- coding: utf-8 -*-
"""
.. _message:

创建消息
====================

消息是 AgentScope 中的核心概念，用于支持多模态数据、工具 API、信息存储/交换和提示构建。

一个消息由四个字段组成：

- ``name``，
- ``role``，
- ``content``，以及
- ``metadata``

这些字段的类型和含义如下：

.. list-table:: 消息对象中的字段
    :header-rows: 1

    * - 字段
      - 类型
      - 描述
    * - name
      - ``str``
      - 消息发送者的名称/身份
    * - role
      - | ``Literal[``
        |     ``"system",``
        |     ``"assistant",``
        |     ``"user"``
        | ``]``
      - 消息发送者的角色，必须是 "system"、"assistant" 或 "user" 之一。
    * - content
      - ``str | list[ContentBlock]``
      - 消息包含的数据，可以是字符串或 block 的列表。
    * - metadata
      - ``dict[str, JSONSerializableObject] | None``
      - 包含额外元数据的字典，通常用于结构化输出。

.. tip:: - 在具有多个身份实体的应用程序中，``name`` 字段用于区分不同的身份。
 - 建议将 ``metadata`` 字段用于结构化输出，在 AgentScope 内置的模块中，``metadata`` 不会参与 LLM 的提示构建。

接下来，我们根据不同的场景分别介绍 ``content`` 字段中支持的不同数据结构（block）。
"""

from agentscope.message import (
    Msg,
    Base64Source,
    TextBlock,
    ThinkingBlock,
    ImageBlock,
    AudioBlock,
    VideoBlock,
    ToolUseBlock,
    ToolResultBlock,
)
import json

# %%
# 创建文本消息
# -----------------------------
# 通过提供 ``name``、``role`` 和 ``content`` 字段来创建消息对象。
#

msg = Msg(
    name="Jarvis",
    role="assistant",
    content="你好！我能怎么帮助你？",
)

print(f"发送者的名称: {msg.name}")
print(f"发送者的角色: {msg.role}")
print(f"消息的内容: {msg.content}")

# %%
# 创建多模态消息
# --------------------------------------
# Message 类通过提供不同的 block 结构来支持多模态内容：
#
# .. list-table:: AgentScope 中的多模态 block
#     :header-rows: 1
#
#     * - 类
#       - 描述
#       - 示例
#     * - TextBlock
#       - 纯文本数据
#       - .. code-block:: python
#
#             TextBlock(
#                type="text",
#                text="Hello, world!"
#             )
#     * - ImageBlock
#       - 图像数据
#       - .. code-block:: python
#
#             ImageBlock(
#                type="image",
#                source=URLSource(
#                    type="url",
#                    url="https://example.com/image.jpg"
#                )
#             )
#     * - AudioBlock
#       - 音频数据
#       - .. code-block:: python
#
#             AudioBlock(
#                type="audio",
#                source=URLSource(
#                    type="url",
#                    url="https://example.com/audio.mp3"
#                )
#             )
#     * - VideoBlock
#       - 视频数据
#       - .. code-block:: python
#
#             VideoBlock(
#                type="video",
#                source=URLSource(
#                    type="url",
#                    url="https://example.com/video.mp4"
#                )
#             )
#
# 对于 ``ImageBlock``、``AudioBlock`` 和 ``VideoBlock``，还可以使用 base64 编码的字符串作为数据源（Source）：
#

msg = Msg(
    name="Jarvis",
    role="assistant",
    content=[
        TextBlock(
            type="text",
            text="这是一个包含 base64 编码数据的多模态消息。",
        ),
        ImageBlock(
            type="image",
            source=Base64Source(
                type="base64",
                media_type="image/jpeg",
                data="/9j/4AAQSkZ...",
            ),
        ),
        AudioBlock(
            type="audio",
            source=Base64Source(
                type="base64",
                media_type="audio/mpeg",
                data="SUQzBAAAAA...",
            ),
        ),
        VideoBlock(
            type="video",
            source=Base64Source(
                type="base64",
                media_type="video/mp4",
                data="AAAAIGZ0eX...",
            ),
        ),
    ],
)

# %%
# 创建推理消息
# --------------------------------------
# ``ThinkingBlock`` 用于支持推理模型，包含模型的思考过程。
#

msg_thinking = Msg(
    name="Jarvis",
    role="assistant",
    content=[
        ThinkingBlock(
            type="thinking",
            thinking="我正在为 AgentScope 构建一个思考块的示例。",
        ),
        TextBlock(
            type="text",
            text="这是一个思考块的示例。",
        ),
    ],
)

# %%
# .. _tool-block:
#
# 创建工具使用/结果消息
# --------------------------------------
# ``ToolUseBlock`` 和 ``ToolResultBlock`` 用于支持工具 API：
#

msg_tool_call = Msg(
    name="Jarvis",
    role="assistant",
    content=[
        ToolUseBlock(
            type="tool_use",
            id="343",
            name="get_weather",
            input={
                "location": "Beijing",
            },
        ),
    ],
)

msg_tool_res = Msg(
    name="system",
    role="system",
    content=[
        ToolResultBlock(
            type="tool_result",
            id="343",
            name="get_weather",
            output="北京的天气是晴天，温度为 25°C。",
        ),
    ],
)


# %%
# .. tip:: AgentScope 中，通常使用 ``role`` 为“system”的消息来记录工具函数的执行结果。有关 AgentScope 中工具的更多信息，请参考 :ref:`tool` 部分。
#
# 序列化和反序列化
# ------------------------------------------------
# 消息对象可以分别通过 ``to_dict`` 和 ``from_dict`` 方法进行序列化和反序列化。

serialized_msg = msg.to_dict()

print(type(serialized_msg))
print(json.dumps(serialized_msg, indent=4, ensure_ascii=False))

# %%
# 从 JSON 格式的数据反序列化消息。

new_msg = Msg.from_dict(serialized_msg)

print(type(new_msg))
print(f'消息的发送者: "{new_msg.name}"')
print(f'发送者的角色: "{new_msg.role}"')
print(f'消息的内容: "{json.dumps(new_msg.content, indent=4, ensure_ascii=False)}"')

# %%
# 属性函数
# ------------------------------------------------
# 为了便于使用 Msg 对象，AgentScope 提供了以下函数：
#
# .. list-table:: Msg 对象的函数
#   :header-rows: 1
#
#   * - 函数
#     - 参数
#     - 描述
#   * - ``get_text_content``
#     -
#     - 将所有 ``TextBlock`` 中的内容收集到单个字符串中（用 "\\n" 分隔）。
#   * - ``get_content_blocks``
#     - ``block_type``
#     - 返回指定类型的内容块列表。如果未提供 ``block_type``，则以块格式返回全部内容。
#   * - ``has_content_blocks``
#     - ``block_type``
#     - 检查消息是否具有指定类型的内容块。``str`` 内容会被视为 ``TextBlock`` 类型。
