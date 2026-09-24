# -*- coding: utf-8 -*-
"""The MiniMax chat model implementation."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from .._anthropic import AnthropicChatModel
from ...credential import MiniMaxCredential
from ...formatter import FormatterBase, MiniMaxChatFormatter


class MiniMaxChatModel(AnthropicChatModel):
    """Chat model for MiniMax's Anthropic-compatible API."""

    type: Literal["minimax_chat"] = "minimax_chat"
    """The type of the chat model."""

    class Parameters(BaseModel):
        """The parameters for the MiniMax chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description=(
                "The maximum number of tokens to generate in the chat "
                "completion."
            ),
            gt=0,
        )

        thinking_enable: bool = Field(
            default=False,
            title="Thinking",
            description="Whether to enable adaptive thinking.",
        )

    def __init__(
        self,
        credential: MiniMaxCredential,
        model: str,
        parameters: "MiniMaxChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 1_000_000,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the MiniMax chat model.

        Args:
            credential (`MiniMaxCredential`):
                The credential used to authenticate MiniMax API calls.
            model (`str`):
                The MiniMax model name, e.g. ``MiniMax-M3``.
            parameters (`MiniMaxChatModel.Parameters | None`, defaults to \
            `None`):
                The MiniMax API parameters.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the MiniMax API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `1000000`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter used to serialize messages.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra arguments forwarded to ``anthropic.AsyncAnthropic``.
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters or self.Parameters(),
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
            formatter=formatter or MiniMaxChatFormatter(),
            client_kwargs=client_kwargs,
        )

    def _thinking_mode(self) -> str | None:
        """Resolve MiniMax's adaptive thinking mode."""
        return "adaptive" if self.parameters.thinking_enable else None

    def _build_thinking_config(
        self,
        max_tokens: int,
    ) -> tuple[dict[str, Any] | None, int]:
        """Build MiniMax's thinking configuration."""
        mode = self._thinking_mode()
        if mode is None:
            return None, max_tokens
        return {"type": mode}, max_tokens

    def _build_output_config(self) -> None:
        """MiniMax does not support Anthropic's output configuration."""
        return None
