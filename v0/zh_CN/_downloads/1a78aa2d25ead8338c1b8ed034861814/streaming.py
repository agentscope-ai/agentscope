# -*- coding: utf-8 -*-
"""
.. _streaming:

流式输出
=========================

AgentScope 支持在终端和 AgentScope Studio 中以打字机效果显示流式输出。

.. list-table::
    :header-rows: 1

    * - API
      - 类
      - 是否支持流式输出
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

本节将展示如何在 AgentScope 中启用流式输出，以及如何在智能体中处理流式返回。
"""

# %%
# 启用流式输出
# ----------------------------
#
# 通过设置模型类的 `stream` 参数，启用流式输出。
# 你可以在初始化或配置中直接指定`stream`参数。
#
# - 在初始化中指定
#

from agentscope.models import DashScopeChatWrapper
import os

model = DashScopeChatWrapper(
    config_name="_",
    model_name="qwen-max",
    api_key=os.environ["DASHSCOPE_API_KEY"],
    stream=True,  # 启用流式输出
)

# %%
# - 在模型配置中指定

model_config = {
    "model_type": "dashscope_chat",
    "config_name": "qwen_config",
    "model_name": "qwen-max",
    "stream": True,
}

# %%
# 使用上述模型配置，我们可以在 AgentScope 中使用内置智能体获取流式输出。
#
# 接下来，我们展示如何在智能体中处理流式输出。

# %%
# 处理流式响应
# -------------------------------------------
#
# 一旦我们启用了流式输出，模型返回对象中的 `stream` 字段将包含一个生成器。
#

prompt = [{"role": "user", "content": "Hi!"}]

response = model(prompt)
print("response.stream的类型:", type(response.stream))

# %%
# 我们可以遍历生成器以获取流式文本。
# 该生成器同时也会生成一个布尔值，标识当前是否为最后一个文本块。

for index, chunk in enumerate(response.stream):
    print(f"{index}.", chunk)
    print(f"当前text字段:", response.text, "\n")

# %%
# .. note:: 注意 `response.stream` 挂载的生成器是增量的，并且只能使用一次。
#  在遍历过程中，`response` 的 `text` 字段会自动拼接字符串。
#  为了与非流式模式兼容，你也可以直接使用`response.text`一次获取所有文本。

prompt = [{"role": "user", "content": "Hi!"}]
response = model(prompt)
# 一次性获取所有文本
print(response.text)

# %%
# 打字机效果
# -------------------------------------------
# 为了实现打字机的显示效果，AgentScope 在 `AgentBase` 类中提供了一个 `speak` 函数。
# 如果给定了一个生成器，`speak` 函数会遍历生成器并在终端或 AgentScope Studio 中以打字机效果打印文本。
#
# .. code-block:: python
#
#   def reply(*args, **kwargs):
#       # ...
#       self.speak(response.stream)
#       # ...
#
# 为了使一套代码同时兼容流式和非流式模式，AgentScope 的所有内置智能体中使用以下代码片段。
#
# .. code-block:: python
#
#   def reply(*args, **kwargs):
#       # ...
#       self.speak(response.stream or response.text)
#       # ...
#
