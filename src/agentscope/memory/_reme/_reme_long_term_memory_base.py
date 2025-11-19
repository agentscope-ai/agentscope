# -*- coding: utf-8 -*-
"""Base long-term memory implementation using ReMe library.

This module provides a base class for long-term memory implementations
that integrate with the ReMe library. ReMe enables agents to maintain
persistent, searchable memories across sessions and contexts.

The module handles the integration between AgentScope's memory system and
the ReMe library, including:
- Model configuration and API credential management
- Context lifecycle management (async context managers)
- Graceful handling of missing dependencies
- Error handling with helpful installation instructions

Key Features:
- Supports both DashScope and OpenAI model providers
- Automatic extraction of API credentials and endpoints
- Flexible configuration via config files or kwargs
- Safe fallback behavior when reme_ai is not installed

Dependencies:
    The ReMe library is an optional dependency that must be installed:

        .. code-block:: bash

            pip install reme-ai
    For more information, visit: https://github.com/modelscope/reMe

Subclasses:
    This base class is extended by specific memory type implementations:
    - ReMeToolLongTermMemory: For tool execution patterns and guidelines
    - ReMeTaskLongTermMemory: For task execution experiences and learnings
    - ReMePersonalLongTermMemory: For user preferences and personal information

Example:
    .. code-block:: python

        from agentscope.models import OpenAIChatModel
        from agentscope.embedding import OpenAITextEmbedding
        from agentscope.memory._reme import ReMeToolLongTermMemory

        # Initialize models
        model = OpenAIChatModel(model_name="gpt-4", api_key="...")
        embedding = OpenAITextEmbedding(
            model_name="text-embedding-3-small", api_key="...")

        # Create memory instance
        memory = ReMeToolLongTermMemory(
            agent_name="my_agent",
            user_name="user_123",
            model=model,
            embedding_model=embedding
        )

        # Use memory in async context
        async with memory:
            # Record tool execution
            await memory.record_to_memory(
                thinking="This tool worked well for data processing",
                content=['{"tool_name": "process_data", "success": true, ...}']
            )

            # Retrieve tool guidelines
            result = await memory.retrieve_from_memory(
                keywords=["process_data"]
            )

"""
from abc import ABCMeta
from typing import Any

from .._long_term_memory_base import LongTermMemoryBase
from ...embedding import (
    DashScopeTextEmbedding,
    OpenAITextEmbedding,
)
from ...model import (
    DashScopeChatModel,
    OpenAIChatModel,
)


