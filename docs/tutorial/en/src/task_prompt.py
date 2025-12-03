# -*- coding: utf-8 -*-
"""
.. _prompt:

Prompt Formatter
=========================

The formatter module in AgentScope is responsible for

- converting messages into the expected format for different LLM APIs,
- (optional) truncating messages to fit within token limits,
- (optional) prompt engineering, e.g. summarizing long conversations.

The last two are optional and can also be handled by developers within the memory or at the agent level.

In AgentScope, there are two types of formatters, "ChatFormatter" and "MultiAgentFormatter", distinguished by the agent identities in their input messages.

- **ChatFormatter**: Designed for standard user-assistant scenario (chatbot), using the ``role`` field to identify the user and assistant.
- **MultiAgentFormatter**: Designed for multi-agent scenario, use the ``name`` field to identify different agents, which will combine conversation history into a single user message dictionary.

The built-in formatters are listed below

.. list-table:: The built-in formatters in AgentScope
    :header-rows: 1

    * - API Provider
      - User-assistant Scenario
      - Multi-Agent Scenario
    * - OpenAI
      - ``OpenAIChatFormatter``
      - ``OpenAIMultiAgentFormatter``
    * - Anthropic
      - ``AnthropicChatFormatter``
      - ``AnthropicMultiAgentFormatter``
    * - DashScope
      - ``DashScopeChatFormatter``
      - ``DashScopeMultiAgentFormatter``
    * - Gemini
      - ``GeminiChatFormatter``
      - ``GeminiChatFormatter``
    * - Ollama
      - ``OllamaChatFormatter``
      - ``OllamaMultiAgentFormatter``
    * - DeedSeek
      - ``DeepSeekChatFormatter``
      - ``DeepSeekMultiAgentFormatter``
    * - vLLM
      - ``OpenAIChatFormatter``
      - ``OpenAIMultiAgentFormatter``

.. tip:: The OpenAI API supports the `name` field, so that `OpenAIChatFormatter` can also be used in multi-agent scenario. You can also use the `OpenAIMultiAgentFormatter` instead, which combine conversation history into a single user message.

Besides, the built-in formatters support to convert different message blocks into the expected format for the target API, which are list below:

.. list-table:: The supported message blocks in the built-in formatters
    :header-rows: 1

    * - Formatter
      - tool_use/result
      - image
      - audio
      - video
      - thinking
    * - ``OpenAIChatFormatter``
      - ✅
      - ✅
      - ✅
      - ❌
      -
    * - ``DashScopeChatFormatter``
      - ✅
      - ✅
      - ✅
      - ❌
      -
    * - ``DashScopeMultiAgentFormatter``
      - ✅
      - ✅
      - ✅
      - ❌
      -
    * - ``AnthropicChatFormatter``
      - ✅
      - ✅
      - ❌
      - ❌
      - ✅
    * - ``AnthropicMultiAgentFormatter``
      - ✅
      - ✅
      - ❌
      - ❌
      - ✅
    * - ``GeminiChatFormatter``
      - ✅
      - ✅
      - ✅
      - ✅
      -
    * - ``GeminiMultiAgentFormatter``
      - ✅
      - ✅
      - ✅
      - ✅
      -
    * - ``OllamaChatFormatter``
      - ✅
      - ✅
      - ❌
      - ❌
      -
    * - ``OllamaMultiAgentFormatter``
      - ✅
      - ✅
      - ❌
      - ❌
      -
    * - ``DeepSeekChatFormatter``
      - ✅
      - ❌
      - ❌
      - ❌
      -
    * - ``DeepSeekMultiAgentFormatter``
      - ✅
      - ❌
      - ❌
      - ❌
      -

.. note:: As stated in the `official documentation <https://docs.anthropic.com/en/docs/build-with-claude/extended-thinking#preserving-thinking-blocks>`_, only Anthropic suggests to preserve the thinking blocks in prompt formatting. For the others, we just ignore the thinking blocks in the input messages.

ReAct-Oriented Formatting
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The built-in formatters are all designed to support ReAct-style agents, where the input messages **consist of alternating conversation history and tool call sequences**.

In user-assistant scenario, the conversation history includes the user and assistant messages, we just convert them into the expected format directly.
However, in multi-agent scenario, the conversation history is a list of messages from different agents as follows:

.. figure:: ../../_static/images/multiagent_msgs.png
    :alt: example of multiagent messages
    :width: 85%
    :align: center

    *Example of multi-agent messages*


Therefore, we have to merge the conversation history into a single user message with tags "<history>" and "</history>".
Taking DashScope as an example, the formatted message will look like this:
"""

from agentscope.token import HuggingFaceTokenCounter
from agentscope.formatter import DashScopeMultiAgentFormatter
from agentscope.message import Msg, ToolResultBlock, ToolUseBlock, TextBlock
import asyncio, json


