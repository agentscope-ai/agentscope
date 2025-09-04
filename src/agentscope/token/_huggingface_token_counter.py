# -*- coding: utf-8 -*-
"""The huggingface token counter class."""
import os
from typing import Any

from agentscope.token._token_base import TokenCounterBase


class HuggingFaceTokenCounter(TokenCounterBase):
    """The token counter for Huggingface models."""

    def __init__(
        self,
        pretrained_model_name_or_path: str,
        use_mirror: bool = False,
        use_fast: bool = False,
        trust_remote_code: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the huggingface token counter.

        Args:
            pretrained_model_name_or_path (`str`):
                The name or path of the pretrained model, which will be used
                to download the tokenizer from Huggingface Hub.
            use_mirror (`bool`, defaults to `False`):
                Whether to enable the HuggingFace mirror, which is useful for
                users in China.
            use_fast (`bool`, defaults to `False`):
                The argument that will be passed to the tokenizer.
            trust_remote_code (`bool`, defaults to `False`):
                The argument that will be passed to the tokenizer.
            **kwargs:
                Additional keyword arguments that will be passed to the
                tokenizer.
        """
        if use_mirror:
            os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(
            pretrained_model_name_or_path,
            use_fast=use_fast,
            trust_remote_code=trust_remote_code,
            **kwargs,
        )

        if self.tokenizer.chat_template is None:
            raise ValueError(
                f"The tokenizer for model {pretrained_model_name_or_path} in "
                f"transformers does not have chat template.",
            )

    async def count(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> int:
        """Count the number of tokens with the tokenizer download from
        HuggingFace hub.

        Args:
            messages (`list[dict]`):
                A list of message dictionaries
            tools (`list[dict] | None`, defaults to `None`):
                The JSON schema of the tools, which will also be involved in
                the token counting.
            **kwargs (`Any`):
                The additional keyword arguments that will be passed to the
                tokenizer, e.g. `chat_template`, `padding`, etc.
        """
        tokenized_msgs = self.tokenizer.apply_chat_template(
            messages,
            add_generation_prompt=False,
            tokenize=True,
            return_tensors="np",
            tools=tools,
            **kwargs,
        )[0]

        return len(tokenized_msgs)
