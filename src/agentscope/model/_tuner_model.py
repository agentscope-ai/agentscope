# -*- coding: utf-8 -*-
"""TunerChatModel definition."""
from typing import Dict, Any, TYPE_CHECKING

from ._openai_model import OpenAIChatModel

if TYPE_CHECKING:
    import openai


class TunerChatModel(OpenAIChatModel):
    """Chat model for tuning.
    This model is specifically designed for reinforcement learning,
    and the `client` attribute is expected to be set during the tuning
    process. Please don't use this chat model for other purposes.
    """

    def __init__(
        self,
        model_path: str,
        max_model_len: int,
        temperature: float = 1.0,
        top_p: float = 1.0,
        max_tokens: int = 8192,
        enable_thinking: bool | None = None,
        tensor_parallel_size: int = 1,
        inference_engine_num: int = 1,
        tool_call_parser: str = "hermes",
        reasoning_parser: str = "deepseek_r1",
    ) -> None:
        """Initialize the tuner chat model.

        Args:
            model_path (`str`): The path to the model checkpoint.
            max_model_len (`int`): The maximum length of the model, including
                context and generated tokens.
            temperature (`float`): Sampling temperature.
            top_p (`float`): Nucleus sampling probability.
            max_tokens (`int`): Maximum tokens for generation.
            enable_thinking (`bool | None`): Whether to enable thinking
                capability. Only applicable for Qwen3 series models.
            tensor_parallel_size (`int`): The tensor parallel size for
                model inference.
            inference_engine_num (`int`): The number of engines for model
                inference.
            tool_call_parser (`str`): The tool call parser to use.
            reasoning_parser (`str`): The reasoning parser to use.
        """
        super().__init__(
            model_name=model_path,
            api_key="EMPTY",
            stream=False,  # RL training does not support streaming
        )

        self.generate_kwargs["temperature"] = temperature
        self.generate_kwargs["top_p"] = top_p
        self.generate_kwargs["max_tokens"] = max_tokens
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

        self.model_path = model_path
        self.max_model_len = max_model_len
        self.temperature = temperature
        self.top_p = top_p
        self.max_tokens = max_tokens
        self.tensor_parallel_size = tensor_parallel_size
        self.engine_num = inference_engine_num
        self.tool_call_parser = tool_call_parser
        self.reasoning_parser = reasoning_parser
        # set client to None initially, will be set later
        self.client = None  # type: ignore

    def set_openai_client(
        self,
        openai_async_client: "openai.AsyncOpenAI",
    ) -> None:
        """Set the OpenAI async client for the model.

        Args:
            openai_async_client (`AsyncOpenAI`): The OpenAI async client
                instance
        """
        self.client = openai_async_client

    def get_config(self) -> Dict[str, Any]:
        """Get the model configuration.

        Returns:
            `Dict[str, Any]`: The model configuration dictionary.
        """
        return {
            "model_path": self.model_path,
            "max_model_len": self.max_model_len,
            "tensor_parallel_size": self.tensor_parallel_size,
            "engine_num": self.engine_num,
            "tool_call_parser": self.tool_call_parser,
            "reasoning_parser": self.reasoning_parser,
            "enable_openai_api": True,
            "enable_auto_tool_choice": True,
        }
