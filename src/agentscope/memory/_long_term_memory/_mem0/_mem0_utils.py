# -*- coding: utf-8 -*-
"""Utility classes for integrating AgentScope with mem0 library.

This module provides wrapper classes that allow AgentScope models to be used
with the mem0 library for long-term memory functionality.
"""
import asyncio
import atexit
import threading
from typing import Any, Coroutine, Dict, List, Literal

from mem0.configs.embeddings.base import BaseEmbedderConfig
from mem0.configs.llms.base import BaseLlmConfig
from mem0.embeddings.base import EmbeddingBase
from mem0.llms.base import LLMBase

from ....embedding import EmbeddingModelBase
from ....model import ChatModelBase, ChatResponse


class _EventLoopManager:
    """Global event loop manager for running async operations in sync context.

    This manager creates and maintains a persistent background event loop
    that runs in a separate daemon thread. This ensures that async model
    clients (like Ollama AsyncClient) remain bound to the same event loop
    across multiple calls, avoiding "Event loop is closed" errors.
    """

    _DEFAULT_TIMEOUT = 5.0  # Default timeout in seconds

    def __init__(self) -> None:
        """Initialize the event loop manager."""
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.loop_started = threading.Event()

        # Register cleanup function to be called on program exit
        atexit.register(self.cleanup)

    def get_loop(self) -> asyncio.AbstractEventLoop:
        """Get or create the persistent background event loop.

        Returns:
            `asyncio.AbstractEventLoop`:
                The persistent event loop running in a background thread.

        Raises:
            `RuntimeError`: If the event loop fails to start within the
            timeout.
        """
        with self.lock:
            if self.loop is None or self.loop.is_closed():

                def run_loop() -> None:
                    """Run the event loop in the background thread."""
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    # Store the loop reference before starting
                    self.loop = loop
                    self.loop_started.set()
                    loop.run_forever()

                # Clear the event before starting the thread
                self.loop_started.clear()

                # Create and start the background thread
                self.thread = threading.Thread(
                    target=run_loop,
                    daemon=True,
                    name="AgentScope-AsyncLoop",
                )
                self.thread.start()

                # Wait for the loop to be ready
                if not self.loop_started.wait(timeout=self._DEFAULT_TIMEOUT):
                    raise RuntimeError(
                        "Timeout waiting for event loop to start",
                    )

            # After waiting, self.loop should be set by the background thread
            assert (
                self.loop is not None
            ), "Event loop was not initialized properly"
            return self.loop

    def cleanup(self) -> None:
        """Cleanup the event loop and thread on program exit."""
        with self.lock:
            if self.loop is not None and not self.loop.is_closed():
                # Stop the event loop gracefully
                self.loop.call_soon_threadsafe(self.loop.stop)

                # Wait for the thread to finish
                if self.thread is not None and self.thread.is_alive():
                    self.thread.join(timeout=self._DEFAULT_TIMEOUT)

                # Close the loop
                self.loop.close()
                self.loop = None
                self.thread = None


# Global event loop manager instance
_event_loop_manager = _EventLoopManager()


def _run_async_in_persistent_loop(coro: Coroutine) -> Any:
    """Run an async coroutine in the persistent background event loop.

    This function uses a global event loop manager to ensure that all
    async operations run in the same event loop, which is crucial for
    async clients like Ollama that bind to a specific event loop.

    Args:
        coro (`Coroutine`):
            The coroutine to run.

    Returns:
        `Any`:
            The result of the coroutine.

    Raises:
        `RuntimeError`:
            If there's an error running the coroutine.
    """
    loop = _event_loop_manager.get_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result()


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

    def _parse_response(
        self,
        model_response: ChatResponse,
        has_tool: bool,
    ) -> str | dict:
        """Parse the model response into a string or
        a dict to follow the mem0 library's format.

        Args:
            model_response (`ChatResponse`): The response from the model.
            has_tool (`bool`): Whether there are tool calls in the response.

        Returns:
            `str | dict`:
                The parsed response. If has_tool is True, return a dict
                with "content" and "tool_calls" keys. Otherwise, return
                a string.
        """
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        tool_parts = []
        for block in model_response.content:
            # Handle TextBlock
            if isinstance(block, dict) and block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))

            # Handle ThinkingBlock
            elif isinstance(block, dict) and block.get("type") == "thinking":
                thinking_parts.append(
                    f"[Thinking: {block.get('thinking', '')}]",
                )
            # Handle ToolUseBlock
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                tool_name = block.get("name")
                tool_input = block.get("input", {})
                tool_parts.append(
                    {
                        "name": tool_name,
                        "arguments": tool_input,
                    },
                )
        text_part = thinking_parts + text_parts
        if has_tool:
            # If there are tool calls, return the content and tool calls
            return {
                "content": "\n".join(text_part) if len(text_part) > 0 else "",
                "tool_calls": tool_parts,
            }
        else:
            return "\n".join(text_part) if len(text_part) > 0 else ""

    def generate_response(
        self,
        messages: List[Dict[str, str]],
        response_format: Any | None = None,
        tools: List[Dict] | None = None,
        tool_choice: str = "auto",
    ) -> str | dict:
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
            `str | dict`:
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

            # Run in the persistent event loop
            # This ensures the model client (e.g., Ollama AsyncClient)
            # always runs in the same event loop, avoiding binding issues
            response = _run_async_in_persistent_loop(
                _async_call(),
            )
            has_tool = tools is not None

            # Extract text from the response content blocks
            if not response.content:
                if has_tool:
                    return {
                        "content": "",
                        "tool_calls": [],
                    }
                else:
                    return ""

            return self._parse_response(response, has_tool)

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

            # Run in the persistent event loop
            # This ensures the model client (e.g., Ollama AsyncClient)
            # always runs in the same event loop, avoiding binding issues
            response = _run_async_in_persistent_loop(
                _async_call(),
            )

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
