# -*- coding: utf-8 -*-
"""
.. _token:

Token 计数
=========================

AgentScope 在 ``agentscope.token`` 模块下提供了 token 计数功能，用于计算给定消息中
的 token 数量，允许开发者在调用 LLM API 前预估 token 数量。

具体而言，可用的 token 计数器如下：

.. list-table::
    :header-rows: 1

    * - LLM API
      - 类
      - 实现方式
      - 支持图像数据
      - 支持工具
    * - Anthropic
      - ``AnthropicTokenCounter``
      - 官方 API
      - ✅
      - ✅
    * - OpenAI
      - ``OpenAITokenCounter``
      - 本地计算
      - ✅
      - ✅
    * - Gemini
      - ``GeminiTokenCounter``
      - 官方 API
      - ✅
      - ✅
    * - HuggingFace
      - ``HuggingFaceTokenCounter``
      - 基于Tokenizer计算
      - 取决于模型
      - 取决于模型

.. tip:: 格式化器模块已集成了 token 计数器以支持提示截断。更多详细信息请参考 :ref:`prompt` 部分。

.. note::
 - 对于 DashScope 模型，目前 dashscope 库不提供 token 计数 API。因此我们建议使用 HuggingFace token 计数器代替。
 - 对于 OpenAI 模型，由于官方未提供 token 计数 API，因此可能存在与官方计算结果不一致的情况。

下面展示使用 OpenAI token 计数器计算 token 数量的示例：
"""

import asyncio
from agentscope.token import OpenAITokenCounter


async def example_token_counting():
    # 示例消息
    messages = [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi, how can I help you?"},
    ]

    # OpenAI token 计数
    openai_counter = OpenAITokenCounter(model_name="gpt-4.1")
    n_tokens = await openai_counter.count(messages)

    print(f"Token 数量: {n_tokens}")


asyncio.run(example_token_counting())


# %%
# 进一步阅读
# ------------------------------
#
# - :ref:`prompt`
#
