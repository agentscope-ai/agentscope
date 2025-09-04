# -*- coding: utf-8 -*-
"""The Anthropic token counter class."""
from typing import Any


class AnthropicTokenCounter:
    """The Anthropic token counter class."""

    def __init__(self, model_name: str, api_key: str, **kwargs: Any) -> None:
        """Initialize the Anthropic token counter.

        Args:
            model_name (`str`):
                The name of the Anthropic model to use, e.g. "claude-2".
            api_key (`str`):
                The API key for Anthropic.
        """
        import anthropic

        self.client = anthropic.AsyncAnthropic(api_key=api_key, **kwargs)
        self.model_name = model_name

    async def count(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> int:
        """Count the number of tokens for the given messages

        .. note:: The Anthropic token counting API requires the multimodal
         data to be in base64 format,

        Args:
            messages (`list[dict]`):
                A list of dictionaries, where `role` and `content` fields are
                required.
            tools (`list[dict] | None`, defaults to `None`):
                The tools JSON schemas that the model can use.
            **kwargs (`Any`):
                Additional keyword arguments for the token counting API.
        """
        system_message = None
        if messages[0].get("role") == "system":
            system_message = messages.pop(0)

        extra_kwargs: dict = {
            "model": self.model_name,
            "messages": messages,
            **kwargs,
        }

        if tools:
            extra_kwargs["tools"] = tools

        if system_message:
            extra_kwargs["system"] = system_message

        res = await self.client.messages.count_tokens(**extra_kwargs)

        return res.input_tokens
