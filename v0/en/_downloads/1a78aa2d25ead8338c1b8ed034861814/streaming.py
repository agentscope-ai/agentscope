# -*- coding: utf-8 -*-
"""
.. _streaming:

Streaming Mode
=========================

AgentScope supports streaming output for the following APIs in both terminal
and AgentScope Studio.

.. list-table::
    :header-rows: 1

    * - API
      - Class
      - Streaming
    * - OpenAI Chat API
      - `OpenAIChatWrapper`
      - ✓
    * - DashScope Chat API
      - `DashScopeChatWrapper`
      - ✓
    * - Gemini Chat API
      - `GeminiChatWrapper`
      - ✓
    * - ZhipuAI Chat API
      - `ZhipuAIChatWrapper`
      - ✓
    * - Ollama Chat API
      - `OllamaChatWrapper`
      - ✓
    * - LiteLLM Chat API
      - `LiteLLMChatWrapper`
      - ✓
    * - Anthropic Chat API
      - `AnthropicChatWrapper`
      - ✓

This section will show how to enable streaming mode in AgentScope and handle
the streaming response within an agent.
"""

# %%
# Enabling Streaming Output
# ----------------------------
#
# AgentScope supports streaming output by providing a `stream` parameter
# in model wrapper class.
# You can directly specify the `stream` parameter in initialization or
# configuration.
#
# - Specifying in Initialization

from agentscope.models import DashScopeChatWrapper
import os

model = DashScopeChatWrapper(
    config_name="_",
    model_name="qwen-max",
    api_key=os.environ["DASHSCOPE_API_KEY"],
    stream=True,  # Enabling the streaming output
)

# %%
# - Specifying in Configuration

model_config = {
    "model_type": "dashscope_chat",
    "config_name": "qwen_config",
    "model_name": "qwen-max",
    "stream": True,
}

# %%
# With the above configuration, we can obtain streaming output with built-in
# agents in AgentScope.
#
# Next, we show how to handle the streaming output within an agent.

# %%
# Handling Streaming Response
# -------------------------------------------
#
# Once we enable the streaming output, the returned model response will
# contain a generator in its `stream` field.

prompt = [{"role": "user", "content": "Hi!"}]

response = model(prompt)
print("The type of response.stream:", type(response.stream))

# %%
# We can iterate over the generator to get the streaming text.
# A boolean value will also be yielded to indicate whether the current
# chunk is the last one.

for index, chunk in enumerate(response.stream):
    print(f"{index}.", chunk)
    print(f"Current text field:", response.text)

# %%
# .. note:: Note the generator is incremental and one-time.
#
# During the iterating, the `text` field in the response will concatenate
# sub strings automatically.
#
# To be compatible with non-streaming mode, you can also directly use
# `response.text` to obtain all text at once.

prompt = [{"role": "user", "content": "Hi!"}]
response = model(prompt)
print(response.text)

# %%
# Displaying Like Typewriter
# -------------------------------------------
# To display the streaming text like a typewriter, AgentScope provides a
# `speak` function within the `AgentBase` class.
# If a generator is given, the `speak` function will iterate over the
# generator and print the text like a typewriter in terminal or AgentScope
# Studio.
#
# .. code-block:: python
#
#   def reply(*args, **kwargs):
#       # ...
#       self.speak(response.stream)
#       # ...
#
# To be compatible with both streaming and non-streaming mode, we use the
# following code snippet for all built-in agents in AgentScope.
#
# .. code-block:: python
#
#   def reply(*args, **kwargs):
#       # ...
#       self.speak(response.stream or response.text)
#       # ...
#
