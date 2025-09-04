# -*- coding: utf-8 -*-
"""The gemini token counter class in agentscope."""
from typing import Any

from agentscope.token._token_base import TokenCounterBase


class GeminiTokenCounter(TokenCounterBase):
    """The Gemini token counter class."""

    def __init__(self, model_name: str, api_key: str, **kwargs: Any) -> None:
        """Initialize the Gemini token counter.

        Args:
            model_name (`str`):
                The name of the Gemini model to use, e.g. "gemini-2.5-flash".
            api_key (`str`):
                The API key for Google Gemini.
            **kwargs:
                Additional keyword arguments that will be passed to the
                Gemini client.
        """
        from google import genai

        self.client = genai.Client(
            api_key=api_key,
            **kwargs,
        )
        self.model_name = model_name

    async def count(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **config_kwargs: Any,
    ) -> int:
        """Count the number of tokens of gemini models."""

        kwargs = {
            "model": self.model_name,
            "contents": messages,
            "config": {
                "tools": tools,
                **config_kwargs,
            },
        }

        res = self.client.models.count_tokens(**kwargs)

        return res.total_tokens
