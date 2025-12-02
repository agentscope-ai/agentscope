# -*- coding: utf-8 -*-
"""
.. _model_api:

Model APIs
====================

AgentScope has integrated many popular model API libraries with different modalities.

.. note:: 1. The text-to-speech (TTS) and speech-to-text (STT) APIs are not included in this list. You can refer to the section :ref:`tools`.

 2. The section only introduces how to use or integrate different model APIs in AgentScope. The prompt requirements and prompt engineering strategies are left in the section :ref:`prompt-engineering`.


.. list-table::
    :header-rows: 1

    * - API
      - Chat
      - Text Generation
      - Vision
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
    * - Zhipu
      - ✓
      - ✗
      - ✗
      - ✓
    * - Anthropic
      - ✓
      - ✗
      - ✗
      - ✗

There are two ways to use the model APIs in AgentScope. You can choose the one that suits you best.

- **Use Configuration**: This is the recommended way to build model API-agnostic applications. You can change model API by modifying the configuration, without changing the code.
- **Initialize Model Explicitly**: If you only want to use a specific model API, initialize model explicitly is much more convenient and transparent to the developer. The API docstrings provide detailed information on the parameters and usage.

.. tip:: Actually, using configuration and initializing model explicitly are equivalent. When you use the configuration, AgentScope just passes the key-value pairs in the configuration to initialize the model automatically.
"""
import os

from agentscope.models import (
    DashScopeChatWrapper,
    ModelWrapperBase,
    ModelResponse,
)
import agentscope

# %%
# Using Configuration
# ------------------------------
# In a model configuration, the following three fields are required:
#
# - config_name: The name of the configuration.
# - model_type: The type of the model API, e.g. "dashscope_chat", "openai_chat", etc.
# - model_name: The name of the model, e.g. "qwen-max", "gpt-4o", etc.
#
# You should load the configurations before using the model APIs by calling `agentscope.init()` as follows:

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
# For the other parameters, you

# %%
# Initializing Model Explicitly
# --------------------------------
# The available model APIs are modularized in the `agentscope.models` module.
# You can initialize a model explicitly by calling the corresponding model class.

# print the modules under agentscope.models
for module_name in agentscope.models.__all__:
    if module_name.endswith("Wrapper"):
        print(module_name)

# %%
# Taking DashScope Chat API as an example:

model = DashScopeChatWrapper(
    config_name="_",
    model_name="qwen-max",
    api_key=os.environ["DASHSCOPE_API_KEY"],
    stream=False,
)

response = model(
    messages=[
        {"role": "user", "content": "Hi!"},
    ],
)

# %%
# The `response` is an object of `agentscope.models.ModelResponse`, which contains the following fields:
#
# - text: The generated text
# - embedding: The generated embeddings
# - image_urls: Refer to generated images
# - raw: The raw response from the API
# - parsed: The parsed response, e.g. load the text into a JSON object
# - stream: A generator that yields the response text chunk by chunk, refer to section :ref: `streaming` for more details.

print(f"Text: {response.text}")
print(f"Embedding: {response.embedding}")
print(f"Image URLs: {response.image_urls}")
print(f"Raw: {response.raw}")
print(f"Parsed: {response.parsed}")
print(f"Stream: {response.stream}")

# %%
# .. _integrating_new_api:
#
# Integrating New LLM API
# ----------------------------
# There are two ways to integrate a new LLM API into AgentScope.
#
# OpenAI-Compatible APIs
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# If your model is compatible with OpenAI Python API, reusing the `OpenAIChatWrapper` class with specific parameters is recommended.
#
# .. note:: You should take care of the messages format manually.
#
# Taking `vLLM <https://github.com/vllm-project/vllm?tab=readme-ov-file>`_, an OpenAI-compatible LLM inference engine, as an example,
# its `official doc <https://github.com/vllm-project/vllm?tab=readme-ov-file>`_ provides the following example for OpenAI Python client library:
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
# It's very easy to integrate vLLM into AgentScope as follows:
#
# - put the parameters for initializing OpenAI client (except `api_key`) into `client_args`, and
# - the parameters for generating completions (expect `model`) into `generate_args`.
#