class ReMeLongTermMemoryBase(LongTermMemoryBase, metaclass=ABCMeta):
    """Base class for ReMe-based long-term memory implementations.

    This class provides the foundation for integrating AgentScope with the ReMe
    library, enabling agents to maintain and retrieve long-term memories across
    different contexts.

    The ReMe library must be installed separately:
        pip install reme-ai

    If the library is not installed, a warning will be issued during
    initialization,
    and runtime errors with installation instructions will be raised
    when attempting
    to use memory operations.
    """

    def __init__(
        self,
        agent_name: str | None = None,
        user_name: str | None = None,
        run_name: str | None = None,
        model: DashScopeChatModel | OpenAIChatModel | None = None,
        embedding_model: (
            DashScopeTextEmbedding | OpenAITextEmbedding | None
        ) = None,
        reme_config_path: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the ReMe-based long-term memory.

        This constructor sets up the connection to the ReMe
        library and configures
        the necessary models for memory operations. The ReMe app
        will be initialized
        with the provided model configurations.

        Args:
            agent_name (`str | None`, optional):
                Name identifier for the agent. Used for organizing
                memories by agent.
            user_name (`str | None`, optional):
                Unique identifier for the user or workspace. This maps
                to workspace_id in ReMe and helps isolate memories across
                different users/workspaces.
            run_name (`str | None`, optional):
                Name identifier for the current execution run or session.
            model (`DashScopeChatModel | OpenAIChatModel | None`, optional):
                The chat model to use for memory operations. The model's
                API credentials and endpoint will be extracted and
                passed to ReMe.
            embedding_model (`DashScopeTextEmbedding | OpenAITextEmbedding | \
            None`, optional):
                The embedding model to use for semantic memory retrieval.
                The model's API credentials and endpoint will be
                extracted and passed to ReMe.
            reme_config_path (`str | None`, optional):
                Path to a custom ReMe configuration file. If not provided, ReMe
                will use its default configuration.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the
                ReMeApp constructor.
                These can include custom ReMe configuration parameters.

        Raises:
            `ValueError`:
                If the provided model is not a DashScopeChatModel or
                OpenAIChatModel, or if the embedding_model is not a
                DashScopeTextEmbedding or OpenAITextEmbedding.

        Note:
            If the reme_ai library is not installed, a warning will be
            issued and self.app will be set to None. Subsequent memory
            operations will raise RuntimeError with installation
            instructions.

        Example:
            .. code-block:: python

                from agentscope.models import OpenAIChatModel
                from agentscope.embedding import OpenAITextEmbedding
                from agentscope.memory._reme import ReMeToolLongTermMemory

                # Initialize models
                model = OpenAIChatModel(
                    model_name="gpt-4",
                    api_key="your-api-key"
                )
                embedding = OpenAITextEmbedding(
                    model_name="text-embedding-3-small",
                    api_key="your-api-key"
                )

                # Create memory instance
                memory = ReMeToolLongTermMemory(
                    agent_name="my_agent",
                    user_name="user_123",
                    run_name="session_001",
                    model=model,
                    embedding_model=embedding
                )

                # Use with async context manager
                async with memory:
                    # Memory operations...
                    pass

        """
        super().__init__()

        # Store agent and workspace identifiers
        self.agent_name = agent_name
        # Maps to ReMe's workspace_id concept
        self.workspace_id = user_name
        self.run_name = run_name

        # Build configuration arguments for ReMeApp
        # These will be passed as command-line style config overrides
        config_args = []

        # Extract LLM API credentials based on model type
        # DashScope uses a fixed endpoint, OpenAI can have custom base_url
        if isinstance(model, DashScopeChatModel):
            llm_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            llm_api_key = model.api_key

        elif isinstance(model, OpenAIChatModel):
            llm_api_base = str(getattr(model.client, "base_url", None))
            llm_api_key = str(getattr(model.client, "api_key", None))

        else:
            raise ValueError(
                f"model must be a DashScopeChatModel or "
                f"OpenAIChatModel instance. "
                f"Got {type(model).__name__} instead.",
            )

        # Extract model name and add to config if provided
        llm_model_name = model.model_name

        if llm_model_name:
            config_args.append(f"llm.default.model_name={llm_model_name}")

        # Extract embedding model API credentials based on type
        # Similar to LLM, DashScope uses fixed endpoint,
        # OpenAI can be customized
        if isinstance(embedding_model, DashScopeTextEmbedding):
            embedding_api_base = (
                "https://dashscope.aliyuncs.com/compatible-mode/v1"
            )
            embedding_api_key = embedding_model.api_key

        elif isinstance(embedding_model, OpenAITextEmbedding):
            base_url = getattr(
                embedding_model.client,
                "base_url",
                None,
            )
            embedding_api_base = str(base_url) if base_url else None
            embedding_api_key = getattr(
                embedding_model.client,
                "api_key",
                None,
            )

        else:
            raise ValueError(
                "embedding_model must be a DashScopeTextEmbedding or "
                "OpenAITextEmbedding instance. "
                f"Got {type(embedding_model).__name__} instead.",
            )

        # Extract embedding model name and add to config if provided
        embedding_model_name = embedding_model.model_name

        if embedding_model_name:
            config_args.append(
                f"embedding_model.default.model_name={embedding_model_name}",
            )

        dimensions = embedding_model.dimensions
        config_args.append(
            f'embedding_model.default.params={{"dimensions": {dimensions}}}',
        )

        # Attempt to import and initialize ReMe
        # If import fails, set app to None and issue a warning
        # This allows the class to be instantiated even without
        # reme_ai installed
        try:
            from reme_ai import ReMeApp
        except ImportError as e:
            raise ImportError(
                "The 'reme_ai' library is required for ReMe-based "
                "long-term memory. Please install it by `pip install reme-ai`,"
                "and visit: https://github.com/modelscope/reMe for more "
                "information.",
            ) from e

        # Initialize ReMe with extracted configurations
        self.app = ReMeApp(
            *config_args,  # Config overrides as positional args
            llm_api_key=llm_api_key,
            llm_api_base=llm_api_base,
            embedding_api_key=embedding_api_key,
            embedding_api_base=embedding_api_base,
            # Optional custom config file
            config_path=reme_config_path,
            # Additional ReMe-specific configurations
            **kwargs,
        )

        # Track if the app context is active (started via __aenter__)
        self._app_started = False

    async def __aenter__(self) -> "ReMeLongTermMemoryBase":
        """Async context manager entry point.

        This method is called when entering an async context
        (using 'async with'). It initializes the ReMe app context if
        available, enabling memory operations within the context block.

        Returns:
            `ReMeLongTermMemoryBase`:
                The memory instance itself, allowing it to be used in
                the context.

        Example:
            .. code-block:: python

                memory = ReMeToolLongTermMemory(
                    agent_name="my_agent",
                    model=model,
                    embedding_model=embedding
                )

                async with memory:
                    # Memory operations can be performed here
                    await memory.record_to_memory(
                        thinking="Recording tool usage",
                        content=[...]
                    )

        """
        if self.app is not None:
            await self.app.__aenter__()
            self._app_started = True
        return self

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        """Async context manager exit point.

        This method is called when exiting an async context (at the end
        of 'async with' block or when an exception occurs). It properly
        cleans up the ReMe app context and resources.

        Args:
            exc_type (`Any`):
                The type of exception that occurred, if any. None if no
                exception.
            exc_val (`Any`):
                The exception instance that occurred, if any. None if no
                exception.
            exc_tb (`Any`):
                The traceback object for the exception, if any. None if
                no exception.

        .. note:: This method will gracefully handle the case where self.app
         is None (reme_ai not installed) by skipping the cleanup but still
         marking the app as stopped. It will also always set _app_started
         to False, ensuring the memory state is properly reset.

        Example:
            .. code-block:: python

                async with memory:
                    try:
                        # Memory operations
                        await memory.record_to_memory(...)
                    except Exception as e:
                        # __aexit__ will be called even if an exception occurs
                        print(f"Error: {e}")
                # __aexit__ has been called and resources are cleaned up

        """
        if self.app is not None:
            await self.app.__aexit__(exc_type, exc_val, exc_tb)
        self._app_started = False
