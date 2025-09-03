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

    # 智谱AI的工具调用token计算
    # 基于工具定义的JSON字符串长度估算
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
            # 智谱AI图像处理的固定token消耗
            # 根据智谱AI文档，图像通常消耗固定数量的token
            token_count += 1000  # 估算值，实际可能需要根据图像大小调整
        elif item["type"] == "image":
            # 处理其他图像格式
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

        # 智谱AI使用类似GPT的tokenizer
        try:
            import tiktoken
            self.encoding = tiktoken.encoding_for_model("gpt-3.5-turbo")
        except Exception:
            # 如果tiktoken不可用，使用简单的字符计数
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
            # 简单的字符计数fallback
            return self._simple_count(messages, tools)

        token_count = 0

        # 计算消息的token数
        for message in messages:
            # 角色和名称的token
            token_count += len(self.encoding.encode(message.get("role", "")))
            if "name" in message:
                token_count += len(self.encoding.encode(message["name"]))

            # 内容的token
            content = message.get("content")
            if content is None:
                continue
            elif isinstance(content, str):
                token_count += len(self.encoding.encode(content))
            elif isinstance(content, list):
                # 多模态内容
                if self._is_vision_model():
                    token_count += _count_content_tokens_for_zhipu_vision_model(
                        content, self.encoding
                    )
                else:
                    # 只处理文本内容
                    for item in content:
                        if item.get("type") == "text":
                            token_count += len(self.encoding.encode(item["text"]))

            # 工具调用的token
            if "tool_calls" in message:
                tool_calls_str = json.dumps(message["tool_calls"], ensure_ascii=False)
                token_count += len(self.encoding.encode(tool_calls_str))

        # 计算工具定义的token数
        if tools:
            token_count += _calculate_tokens_for_tools(tools, self.encoding)

        # 智谱AI的额外开销
        token_count += len(messages) * 3  # 每条消息的格式开销

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
                tool_calls_str = json.dumps(message["tool_calls"], ensure_ascii=False)
                total_chars += len(tool_calls_str)

        if tools:
            tools_str = json.dumps(tools, ensure_ascii=False)
            total_chars += len(tools_str)

        # 粗略估算：中文字符约等于1.5个token，英文字符约等于0.25个token
        # 这里使用保守估算
        return int(total_chars * 0.5)

    def _is_vision_model(self) -> bool:
        """Check if the model is a vision model."""
        vision_models = ["glm-4v", "glm-4v-plus"]
        return any(self.model_name.startswith(model) for model in vision_models)

