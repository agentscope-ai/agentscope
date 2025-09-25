# -*- coding: utf-8 -*-
"""The model usage class"""
from typing import Optional, Literal

from pydantic import BaseModel, Field


class ChatUsage(BaseModel):
    """The base model for chat model usage"""

    class Usage(BaseModel):
        """The usage class for chat model usage"""

        prompt_tokens: int = Field(
            description="The number of tokens used in the prompt",
            ge=0,
        )
        completion_tokens: int = Field(
            description="The number of tokens used in the completion",
            ge=0,
        )
        total_tokens: int = Field(
            description="The total number of tokens used",
            ge=0,
        )

    type: Literal["chat"] = Field(default="chat")
    usage: Usage = Field()

    def __init__(
        self,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: Optional[int] = None,
    ) -> None:
        """The init function that allows lazy initialization of total_tokens

        Args:
            prompt_tokens (`int`):
                The number of tokens used in the prompt
            completion_tokens (`int`):
                The number of tokens used in the completion
            total_tokens (`int`, defaults to `None`):
                The total number of tokens used
        """
        super().__init__(
            usage=ChatUsage.Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens
                if total_tokens is not None
                else prompt_tokens + completion_tokens,
            ),
        )
