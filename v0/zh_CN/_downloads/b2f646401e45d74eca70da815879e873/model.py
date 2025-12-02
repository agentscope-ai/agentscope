# -*- coding: utf-8 -*-
"""
.. _model_api:

模型 API
====================

AgentScope 已集成了许多不同模态的模型 API 。

.. note:: 1. 本列表不包括文本到语音(TTS)和语音到文本(STT)API。您可以参考 :ref:`tools` 一节。
 2. 本节仅介绍如何在AgentScope中使用或集成不同的模型API。关于提示要求和提示工程策略的内容将在 :ref:`prompt-engineering` 一节中介绍。


.. list-table::
    :header-rows: 1

    * - API
      - 对话
      - 文本生成
      - 视觉
      - Embedding
    * - OpenAI
      - ✓
      - ✗
      - ✓
      - ✓
    * - DashScope
      - ✓
      - ✗
      - ✓
      - ✓
    * - Gemini
      - ✓
      - ✗
      - ✗
      - ✓
    * - Ollama
      - ✓
      - ✓
      - ✓
      - ✓
    * - Yi
      - ✓
      - ✗
      - ✗
      - ✗
    * - LiteLLM
      - ✓
      - ✗
      - ✗
      - ✗
    * - ZhipuAI
      - ✓
      - ✗
      - ✗
      - ✓
    * - Anthropic
      - ✓
      - ✗
      - ✗
      - ✗

在 AgentScope 中使用模型 API 有两种方式。可以根据自己的需求进行选择：

- **使用模型配置**：这是构建与模型 API 无关的应用程序的推荐方式。可以通过修改配置来更改模型 API，而无需更改智能体代码。
- **显式初始化模型**：如果只想使用特定的模型 API，显式初始化模型会更加方便和透明。API 文档字符串提供了参数和用法的详细信息。

.. tip:: 实际上，使用配置和显式初始化模型是等效的。使用模型配置时，AgentScope 只是将配置中的键值对传递给模型的构造函数。
"""

import os

from agentscope.models import (
    DashScopeChatWrapper,
    ModelWrapperBase,
    ModelResponse,
)
import agentscope

# %%
# 使用配置
# ------------------------------
# 在模型配置中，需要提供以下三个字段：
#
# - config_name：配置的名称。
# - model_type：模型 API 的类型，例如 "dashscope_chat"、"openai_chat" 等。
# - model_name：模型的名称，例如 "qwen-max"、"gpt-4o" 等。
#
# 在使用模型 API 之前通过调用 `agentscope.init()` 来加载配置，如下所示：
#

agentscope.init(
    model_configs=[
        {
            "config_name": "gpt-4o_temperature-0.5",
            "model_type": "openai_chat",
            "model_name": "gpt-4o",
            "api_key": "xxx",
            "generate_args": {
                "temperature": 0.5,
            },
        },
        {
            "config_name": "my-qwen-max",
            "model_type": "dashscope_chat",
            "model_name": "qwen-max",
        },
    ],
)

# %%
# 对于其它可选参数，可以查看对应模型 API 的构造函数的说明。

# %%
# 显式初始化模型
# --------------------------------
# `agentscope.models` 模块提供了所有的内置模型 API。
# 您可以通过调用相应的模型类来显式初始化模型。
#

# 打印 agentscope.models 下的模块
for module_name in agentscope.models.__all__:
    if module_name.endswith("Wrapper"):
        print(module_name)

# %%
# 以 DashScope Chat API 为例：
#

model = DashScopeChatWrapper(
    config_name="_",
    model_name="qwen-max",
    api_key=os.environ["DASHSCOPE_API_KEY"],
    stream=False,
)

response = model(
    messages=[
        {"role": "user", "content": "嗨！"},
    ],
)

# %%
# `response` 是 `agentscope.models.ModelResponse` 的一个对象，它包含以下字段：
#
# - text：生成的文本
# - embedding：生成的嵌入
# - image_urls：引用生成的图像
# - raw：来自 API 的原始响应
# - parsed：解析后的响应，例如将字符串解析成 JSON 对象
# - stream：用来挂载流式响应的生成器，更多详情请参考 :ref:`streaming` 一节。

print(f"文本：{response.text}")
print(f"嵌入：{response.embedding}")
print(f"图像URL：{response.image_urls}")
print(f"原始响应：{response.raw}")
print(f"解析后响应：{response.parsed}")
print(f"流响应：{response.stream}")

# %%
# .. _integrating_new_api:
#
# 集成新的 LLM API
# ----------------------------
# 将新的 LLM API 集成到 AgentScope 有两种方式。
#
# OpenAI 兼容 API
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# 如果您的模型与 OpenAI Python API 兼容，建议重用 `OpenAIChatWrapper` 类并提供特定参数。
#
# .. note:: 需要确保 API 的回复同样兼容 OpenAI Python API。
#
# 以 vLLM 为例，
# 其 `官方文档 <https://github.com/vllm-project/vllm?tab=readme-ov-file>`_ 提供了以下使用 OpenAI Python 库的示例：
#
# .. code-block:: python
#
#       from openai import OpenAI
#       client = OpenAI(
#           base_url="http://localhost:8000/v1",
#           api_key="token-abc123",
#       )
#
#       completion = client.chat.completions.create(
#           model="NousResearch/Meta-Llama-3-8B-Instruct",
#           messages=[
#               {"role": "user", "content": "Hello!"}
#           ],
#           temperature=0.5,
#       )
#
#       print(completion.choices[0].message)
#
#
# 将 vLLM 集成到 AgentScope 非常简单，如下：
#
# - 将初始化 OpenAI 客户端的参数（除了 `api_key`）放入 `client_args`，
# - 将生成完成的参数（除了 `model`）放入 `generate_args`。
#

