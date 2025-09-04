# -*- coding: utf-8 -*-
"""Utility classes for integrating AgentScope with mem0 library.

This module provides wrapper classes that allow AgentScope models to be used
with the mem0 library for long-term memory functionality.
"""
import asyncio
from typing import Any, Dict, List, Literal

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.configs.llms.base import BaseLlmConfig
from mem0.embeddings.base import EmbeddingBase
from mem0.llms.base import LLMBase


from ..embedding import EmbeddingModelBase
from ..model import ChatModelBase, ChatResponse


class AgentScopeLLM(LLMBase):
    """Wrapper for the AgentScope LLM.

    This class is a wrapper for the AgentScope LLM. It is used to generate
    responses using the AgentScope LLM in mem0.
    """

    def __init__(self, config: BaseLlmConfig | None = None):
        """Initialize the AgentScopeLLM wrapper.

        Args:
            config (`BaseLlmConfig | None`, optional):
                Configuration object for the LLM. Default is None.
        """
        super().__init__(config)

        if self.config.model is None:
            raise ValueError("`model` parameter is required")

        if not isinstance(self.config.model, ChatModelBase):
            raise ValueError("`model` must be an instance of ChatModelBase")

        self.agentscope_model = self.config.model

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        response_format: Any | None = None,
        tools: List[Dict] | None = None,
        tool_choice: str = "auto",
    ) -> str:
        """Generate a response based on the given messages using agentscope.

        Args:
            messages (`List[Dict[str, str]]`):
                List of message dicts containing 'role' and 'content'.
            response_format (`Any | None`, optional):
                Format of the response. Not used in AgentScope.
            tools (`List[Dict] | None`, optional):
                List of tools that the model can call. Not used in AgentScope.
            tool_choice (`str`, optional):
                Tool choice method. Not used in AgentScope.

        Returns:
            `str`:
                The generated response.
        """
        # pylint: disable=unused-argument
        try:
            # Convert the messages to AgentScope's format
            agentscope_messages = []
            for message in messages:
                role = message["role"]
                content = message["content"]

                if role in ["system", "user", "assistant"]:
                    agentscope_messages.append(
                        {"role": role, "content": content},
                    )

            if not agentscope_messages:
                raise ValueError(
                    "No valid messages found in the messages list",
                )

            # Use the agentscope model to generate response (async call)
            async def _async_call() -> ChatResponse:
                # TODO: handle the streaming response or forbidden streaming
                #  mode
                return await self.agentscope_model(  # type: ignore
                    agentscope_messages,
                    tools=tools,
                )

            response = asyncio.run(_async_call())

            # Extract text from the response content blocks
            if not response.content:
                return ""

            # Collect all text from different block types
            text_parts = []
            thinking_parts = []
            tool_parts = []

            for block in response.content:
                # Handle TextBlock
                if isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block.get("text", ""))

                # Handle ThinkingBlock
                elif (
                    isinstance(block, dict) and block.get("type") == "thinking"
                ):
                    thinking_parts.append(
                        f"[Thinking: {block.get('thinking', '')}]",
                    )

                # Handle ToolUseBlock
                elif (
                    isinstance(block, dict) and block.get("type") == "tool_use"
                ):
                    tool_name = block.get("name")
                    tool_input = block.get("input", {})
                    tool_parts.append(
                        f"[Tool: {tool_name} - {str(tool_input)}]",
                    )

            # Combine all parts in order: thinking, text, tools
            all_parts: list[str] = thinking_parts + text_parts + tool_parts

            if all_parts:
                return "\n".join(all_parts)
            # If no recognized blocks found, try to convert the entire
            # content to string
            return str(response.content)

        except Exception as e:
            raise RuntimeError(
                f"Error generating response using agentscope model: {str(e)}",
            ) from e


class AgentScopeEmbedding(EmbeddingBase):
    """Wrapper for the AgentScope Embedding model.

    This class is a wrapper for the AgentScope Embedding model. It is used
    to generate embeddings using the AgentScope Embedding model in mem0.
    """

    def __init__(self, config: BaseEmbedderConfig | None = None):
        """Initialize the AgentScopeEmbedding wrapper.

        Args:
            config (`BaseEmbedderConfig | None`, optional):
                Configuration object for the embedder. Default is None.
        """
        super().__init__(config)

        if self.config.model is None:
            raise ValueError("`model` parameter is required")

        if not isinstance(self.config.model, EmbeddingModelBase):
            raise ValueError(
                "`model` must be an instance of EmbeddingModelBase",
            )

        self.agentscope_model = self.config.model

    def embed(
        self,
        text: str | List[str],
        memory_action: Literal[  # pylint: disable=unused-argument
            "add",
            "search",
            "update",
        ]
        | None = None,
    ) -> List[float]:
        """Get the embedding for the given text using AgentScope.

        Args:
            text (`str | List[str]`):
                The text to embed.
            memory_action (`Literal["add", "search", "update"] | None`, \
            optional):
                The type of embedding to use. Must be one of "add", "search",
                or "update". Defaults to None.

        Returns:
            `List[float]`:
                The embedding vector.
        """
        try:
            # Convert single text to list for AgentScope embedding model
            text_list = [text] if isinstance(text, str) else text

            # Use the agentscope model to generate embedding (async call)
            async def _async_call() -> Any:
                response = await self.agentscope_model(text_list)
                return response

            response = asyncio.run(_async_call())

            # Extract the embedding vector from the first Embedding object
            # response.embeddings is a list of Embedding objects
            # Each Embedding object has an 'embedding' attribute containing
            # the vector
            embedding = response.embeddings[0]

            if embedding is None:
                raise ValueError("Failed to extract embedding from response")
            return embedding

        except Exception as e:
            raise RuntimeError(
                f"Error generating embedding using agentscope model: {str(e)}",
            ) from e
