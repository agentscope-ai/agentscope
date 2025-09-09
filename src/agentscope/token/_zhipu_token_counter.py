# -*- coding: utf-8 -*-
"""The Zhipu AI token counting class."""
import json
from typing import Any

from ._token_base import TokenCounterBase


def _calculate_tokens_for_tools(
    tools: list[dict],
    encoding: Any,
) -> int:
    """Calculate the tokens for the given tools JSON schema."""
    if not tools:
        return 0

    tools_str = json.dumps(tools, ensure_ascii=False)
    return len(encoding.encode(tools_str))


def _count_content_tokens_for_zhipu_vision_model(
    content: list[dict],
    encoding: Any,
) -> int:
    """Count the number of tokens for the content of a Zhipu vision model.

    Args:
        content (`list[dict]`):
            A list of dictionaries containing text and image content.
        encoding (`Any`):
            The encoding object.

    Returns:
        `int`:
            The number of tokens.
    """
    token_count = 0

    for item in content:
        if item["type"] == "text":
            token_count += len(encoding.encode(item["text"]))
        elif item["type"] == "image_url":
            token_count += 1000
        elif item["type"] == "image":
            token_count += 1000

    return token_count


class ZhipuTokenCounter(TokenCounterBase):
    """The Zhipu AI token counter."""

    def __init__(
        self,
        model_name: str,
        **kwargs: Any,
    ) -> None:
        """Initialize the Zhipu AI token counter.

        Args:
            model_name (`str`):
                The name of the model to count tokens for.
            **kwargs (`Any`):
                Additional keyword arguments.
        """
        self.model_name = model_name

        try:
            import tiktoken

            self.encoding = tiktoken.encoding_for_model(self.model_name)
        except Exception:
            self.encoding = None

    async def count(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        **kwargs: Any,
    ) -> int:
        """Count the number of tokens in the given messages and tools.

        Args:
            messages (`list[dict]`):
                A list of messages to count tokens for.
            tools (`list[dict] | None`, default `None`):
                A list of tools to count tokens for.
            **kwargs (`Any`):
                Additional keyword arguments.

        Returns:
            `int`:
                The number of tokens.
        """
        if self.encoding is None:
            return self._simple_count(messages, tools)

        token_count = 0

        for message in messages:
            token_count += len(self.encoding.encode(message.get("role", "")))
            if "name" in message:
                token_count += len(self.encoding.encode(message["name"]))

            content = message.get("content")
            if content is None:
                continue
            elif isinstance(content, str):
                token_count += len(self.encoding.encode(content))
            elif isinstance(content, list):
                if self._is_vision_model():
                    token_count += (
                        _count_content_tokens_for_zhipu_vision_model(
                            content,
                            self.encoding,
                        )
                    )
                else:
                    for item in content:
                        if item.get("type") == "text":
                            token_count += len(
                                self.encoding.encode(item["text"])
                            )

            if "tool_calls" in message:
                tool_calls_str = json.dumps(
                    message["tool_calls"], ensure_ascii=False
                )
                token_count += len(self.encoding.encode(tool_calls_str))

        if tools:
            token_count += _calculate_tokens_for_tools(tools, self.encoding)

        token_count += len(messages) * 3

        return token_count

    def _simple_count(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
    ) -> int:
        """Simple character-based token counting fallback."""
        total_chars = 0

        for message in messages:
            content = message.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                for item in content:
                    if item.get("type") == "text":
                        total_chars += len(item.get("text", ""))

            if "tool_calls" in message:
                tool_calls_str = json.dumps(
                    message["tool_calls"], ensure_ascii=False
                )
                total_chars += len(tool_calls_str)

        if tools:
            tools_str = json.dumps(tools, ensure_ascii=False)
            total_chars += len(tools_str)

        return int(total_chars * 0.5)

    def _is_vision_model(self) -> bool:
        """Check if the model is a vision model."""
        vision_models = ["glm-4v", "glm-4v-plus"]
        return any(
            self.model_name.startswith(model) for model in vision_models
        )
