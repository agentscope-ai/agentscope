# -*- coding: utf-8 -*-
"""The token base class in agentscope."""
from abc import abstractmethod
from typing import Any


class TokenCounterBase:
    """The base class for token counting."""

    @abstractmethod
    async def count(
        self,
        messages: list[dict],
        **kwargs: Any,
    ) -> int:
        """Count the number of tokens by the given model and messages."""
