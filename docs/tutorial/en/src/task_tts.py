# -*- coding: utf-8 -*-
"""
.. _tts:

TTS
====================

AgentScope provides a unified interface for Text-to-Speech (TTS) models across multiple API providers.
This tutorial demonstrates how to use TTS models in AgentScope.

AgentScope supports the following TTS APIs:

.. list-table:: Built-in TTS Models
    :header-rows: 1

    * - API
      - Class
      - Streaming Input
      - Non-Streaming Input
      - Streaming Output
      - Non-Streaming Output
    * - DashScope Realtime API
      - ``DashScopeRealtimeTTSModel``
      - ✅
      - ✅
      - ✅
      - ✅
    * - DashScope CosyVoice Realtime API
      - ``DashScopeCosyVoiceRealtimeTTSModel``
      - ✅
      - ✅
      - ✅
      - ✅
    * - DashScope API
      - ``DashScopeTTSModel``
      - ❌
      - ✅
      - ✅
      - ✅
    * - DashScope CosyVoice API
      - ``DashScopeCosyVoiceTTSModel``
      - ❌
      - ✅
      - ✅
      - ✅
    * - OpenAI API
      - ``OpenAITTSModel``
      - ❌
      - ✅
      - ✅
      - ✅
    * - Gemini API
      - ``GeminiTTSModel``
      - ❌
      - ✅
      - ✅
      - ✅

.. note:: The streaming input and output in AgentScope TTS models are all accumulative.

**Choosing the Right Model:**

- **Use Non-Realtime TTS** when you have complete text ready (e.g., pre-written
  responses, complete LLM outputs)
- **Use Realtime TTS** when text is generated progressively (e.g., streaming
  LLM responses) for lower latency

"""

import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.tts import (
    DashScopeRealtimeTTSModel,
    DashScopeTTSModel,
)

# %%
# Non-Realtime TTS
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Non-realtime TTS models process complete text inputs and are the simplest
# to use. You can directly call their ``synthesize()`` method.
#
# Taking DashScope TTS model as an example:


async def example_non_realtime_tts() -> None:
    """A basic example of using non-realtime TTS models."""
    # Example with DashScope TTS
    tts_model = DashScopeTTSModel(
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        model_name="qwen3-tts-flash",
        voice="Cherry",
        stream=False,  # Non-streaming output
    )

    msg = Msg(
        name="assistant",
        content="Hello, this is DashScope TTS.",
        role="assistant",
    )

    # Directly synthesize without connecting
    tts_response = await tts_model.synthesize(msg)

    # tts_response.content contains an audio block with base64-encoded audio data
    print(
        "The length of audio data:",
        len(tts_response.content["source"]["data"]),
    )


asyncio.run(example_non_realtime_tts())

# %%
# **Streaming Output for Lower Latency:**
#
# When ``stream=True``, the model returns audio chunks progressively, allowing
# you to start playback before synthesis completes. This reduces perceived latency.
#


async def example_non_realtime_tts_streaming() -> None:
    """An example of using non-realtime TTS models with streaming output."""
    # Example with DashScope TTS with streaming output
    tts_model = DashScopeTTSModel(
        api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
        model_name="qwen3-tts-flash",
        voice="Cherry",
        stream=True,  # Enable streaming output
    )

    msg = Msg(
        name="assistant",
        content="Hello, this is DashScope TTS with streaming output.",
        role="assistant",
    )

    # Synthesize and receive an async generator for streaming output
    async for tts_response in await tts_model.synthesize(msg):
        # Process each audio chunk as it arrives
        print(
            "Received audio chunk of length:",
            len(tts_response.content["source"]["data"]),
        )


asyncio.run(example_non_realtime_tts_streaming())


# %%
# Realtime TTS
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Realtime TTS models are designed for scenarios where text is generated
# incrementally, such as streaming LLM responses. This enables the lowest
# possible latency by starting audio synthesis before the complete text is ready.
#
# **Key Concepts:**
#
# - **Stateful Processing**: Realtime TTS maintains state for a single streaming
#   session, identified by ``msg.id``. Only one streaming session can be active
#   at a time.
# - **Two Methods**:
#
#   - ``push(msg)``: Non-blocking method that submits text chunks and returns
#     immediately. May return partial audio if available.
#   - ``synthesize(msg)``: Blocking method that finalizes the session and returns
#     all remaining audio. When ``stream=True``, it returns an async generator.
#
# .. code-block:: python
#
#     async def example_realtime_tts_streaming():
#         tts_model = DashScopeRealtimeTTSModel(
#             api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
#             model_name="qwen3-tts-flash-realtime",
#             voice="Cherry",
#             stream=False,
#         )
#
#         # realtime tts model received accumulative text chunks
#         res = await tts_model.push(msg_chunk_1)  # non-blocking
#         res = await tts_model.push(msg_chunk_2)  # non-blocking
#         ...
#         res = await tts_model.synthesize(final_msg)  # blocking, get all remaining audio
#
# When setting ``stream=True`` during initialization, the ``synthesize()`` method returns an async generator of ``TTSResponse`` objects, allowing you to process audio chunks as they arrive.
#
#
# Integrating with ReActAgent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope agents can automatically synthesize their responses to speech
# when provided with a TTS model. This works seamlessly with both realtime
# and non-realtime TTS models.
#
# **How It Works:**
#
# 1. The agent generates a text response (potentially streamed from an LLM)
# 2. The TTS model synthesizes the text to audio automatically
# 3. The synthesized audio is attached to the ``speech`` field of the ``Msg`` object
# 4. The audio is played during the agent's ``self.print()`` method
#


async def example_agent_with_tts() -> None:
    """An example of using TTS with ReActAgent."""
    # Create an agent with TTS enabled
    agent = ReActAgent(
        name="Assistant",
        sys_prompt="You are a helpful assistant.",
        model=DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY", ""),
            model_name="qwen-max",
            stream=True,
        ),
        formatter=DashScopeChatFormatter(),
        # Enable TTS
        tts_model=DashScopeRealtimeTTSModel(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            model_name="qwen3-tts-flash-realtime",
            voice="Cherry",
        ),
    )
    user = UserAgent("User")

    # Build a conversation just like normal
    msg = None
    while True:
        msg = await agent(msg)
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break


# %%
# Customizing TTS Model
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# You can create custom TTS implementations by inheriting from ``TTSModelBase``.
# The base class provides a flexible interface for both realtime and non-realtime
# TTS models.
# We use an attribute ``supports_streaming_input`` to indicate if the TTS model is realtime or not.
#
# For realtime TTS models, you need to implement the ``connect``, ``close``, ``push`` and ``synthesize`` methods to handle the lifecycle and streaming input.
#
# While for non-realtime TTS models, you only need to implement the ``synthesize`` method.
#
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# - :ref:`agent` - Learn more about agents in AgentScope
# - :ref:`message` - Understand message format in AgentScope
# - API Reference: :class:`agentscope.tts.TTSModelBase`
#
