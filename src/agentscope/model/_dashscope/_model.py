# -*- coding: utf-8 -*-
""""""
from pydantic import BaseModel, SecretStr, Field

from .._model_schema import ModelSchema


class DashScopeProvider(BaseModel):
    """The provider for DashScope."""
    api_key: SecretStr = Field(
        title="API Key",
        description="The DashScope API key",
    )
    base_api_url: str | None = Field(default=None, title="Base API URL", description="The base API URL")

class DashScopeLLMParameter(BaseModel):
    """The LLM parameter for DashScope."""
    stream: bool = Field(default=True, title="Stream mode")
    max_retries: int = Field(default=3, title="Max retries", description="The maximum number of retries on failure")
    # Thinking related parameters
    enable_thinking: bool = Field(default=True, title="Enable Thinking")
    preserve_thinking: bool = Field(default=False, title="Preserve Thinking")
    thinking_budget: int | None = Field(default=None, title="Thinking budget", description="The maximum token budget for the thinking process")

    temperature: float | None = Field(default=None, title="Temperature", description="The temperature for response generation", ge=0.0, lt=2.0)
    topK: int | None = Field(default=None)
    max_tokens: int | None = Field(default=None)

class DashScopeChatModel(BaseModel):
    """The chat model for DashScope."""
    provider: DashScopeProvider
    """The provider of the dashscope model."""
    model: str = Field(title="Model Name", description="The name of the model to use")
    parameters: DashScopeLLMParameter

    @classmethod
    def list_models(cls) -> list[ModelSchema]:
        """List available models from DashScope API, as well as their
        capabilities and supported parameters."""
        return []

model = DashScopeChatModel(
    provider=DashScopeProvider(
        api_key=SecretStr("your_api_key_here"),
    ),
    model="qwen3.6-plus",
    parameters=DashScopeLLMParameter(
        thinking_budget=None,
        temperature=0.1,
    )
)

