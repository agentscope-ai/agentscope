# -*- coding: utf-8 -*-
"""
.. _token:

Token
=========================

AgentScope provides a token counter module under ``agentscope.token`` to
calculate the number of tokens in the given messages, allowing developers
to estimate the number of tokens in a prompt before sending it to an API.

Specifically, the following token counters are available:

.. list-table::
    :header-rows: 1

    * - Provider
      - Class
      - Support Image Data
      - Support Tools
    * - Anthropic
      - ``AnthropicTokenCounter``
      - ✅
      - ✅
    * - OpenAI
      - ``OpenAITokenCounter``
      - ✅
      - ✅
    * - Gemini
      - ``GeminiTokenCounter``
      - ✅
      - ✅
    * - HuggingFace
      - ``HuggingFaceTokenCounter``
      - Depends on the model
      - Depends on the model

.. tip:: The formatter module has integrated the token counters to support prompt truncation. Refer to the :ref:`prompt` section for more details.

.. note:: For DashScope models, the dashscope library doesn't provide a token counting API. So we recommend using the HuggingFace token counter instead.

We show an example of using the OpenAI token counter to count the number of tokens:
"""

import asyncio
from agentscope.token import OpenAITokenCounter


async def example_token_counting():
    # Example messages
    messages = [
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi, how can I help you?"},
    ]

    # OpenAI token counting
    openai_counter = OpenAITokenCounter(model_name="gpt-4.1")
    n_tokens = await openai_counter.count(messages)

    print(f"Number of tokens: {n_tokens}")


asyncio.run(example_token_counting())


# %%
# Further Reading
# ------------------------------
#
# - :ref:`prompt`
#
