# -*- coding: utf-8 -*-
"""A model class for RL Training with Trinity-RFT."""
from typing import (
    Optional,
    TYPE_CHECKING,
)
from ._openai_model import OpenAIChatModel
from ..types import JSONSerializableObject


if TYPE_CHECKING:
    from openai import AsyncOpenAI
else:
    AsyncOpenAI = "openai.AsyncOpenAI"


class TrinityChatModel(OpenAIChatModel):
    """A model class for RL Training with Trinity-RFT."""

    def __init__(
        self,
        openai_async_client: AsyncOpenAI,
        generate_kwargs: dict[str, JSONSerializableObject] | None = None,
        enable_thinking: Optional[bool] = None,
    ) -> None:
        """Initialize the Trinity model class.

        Args:
            openai_async_client (`AsyncOpenAI`):
                The OpenAI async client instance provided by Trinity-RFT.
            generate_kwargs (`dict[str, JSONSerializableObject] | None`, \
            optional):
                Additional keyword arguments to pass to the model's generate
                method. Defaults to None.
            enable_thinking (`bool`, optional):
                Whether to enable the model's thinking capability. Only
                applicable for Qwen3 series models. Defaults to None.
        """
        model_name = getattr(openai_async_client, "model_path", None)
        if model_name is None:
            raise ValueError(
                "The provided openai_async_client does not have a "
                "`model_path` attribute. Please ensure you are using "
                "the instance provided by Trinity-RFT.",
            )
        super().__init__(
            model_name=model_name,
            api_key="EMPTY",
            generate_kwargs=generate_kwargs,
            stream=False,  # RL training does not support streaming
        )
        if enable_thinking is not None:
            if "chat_template_kwargs" not in self.generate_kwargs:
                self.generate_kwargs["chat_template_kwargs"] = {}
            assert isinstance(
                self.generate_kwargs["chat_template_kwargs"],
                dict,
            ), "chat_template_kwargs must be a dictionary."
            self.generate_kwargs["chat_template_kwargs"][
                "enable_thinking"
            ] = enable_thinking
        # change the client instance to the provided one
        self.client = openai_async_client
