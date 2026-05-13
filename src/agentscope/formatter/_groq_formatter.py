# -*- coding: utf-8 -*-
"""The Groq formatter for agentscope.

Groq uses an OpenAI-compatible API, so the formatters are aliases
of the OpenAI formatters.
"""
from ._openai_formatter import OpenAIChatFormatter, OpenAIMultiAgentFormatter


class GroqChatFormatter(OpenAIChatFormatter):
    """The Groq formatter class for chatbot scenario.

    Groq uses an OpenAI-compatible message format, so this class
    inherits directly from :class:`OpenAIChatFormatter`.
    """


class GroqMultiAgentFormatter(OpenAIMultiAgentFormatter):
    """The Groq formatter class for multi-agent scenario.

    Groq uses an OpenAI-compatible message format, so this class
    inherits directly from :class:`OpenAIMultiAgentFormatter`.
    """
