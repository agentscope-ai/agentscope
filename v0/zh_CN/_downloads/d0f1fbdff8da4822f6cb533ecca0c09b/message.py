# -*- coding: utf-8 -*-
"""
.. _message:

消息
====================

消息是一种用于智能体间信息交换的数据结构。

一个消息由 name, role, content 和 metadata 四个字段组成，这些字段的类型含义如下

.. list-table::
    :header-rows: 1

    * - 字段
      - 类型
      - 描述
    * - name
      - `str`
      - 消息发送者的名字
    * - role
      - `Literal["system", "assistant", "user"]`
      - 消息发送者的角色，必须是 "system"、"assistant" 或 "user" 之一。其中 "system"
       常用于 system prompt，或是一些系统消息（例如工具的执行等）；"assistant" 则是大模
       型扮演的智能体；"user" 则是用户。
    * - content
      - `Union[str, list[ContentBlock]]`
      - 消息的具体内容，可以是字符串或者 ContentBlock 列表，ContentBlock 是一个字典
    * - metadata
      - `JSONSerializable`
      - 用于存储结构化数据，可以是任何可序列化的对象，例如智能体的结构化输出

.. tip:: 多智能体中，`role` 字段为 "user" 或是 "assistant" 时, `name` 可以是不同的值，
 用于区分不同的用户和智能体，而 `role` 为 "system" 时，`name` 通常是固定的，
 例如 "system"。

.. tip:: 当消息中的数据需要用于控制流程或结构化输出时，建议使用 `metadata` 字段。
"""

from agentscope.message import Msg
import json

# %%
# 创建消息
# ----------------
# 可以通过指定 name、role 和 content 字段来创建消息。
#

msg = Msg(
    name="Jarvis",
    role="assistant",
    content="嗨！我能为您效劳吗？",
)

print(f'消息发送者："{msg.name}"')
print(f'发送者角色："{msg.role}"')
print(f'消息内容："{msg.content}"')

# %%
# 多模态消息
# ----------------
# 消息的 `content` 字段可以是多模态的，例如文本、图片、音频、视频等。AgentScope使用
# block 这一概念来表示消息的内容，block 是一个字典，包含了消息的类型和内容，具体支持的
# 类型如下：
#
# .. list-table::
#     :header-rows: 1
#
#     * - 类型
#       - 描述
#       - 初始化
#     * - TextBlock
#       - 文本块
#       - `TextBlock(type="text", text="你好")`
#     * - ImageBlock
#       - 图片块
#       - `ImageBlock(type="image", url="https://example.com/image.jpg")`
#     * - AudioBlock
#       - 音频块
#       - `AudioBlock(type="audio", url="https://example.com/audio.mp3")`
#     * - VideoBlock
#       - 视频块
#       - `VideoBlock(type="video", url="https://example.com/video.mp4")`
#     * - FileBlock
#       - 文件块
#       - `FileBlock(type="file", url="https://example.com/file.zip")`
#
# 使用的方式如下：

from agentscope.message import (
    TextBlock,
    ImageBlock,
    AudioBlock,
)

msg_image = Msg(
    name="Stank",
    role="user",
    content=[
        TextBlock(type="text", text="帮我看看这张图片"),
        ImageBlock(type="image", url="https://example.com/image.jpg"),
    ],
)

msg_audio = Msg(
    name="Stank",
    role="user",
    content=[
        TextBlock(type="text", text="帮我播放这首歌"),
        AudioBlock(type="audio", url="https://example.com/audio.mp3"),
    ],
)


# %%
# .. tip:: 使用 tools API 时，`content` 字段支持额外的两个 block 类型，分别是
# `ToolUseBlock` 和 `ToolResultBlock`。更多内容请参考 :ref:`tools`。
#
# 序列化
# ----------------
# 消息可以序列化为 JSON 格式的字符串。
#

serialized_msg = msg.to_dict()

print(type(serialized_msg))
print(json.dumps(serialized_msg, indent=4))

# %%
# 反序列化
# ----------------
# 从 JSON 格式的字典反序列化消息。
#

new_msg = Msg.from_dict(serialized_msg)

print(new_msg)
print(f'消息发送者："{new_msg.name}"')
print(f'发送者角色："{new_msg.role}"')
print(f'消息内容："{new_msg.content}"')