input_msgs = [
    # System prompt
    Msg("system", "You're a helpful assistant named Friday", "system"),
    # Conversation history
    Msg("Bob", "Hi, Alice, do you know the nearest library?", "assistant"),
    Msg(
        "Alice",
        "Sorry, I don't know. Do you have any idea, Charlie?",
        "assistant",
    ),
    Msg(
        "Charlie",
        "No, let's ask Friday. Friday, get me the nearest library.",
        "assistant",
    ),
    # Tool sequence
    Msg(
        "Friday",
        [
            ToolUseBlock(
                type="tool_use",
                name="get_current_location",
                id="1",
                input={},
            ),
        ],
        "assistant",
    ),
    Msg(
        "system",
        [
            ToolResultBlock(
                type="tool_result",
                name="get_current_location",
                id="1",
                output=[TextBlock(type="text", text="104.48, 36.30")],
            ),
        ],
        "system",
    ),
    Msg(
        "Friday",
        [
            ToolUseBlock(
                type="tool_use",
                name="search_around",
                id="2",
                input={"location": [104.48, 36.30], "keyword": "library"},
            ),
        ],
        "assistant",
    ),
    Msg(
        "system",
        [
            ToolResultBlock(
                type="tool_result",
                name="search_around",
                id="2",
                output=[TextBlock(type="text", text="[...]")],
            ),
        ],
        "system",
    ),
    # Conversation history continues
    Msg("Friday", "The nearest library is ...", "assistant"),
    Msg("Bob", "Thanks, Friday!", "user"),
    Msg("Alice", "Let's go together.", "user"),
]


async def run_formatter_example() -> list[dict]:
    """Example of how to format multi-agent messages."""
    formatter = DashScopeMultiAgentFormatter()
    formatted_message = await formatter.format(input_msgs)
    print("The formatted message:")
    print(json.dumps(formatted_message, indent=4))
    return formatted_message


formatted_message = asyncio.run(run_formatter_example())

# %%
# Specifically, the conversation histories are formatted into:
#
print("The first conversation history:")
print(formatted_message[1]["content"])

print("\nThe second conversation history:")
print(formatted_message[-1]["content"])

# %%
# Truncation-based Formatting
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# With the token module in AgentScope, the built-in formatters support to truncate the input messages by **deleting the oldest messages** (except the system prompt message) when the token exceeds the limit.
#
# Taking OpenAIFormatter as an example, we first calculate the total number of tokens of the input messages.
#


async def run_token_counter() -> int:
    """Compute the token number of the input messages."""
    # We use huggingface token counter for dashscope models.
    token_counter = HuggingFaceTokenCounter(
        "Qwen/Qwen2.5-VL-3B-Instruct",
        use_mirror=False,
    )

    return await token_counter.count(formatted_message)


n_tokens = asyncio.run(run_token_counter())
print("The tokens in the formatted messages are: ", n_tokens)


# %%
# Then we set the maximum token limit to 20 tokens less than the total number of tokens and run the formatter.
#


async def run_truncated_formatter() -> None:
    """Example of how to format messages with truncation."""
    token_counter = HuggingFaceTokenCounter(
        pretrained_model_name_or_path="Qwen/Qwen2.5-VL-3B-Instruct",
        use_mirror=False,
    )
    formatter = DashScopeMultiAgentFormatter(
        token_counter=token_counter,
        max_tokens=n_tokens - 20,
    )
    truncated_formatted_message = await formatter.format(input_msgs)
    n_truncated_tokens = await token_counter.count(truncated_formatted_message)
    print("The tokens after truncation: ", n_truncated_tokens)

    print("\nThe conversation history after truncation:")
    print(truncated_formatted_message[1]["content"])


asyncio.run(run_truncated_formatter())

# %%
# We can see the first two messages from Bob and Alice are removed to fit within the context length limits.
#
#
# Customizing Formatter
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope provides two base classes ``FormatterBase`` and its child class ``TruncatedFormatterBase``.
# The ``TruncatedFormatterBase`` class provides the FIFO truncation strategy, and all the built-in formatters are inherited from it.
#
# .. list-table:: The base classes of formatters in AgentScope
#   :header-rows: 1
#
#   * - Class
#     - Abstract Method
#     - Description
#   * - ``FormatterBase``
#     - ``format``
#     - Format the input ``Msg`` objects into the expected format for the target API
#   * - ``TruncatedFormatterBase``
#     - ``_format_agent_message``
#     - Format the agent messages, which may contain multiple identities in multi-agent scenario
#   * -
#     - ``_format_tool_sequence``
#     - Format the tool use and result sequence into the expected format
#   * -
#     - ``_format`` (optional)
#     - Format the input ``Msg`` objects into the expected format for the target API
#
# .. tip:: - The ``_format`` in ``TruncatedFormatterBase`` groups input messages into agent messages and tool sequences, and then format them by calling ``_format_agent_message`` and ``_format_tool_sequence`` respectively. You can override it to implement your own formatting strategy.
#  - Optionally, you can override the ``_truncate`` method in ``TruncatedFormatterBase`` to implement your own truncation strategy.
#
# Further Reading
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
#
# - :ref:`token`
# - :ref:`model`
#
