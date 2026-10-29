# -*- coding: utf-8 -*-
"""The MiniMax chat model implementation."""

from typing import Any, Literal

from pydantic import BaseModel, Field

from .._anthropic import AnthropicChatModel
from .._model_card import ModelCard
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
            description=(
                "Whether to enable adaptive thinking. MiniMax-M3.1-Flash "
                "always uses adaptive thinking, regardless of this toggle."
            ),
        )

        reasoning_effort: Literal[
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        ] = Field(
            default="max",
            title="Reasoning Effort",
            description="Thinking depth for MiniMax-M3.1-Flash only.",
        )

    def __init__(
        self,
        credential: MiniMaxCredential,
        model: str = "MiniMax-M3.1-Flash",
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
            model (`str`, defaults to `"MiniMax-M3.1-Flash"`):
                The MiniMax model name. Explicit legacy and custom names are
                forwarded unchanged.
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

    @classmethod
    def list_models(
        cls,
        custom_yaml_dir: str | None = None,
        *,
        include_legacy: bool = False,
    ) -> list[ModelCard]:
        """List current candidates, optionally retaining saved legacy cards.

        Args:
            custom_yaml_dir (`str | None`, defaults to `None`):
                A caller-owned catalog, returned without filtering.
            include_legacy (`bool`, defaults to `False`):
                Include historical cards when resolving saved configurations.
        """
        cards = super().list_models(custom_yaml_dir)
        if custom_yaml_dir is not None or include_legacy:
            return cards
        return [
            card
            for name in ("MiniMax-M3.1-Flash", "MiniMax-M3")
            for card in cards
            if card.name == name
        ]

    def _build_thinking_config(
        self,
        max_tokens: int,
    ) -> tuple[dict[str, Any] | None, int]:
        """MiniMax only accepts adaptive thinking, without a token budget."""
        if (
            self.model == "MiniMax-M3.1-Flash"
            or self.parameters.thinking_enable
        ):
            return {"type": "adaptive"}, max_tokens
        return None, max_tokens

    def _get_disable_thinking_kwargs(self) -> dict:
        """Flash cannot use the no-thinking structured-output fallback."""
        if self.model == "MiniMax-M3.1-Flash":
            return {}
        return super()._get_disable_thinking_kwargs()

    def _build_output_config(self) -> dict[str, Any] | None:
        """Only Flash supports configurable thinking depth."""
        if self.model == "MiniMax-M3.1-Flash":
            return {"effort": self.parameters.reasoning_effort}
        return None
