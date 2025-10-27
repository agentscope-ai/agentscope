# -*- coding: utf-8 -*-
"""Base long-term memory implementation using ReMe library.

This module provides a base class for long-term memory implementations
that integrate with the ReMe library.
"""
from typing import Any
from typing import TYPE_CHECKING

from .._long_term_memory_base import LongTermMemoryBase
from ...embedding import (
    EmbeddingModelBase,
    DashScopeTextEmbedding,
    OpenAITextEmbedding,
)
from ...model import (
    ChatModelBase,
    DashScopeChatModel,
    OpenAIChatModel,
)

if TYPE_CHECKING:
    from reme_ai import ReMeApp


class ReMeBaseLongTermMemory(LongTermMemoryBase):
    """Base class for ReMe-based long-term memory implementations."""

    def __init__(
            self,
            agent_name: str | None = None,
            user_name: str | None = None,
            run_name: str | None = None,
            model: ChatModelBase | None = None,
            embedding_model: EmbeddingModelBase | None = None,
            reme_config_path: str | None = None,
            **kwargs: Any):
        super().__init__()

        self.agent_name = agent_name
        self.workspace_id = user_name
        self.run_name = run_name

        # Build configuration arguments for ReMeApp
        config_args = []

        if isinstance(model, DashScopeChatModel):
            llm_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            llm_api_key = model.api_key

        elif isinstance(model, OpenAIChatModel):
            llm_api_base = str(getattr(model.client, "base_url", None))
            llm_api_key = str(getattr(model.client, "api_key", None))

        else:
            raise ValueError(f"model must be a DashScopeChatModel or OpenAIChatModel instance. "
                             f"Got {type(model).__name__} instead.")

        # Extract model parameters
        llm_model_name = model.model_name

        if llm_model_name:
            config_args.append(f"llm.default.model_name={llm_model_name}")

        # Validate embedding model type and extract parameters
        if isinstance(embedding_model, DashScopeTextEmbedding):
            embedding_api_base = "https://dashscope.aliyuncs.com/compatible-mode/v1"
            embedding_api_key = embedding_model.api_key

        elif isinstance(embedding_model, OpenAITextEmbedding):
            embedding_api_base = getattr(embedding_model.client, "base_url", None)
            embedding_api_key = getattr(embedding_model.client, "api_key", None)

        else:
            raise ValueError("embedding_model must be a DashScopeTextEmbedding or OpenAITextEmbedding "
                             f"instance. Got {type(embedding_model).__name__} instead.")

        # Extract embedding model parameters
        embedding_model_name = embedding_model.model_name

        if embedding_model_name:
            config_args.append(f"embedding_model.default.model_name={embedding_model_name}")

        from reme_ai import ReMeApp
        self.app = ReMeApp(*config_args,
                           llm_api_key=llm_api_key,
                           llm_api_base=llm_api_base,
                           embedding_api_key=embedding_api_key,
                           embedding_api_base=embedding_api_base,
                           config_path=reme_config_path,
                           **kwargs)

        # Track if the app context is active
        self._app_started = False

    async def __aenter__(self) -> "ReMeBaseLongTermMemory":
        """Async context manager entry."""
        await self.app.__aenter__()
        self._app_started = True
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Async context manager exit."""
        await self.app.__aexit__(exc_type, exc_val, exc_tb)
        self._app_started = False
