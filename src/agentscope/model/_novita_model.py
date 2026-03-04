# -*- coding: utf-8 -*-
"""Novita Chat model class."""
from typing import (
    Any,
    Literal,
)

from ._openai_model import OpenAIChatModel
from ..types import JSONSerializableObject


class NovitaChatModel(OpenAIChatModel):
    """The Novita chat model class."""

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        stream: bool = True,
        reasoning_effort: Literal["low", "medium", "high"] | None = None,
        organization: str = None,
        stream_tool_parsing: bool = True,
        client_kwargs: dict[str, JSONSerializableObject] | None = None,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Novita client.

        Args:
            model_name (`str`):
                The name of the model to use in Novita AI API.
            api_key (`str`, optional):
                The API key for Novita AI API. If not specified, it will
                be read from the environment variable `NOVITA_API_KEY` or
                `OPENAI_API_KEY`.
            stream (`bool`, default `True`):
                Whether to use streaming output or not.
            reasoning_effort (`Literal["low", "medium", "high"] | None`, 
            optional):
                Reasoning effort, supported for some models.
            organization (`str`, optional):
                The organization ID for Novita AI API.
            stream_tool_parsing (`bool`, default `True`):
                Whether to parse incomplete tool use JSON during streaming.
            client_kwargs (`dict[str, JSONSerializableObject] | None`, 
            optional):
                The extra keyword arguments to initialize the OpenAI client.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, 
            optional):
                The extra keyword arguments used in generation.
            **kwargs (`Any`):
                Additional keyword arguments.
        """
        import os

        if api_key is None:
            api_key = os.environ.get("NOVITA_API_KEY") or os.environ.get(
                "OPENAI_API_KEY",
            )

        if client_kwargs is None:
            client_kwargs = {}

        if "base_url" not in client_kwargs:
            client_kwargs["base_url"] = "https://api.novita.ai/openai"

        super().__init__(
            model_name=model_name,
            api_key=api_key,
            stream=stream,
            reasoning_effort=reasoning_effort,
            organization=organization,
            stream_tool_parsing=stream_tool_parsing,
            client_kwargs=client_kwargs,
            generate_kwargs=generate_kwargs,
            **kwargs,
        )
