# -*- coding: utf-8 -*-
"""The Atlas Cloud chat model implementation."""
from typing import Any, Literal

from ...credential import AtlasCloudCredential
from .._openai_chat import OpenAIChatModel


class AtlasCloudChatModel(OpenAIChatModel):
    """Atlas Cloud chat model using the OpenAI-compatible API surface."""

    type: Literal["atlascloud_chat"] = "atlascloud_chat"
    """The type of the chat model."""

    def __init__(
        self,
        credential: AtlasCloudCredential,
        model: str = "qwen/qwen3.5-flash",
        parameters: "OpenAIChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 1_000_000,
        client_kwargs: dict[str, Any] | None = None,
        extra_body: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the Atlas Cloud chat model.

        Args:
            credential (`AtlasCloudCredential`):
                The Atlas Cloud credential used to authenticate API calls.
            model (`str`, defaults to `"qwen/qwen3.5-flash"`):
                The Atlas Cloud model id.
            parameters (`OpenAIChatModel.Parameters | None`, defaults to \
            `None`):
                The OpenAI-compatible Chat API parameters.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `1_000_000`):
                The model context size used for context compression.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra keyword arguments forwarded to ``openai.AsyncClient``.
            extra_body (`dict[str, Any] | None`, defaults to `None`):
                Additional request body fields forwarded to Atlas Cloud.
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters,
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
            client_kwargs=client_kwargs,
            extra_body=extra_body,
        )
