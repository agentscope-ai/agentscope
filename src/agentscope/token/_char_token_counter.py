# -*- coding: utf-8 -*-
"""A simple character-based token counter implementation."""
from typing import Any

from ._token_base import TokenCounterBase


class CharTokenCounter(TokenCounterBase):
    """A very simple implementation that counts tokens based on character
    length."""

    async def count(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> int:
        """Count the number of tokens in the messages based on the characters

        Args:
            messages (`list[dict]`):
                The list of messages to be counted.
            tools (`list[dict] | None`, *optional*):
                The list of tools, not used in this counter.

        Returns:
            `int`:
                The total number of tokens counted.
        """
        texts = []
        for msg in messages:
            texts.append(str(msg))

        if tools:
            texts.append(str(tools))

        text = "\n".join(texts)
        return len(text)
