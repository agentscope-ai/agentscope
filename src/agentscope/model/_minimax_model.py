# -*- coding: utf-8 -*-
"""MiniMax Chat model class."""
from typing import Any

from ._openai_model import OpenAIChatModel
from ..types import JSONSerializableObject


class MiniMaxChatModel(OpenAIChatModel):
    """The MiniMax chat model class.

    MiniMax provides an OpenAI-compatible API, so this class inherits from
    OpenAIChatModel and sets the appropriate defaults for MiniMax's API
    endpoint.

    Available models include:
    - MiniMax-M2.5: Flagship model with 204K context window
    - MiniMax-M2.5-highspeed: Speed-optimized variant

    Note: MiniMax does not accept a temperature of exactly 0. Use a small
    positive value (e.g., 0.01) instead.
    """

    def __init__(
        self,
        model_name: str = "MiniMax-M2.5",
        api_key: str | None = None,
        stream: bool = True,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the MiniMax client.

        Args:
            model_name (`str`, default `"MiniMax-M2.5"`):
                The name of the model to use. Available models:
                "MiniMax-M2.5", "MiniMax-M2.5-highspeed".
            api_key (`str`, default `None`):
                The API key for MiniMax API. If not specified, it will
                be read from the environment variable `MINIMAX_API_KEY`.
            stream (`bool`, default `True`):
                Whether to use streaming output or not.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments to initialize the OpenAI client.
                The `base_url` is automatically set to MiniMax's API endpoint
                unless explicitly overridden.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
             optional):
                The extra keyword arguments used in API generation,
                e.g. `temperature`, `seed`.
            **kwargs (`Any`):
                Additional keyword arguments.
        """
        import os

        if api_key is None:
            api_key = os.environ.get("MINIMAX_API_KEY")

        client_kwargs = client_kwargs or {}
        client_kwargs.setdefault("base_url", "https://api.minimax.io/v1")

        super().__init__(
            model_name=model_name,
            api_key=api_key,
            stream=stream,
            client_kwargs=client_kwargs,
            generate_kwargs=generate_kwargs,
            **kwargs,
        )