vllm_model_config = {
    "model_type": "openai_chat",
    "config_name": "vllm_llama2-7b-chat-hf",
    "model_name": "meta-llama/Llama-2-7b-chat-hf",
    "api_key": "token-abc123",  # API 密钥
    "client_args": {
        "base_url": "http://localhost:8000/v1/",  # 用于指定 API 的基础 URL
    },
    "generate_args": {
        "temperature": 0.5,  # 生成参数，如 temperature、seed
    },
}

# %%
# 或者，直接用参数初始化 OpenAI Chat API 的模型类：
#

from agentscope.models import OpenAIChatWrapper

model = OpenAIChatWrapper(
    config_name="",
    model_name="meta-llama/Llama-2-7b-chat-hf",
    api_key="token-abc123",
    client_args={"base_url": "http://localhost:8000/v1/"},
    generate_args={"temperature": 0.5},
)

# %%
# RESTful API
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# 如果您的模型通过 RESTful post API 访问，并且响应格式与 OpenAI API 兼容，可以考虑使用 `PostAPIChatWrapper`。
#
# 以下面的 curl 命令为例，只需将 header、API URL 和 data（除了 `messages`，它将自动传递）提取成模型类的初始化参数即可。
#
# 一个示例 post 请求：
#
# .. code-block:: bash
#
#     curl https://api.openai.com/v1/chat/completions
#     -H "Content-Type: application/json"
#     -H "Authorization: Bearer $OPENAI_API_KEY"
#     -d '{
#             "model": "gpt-4o",
#             "messages": [
#                 {"role": "user", "content": "write a haiku about ai"}
#             ]
#         }'
#
# 相应的模型类初始化如下：
#

from agentscope.models import PostAPIChatWrapper

post_api_model = PostAPIChatWrapper(
    config_name="",
    api_url="https://api.openai.com/v1/chat/completions",  # 目标 URL
    headers={
        "Content-Type": "application/json",  # 来自头部
        "Authorization": "Bearer $OPENAI_API_KEY",
    },
    json_args={
        "model": "gpt-4o",  # 来自数据
    },
)

# %%
# 它的模型配置如下：
#

post_api_config = {
    "config_name": "{my_post_model_config_name}",
    "model_type": "post_api_chat",
    "api_url": "https://api.openai.com/v1/chat/completions",
    "headers": {
        "Authorization": "Bearer {YOUR_API_TOKEN}",
    },
    "json_args": {
        "model": "gpt-4o",
    },
}

# %%
# 如果你的模型 API 返回格式与 OpenAI 不同，可以继承 `PostAPIChatWrapper` 并重写 `_parse_response` 方法。
#
# .. note:: 需要在子类中定义一个新的 `model_type` 字段，以区分它与现有的模型类。
#
#


class MyNewModelWrapper(PostAPIChatWrapper):
    model_type: str = "{my_new_model_type}"

    def _parse_response(self, response: dict) -> ModelResponse:
        """解析来自 API 服务器的响应。

        Args:
            response (`dict`):
                从 API 服务器获取的响应，并通过
                `response.json()` 解析为统一的格式。

        Returns (`ModelResponse`):
            解析后的响应。
        """
        # TODO: 将以下代码替换为您自己的解析逻辑
        return ModelResponse(
            text=response["data"]["response"]["choices"][0]["message"][
                "content"
            ],
        )


# %%
# 自定义模型类
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# 如果需要从头开始实现新的模型类，首先需要了解 AgentScope 中的以下概念：
#
# - **model_type**：在使用模型配置时，AgentScope 使用 `model_type` 字段来区分不同的模型 API。因此，请确保您的新模型类具有唯一的 `model_type`。
# - **__init__**：从模型配置初始化时，AgentScope 会将配置中的所有键值对传递给模型类的 `__init__` 方法。因此，请确保您的 `__init__` 方法可以处理配置中的所有参数。
# - **__call__**：模型类的核心方法是 `__call__`，它接收输入消息并返回响应。其返回值应该是 `ModelResponse` 对象。
#


class MyNewModelWrapper(ModelWrapperBase):
    model_type: str = "{my_new_model_type}"

    def __init__(self, config_name, model_name, **kwargs) -> None:
        super().__init__(config_name, model_name=model_name)

        # TODO: 在这里初始化您的模型

    def __call__(self, *args, **kwargs) -> ModelResponse:
        # TODO: 在这里实现您的模型的核心逻辑

        return ModelResponse(
            text="Hello, World!",
        )


# %%
# .. tip:: 可选地，可以在模型中实现一个 `format` 方法，从而将 AgentScope 中的 `Msg` 类 转化为目标模型 API 要求的格式。更多详情请参考 :ref:`prompt-engineering`。
#
# 进一步阅读
# ---------------------
# - :ref:`prompt-engineering`
# - :ref:`streaming`
# - :ref:`structured-output`