vllm_model_config = {
    "model_type": "openai_chat",
    "config_name": "vllm_llama2-7b-chat-hf",
    "model_name": "meta-llama/Llama-2-7b-chat-hf",
    "api_key": "token-abc123",  # The API key
    "client_args": {
        "base_url": "http://localhost:8000/v1/",  # Used to specify the base URL of the API
    },
    "generate_args": {
        "temperature": 0.5,  # The generation parameters, e.g. temperature, seed
    },
}

# %%
# Or, directly initialize the OpenAI Chat model wrapper with the parameters:

from agentscope.models import OpenAIChatWrapper

model = OpenAIChatWrapper(
    config_name="",
    model_name="meta-llama/Llama-2-7b-chat-hf",
    api_key="token-abc123",
    client_args={"base_url": "http://localhost:8000/v1/"},
    generate_args={"temperature": 0.5},
)

# %%
# RESTful APIs
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# If your model is accessed via RESTful post API, and OpenAI-compatible in response format, consider to use the `PostAPIChatWrapper`.
#
# Taking the following curl command as an example, just extract the **header**, **API URL**, and **data** (except `messages`, which will be passed automatically) as the parameters for initializing the model wrapper.
#
# For an example post request:
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
# The corresponding model wrapper initialization is as follows:

from agentscope.models import PostAPIChatWrapper

post_api_model = PostAPIChatWrapper(
    config_name="",
    api_url="https://api.openai.com/v1/chat/completions",  # The target URL
    headers={
        "Content-Type": "application/json",  # From the header
        "Authorization": "Bearer $OPENAI_API_KEY",
    },
    json_args={
        "model": "gpt-4o",  # From the data
    },
)

# %%
# Its model configuration is as follows:

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
# If your model API response format is different from OpenAI API, you can inherit from `PostAPIChatWrapper` and override the `_parse_response` method to adapt to your API response format.
#
# .. note:: You need to define a new `model_type` field in the subclass to distinguish it from the existing model wrappers.
#
#


class MyNewModelWrapper(PostAPIChatWrapper):
    model_type: str = "{my_new_model_type}"

    def _parse_response(self, response: dict) -> ModelResponse:
        """Parse the response from the API server.

        Args:
            response (`dict`):
                The response obtained from API server and parsed by
                `response.json()` to unify the format.

        Return (`ModelResponse`):
            The parsed response.
        """
        # TODO: Replace by your own parsing logic
        return ModelResponse(
            text=response["data"]["response"]["choices"][0]["message"][
                "content"
            ],
        )


# %%
# From Scratch
# ^^^^^^^^^^^^^^^^^^^^^^^^^^^
#
# If you decide to implement a new model API from scratch, you need to know the following concepts in AgentScope:
#
# - **model_type**: When using model configurations, AgentScope uses the `model_type` field to distinguish different model APIs. So ensure your new model wrapper class has a unique `model_type`.
# - **__init__**: When initializing from configuration, AgentScope passes all the key-value pairs in the configuration to the `__init__` method of the model wrapper. So ensure your `__init__` method can handle all the parameters in the configuration.
# - **__call__**: The core method of the model wrapper is `__call__`, which takes the input messages and returns the response. Its return value should be an object of `ModelResponse`.


class MyNewModelWrapper(ModelWrapperBase):
    model_type: str = "{my_new_model_type}"

    def __init__(self, config_name, model_name, **kwargs) -> None:
        super().__init__(config_name, model_name=model_name)

        # TODO: Initialize your model here

    def __call__(self, *args, **kwargs) -> ModelResponse:
        # TODO: Implement the core logic of your model here

        return ModelResponse(
            text="Hello, World!",
        )


# %%
# .. tip:: Optionally, you can implement a format method to format the prompt before sending it to the model API.
#  Refer to :ref:`prompt-engineering` for more details.
#
# Further Reading
# ---------------------
# - :ref:`prompt-engineering`
# - :ref:`streaming`
# - :ref:`structured-output`
