# -*- coding: utf-8 -*-
"""
.. _message:

Message
====================

Message is a specialized data structure for information exchange.
In AgentScope, we use message to communicate among agents.

A message consists of four fields: `name`, `role`, `content`, and `metadata`.
The types and meanings of these fields are as follows:

.. list-table::
    :header-rows: 1

    * - Field
    - Type
    - Description
    * - name
    - `str`
    - The name of the message sender
    * - role
    - `Literal["system", "assistant", "user"]`
    - The role of the message sender, which must be one of "system", "assistant", or "user".
        "system" is commonly used for system prompt or system messages (e.g., tool execution);
        "assistant" is usually LLM-empowered agents; "user" is for users.
    * - content
    - `Union[str, list[ContentBlock]]`
    - The specific content of the message, which can be a string or a list of `ContentBlock`.
        `ContentBlock` is a dictionary.
    * - metadata
    - `JSONSerializable`
    - Used to store structured data, which can be any serializable object, such as structured output of agents.

.. tip:: In multi-agent systems, when the `role` field is "user" or
 "assistant", the `name` field can have different values to distinguish
 different identities. When the `role` field is "system", the `name` field is
 usually fixed, such as "system".

.. tip:: When the data in the message is used for controlling the flow
 or structured output, it is recommended to use the `metadata` field.
"""

from agentscope.message import Msg
import json

# %%
# Create a Message
# ----------------
# Message can be created by specifying the `name`, `role`, and `content` fields.


msg = Msg(
    name="Jarvis",
    role="assistant",
    content="Hi! How can I help you?",
)

print(f'The sender of the message: "{msg.name}"')
print(f'The role of the sender: "{msg.role}"')
print(f'The content of the message: "{msg.content}"')

# %%
# Multimodal Message
# -------------------------
# The `content` field also supports multimodal content, such as text, image,
# audio, and video.
# AgentScope uses the concept of block to represent the content of a message.
# A block is a dictionary containing the type and content of the message,
# and the specific supported types are as follows:
#
# .. list-table::
#     :header-rows: 1
#
#     * - Class
#       - Description
#       - Example
#     * - TextBlock
#       - Text block
#       - `TextBlock(type="text", text="你好")`
#     * - ImageBlock
#       - Image block
#       - `ImageBlock(type="image", url="https://example.com/image.jpg")`
#     * - AudioBlock
#       - Audio block
#       - `AudioBlock(type="audio", url="https://example.com/audio.mp3")`
#     * - VideoBlock
#       - Video block
#       - `VideoBlock(type="video", url="https://example.com/video.mp4")`
#     * - FileBlock
#       - File block
#       - `FileBlock(type="file", url="https://example.com/file.zip")`
#
# The usage is as follows:

from agentscope.message import (
    TextBlock,
    ImageBlock,
    AudioBlock,
)

msg_image = Msg(
    name="Stank",
    role="user",
    content=[
        TextBlock(type="text", text="Describe this image for me."),
        ImageBlock(type="image", url="https://example.com/image.jpg"),
    ],
)

msg_audio = Msg(
    name="Stank",
    role="user",
    content=[
        TextBlock(type="text", text="Listen to this audio."),
        AudioBlock(type="audio", url="https://example.com/audio.mp3"),
    ],
)

# %%
# .. tip:: For tools API, there are two additional blocks: `ToolUseBlock` and
#  `ToolResultBlock`. Further reading refer to :ref:`tools`.
#
# Serialize
# ----------------
# Message can be serialized to a string in JSON format.

serialized_msg = msg.to_dict()

print(type(serialized_msg))
print(json.dumps(serialized_msg, indent=4))

# %%
# Deserialize
# ----------------
# Deserialize a message from a string in JSON format.

new_msg = Msg.from_dict(serialized_msg)

print(new_msg)
print(f'The sender of the message: "{new_msg.name}"')
print(f'The role of the sender: "{new_msg.role}"')
print(f'The content of the message: "{new_msg.content}"')
