# -*- coding: utf-8 -*-
"""
.. _message:

Create Message
====================

Message is the core concept in AgentScope, used to support multimodal data, tools API, information storage/exchange and prompt construction.

A message consists of four fields:

- ``name``,
- ``role``,
- ``content``, and
- ``metadata``

The types and descriptions of these fields are as follows:

.. list-table:: The fields in a message object
    :header-rows: 1

    * - Field
      - Type
      - Description
    * - name
      - ``str``
      - The name/identity of the message sender
    * - role
      - | ``Literal[``
        |     ``"system",``
        |     ``"assistant",``
        |     ``"user"``
        | ``]``
      - The role of the message sender, which must be one of "system", "assistant", or "user".
    * - content
      - ``str | list[ContentBlock]``
      - The data of the message, which can be a string or a list of blocks.
    * - metadata
      - ``dict[str, JSONSerializableObject] | None``
      - A dict containing additional metadata about the message, usually used for structured output.

.. tip:: - In application with multiple identities, the ``name`` field is used to distinguish between different identities.
 - The ``metadata`` field is recommended for structured output, which won't be included in the prompt construction.

Next, we introduce the supported blocks in the ``content`` field by their corresponding scenarios.
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
# Creating Textual Message
# -----------------------------
# Creating a message object by providing the ``name``, ``role``, and ``content`` fields.
#

msg = Msg(
    name="Jarvis",
    role="assistant",
    content="Hi! How can I help you?",
)

print(f"The name of the sender: {msg.name}")
print(f"The role of the sender: {msg.role}")
print(f"The content of the message: {msg.content}")

# %%
# Creating Multimodal Message
# --------------------------------------
# The message class supports multimodal content by providing different content blocks:
#
# .. list-table:: Multimodal content blocks in AgentScope
#     :header-rows: 1
#
#     * - Class
#       - Description
#       - Example
#     * - TextBlock
#       - Pure text data
#       - .. code-block:: python
#
#             TextBlock(
#                type="text",
#                text="Hello, world!"
#             )
#     * - ImageBlock
#       - The image data
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
#       - The audio data
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
#       - The video data
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
# For ``ImageBlock``, ``AudioBlock`` and ``VideoBlock``, you can use either a base64 encoded string as the source:
#

msg = Msg(
    name="Jarvis",
    role="assistant",
    content=[
        TextBlock(
            type="text",
            text="This is a multimodal message with base64 encoded data.",
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
# Creating Thinking Message
# --------------------------------------
# The ``ThinkingBlock`` is to support reasoning models, containing the thinking process of the model.
#

msg_thinking = Msg(
    name="Jarvis",
    role="assistant",
    content=[
        ThinkingBlock(
            type="thinking",
            thinking="I'm building an example for thinking block in AgentScope.",
        ),
        TextBlock(
            type="text",
            text="This is an example for thinking block.",
        ),
    ],
)

# %%
# .. _tool-block:
#
# Creating Tool Use/Result Message
# --------------------------------------
# The ``ToolUseBlock`` and ``ToolResultBlock`` are to support tools API:
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
            output="The weather in Beijing is sunny with a temperature of 25°C.",
        ),
    ],
)


# %%
# .. tip:: Refer to the :ref:`tool` section for more information about tools API in AgentScope.
#
# Serialization and Deserialization
# ------------------------------------------------
# Message object can be serialized and deserialized by ``to_dict`` and ``from_dict`` methods, respectively.

serialized_msg = msg.to_dict()

print(type(serialized_msg))
print(json.dumps(serialized_msg, indent=4))

# %%
# Deserialize a message from a string in JSON format.

new_msg = Msg.from_dict(serialized_msg)

print(type(new_msg))
print(f'The sender of the message: "{new_msg.name}"')
print(f'The role of the sender: "{new_msg.role}"')
print(f'The content of the message: "{json.dumps(new_msg.content, indent=4)}"')

# %%
# Property Functions
# ------------------------------------------------
# To ease the use of message object, AgentScope provides these functions:
#
# .. list-table:: Functions of the message object
#   :header-rows: 1
#
#   * - Function
#     - Parameters
#     - Description
#   * - get_text_content
#     - \-
#     - Gather content from all ``TextBlock`` in to a single string (separated by "\\n").
#   * - get_content_blocks
#     - ``block_type``
#     - Return a list of content blocks of the specified type. If ``block_type`` not provided, return content in blocks format.
#   * - has_content_blocks
#     - ``block_type``
#     - Check whether the message has content blocks of the specified type. The ``str`` content is considered as a ``TextBlock`` type.
