# -*- coding: utf-8 -*-
"""Configuration class for UniversalAgent."""

from typing import Dict, List, Optional, Any, Literal
from pydantic import BaseModel, Field
import os


class ModelConfig(BaseModel):
    """Model configuration."""
    name: str
    model_type: Literal["openai", "anthropic", "gemini", "dashscope", "ollama"]
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    model_name: str
    capabilities: List[str] = Field(default_factory=lambda: ["text"])
    max_tokens: Optional[int] = None
    temperature: float = 0.7
    stream: bool = True


class MemoryConfig(BaseModel):
    """Memory configuration."""
    memory_type: Literal["in_memory", "redis", "sqlite", "hybrid"] = "in_memory"
    redis_url: Optional[str] = None
    sqlite_path: Optional[str] = None
    enable_long_term_memory: bool = False
    long_term_memory_type: Optional[Literal["mem0", "reme"]] = None
    compression_enabled: bool = True
    max_memory_size: int = 1000


class ToolConfig(BaseModel):
    """Tool configuration."""
    enable_coding_tools: bool = True
    enable_file_tools: bool = True
    enable_multimodal_tools: bool = True
    enable_mcp_tools: bool = False
    mcp_clients: List[Dict[str, Any]] = Field(default_factory=list)
    custom_tools: List[Dict[str, Any]] = Field(default_factory=list)
    tool_groups: Dict[str, List[str]] = Field(default_factory=dict)


class RAGConfig(BaseModel):
    """RAG configuration."""
    enable_rag: bool = False
    knowledge_bases: List[Dict[str, Any]] = Field(default_factory=list)
    embedding_model: str = "openai"
    vector_store: Literal["qdrant", "milvus", "mysql"] = "qdrant"
    vector_store_config: Dict[str, Any] = Field(default_factory=dict)
    readers: List[str] = Field(default_factory=lambda: ["text", "pdf"])


class PlanConfig(BaseModel):
    """Plan configuration."""
    enable_planning: bool = False
    max_subtasks: int = 10
    plan_storage_type: Literal["in_memory", "sqlite"] = "in_memory"
    auto_planning: bool = True
    manual_planning: bool = True


class TTSConfig(BaseModel):
    """TTS configuration."""
    enable_tts: bool = False
    model: Literal["openai", "dashscope", "gemini"] = "openai"
    voice: Optional[str] = None
    auto_play: bool = False
    audio_format: str = "mp3"


class TracingConfig(BaseModel):
    """Tracing configuration."""
    enable_tracing: bool = False
    tracing_url: Optional[str] = None
    studio_url: Optional[str] = None
    log_level: str = "INFO"


class UniversalAgentConfig(BaseModel):
    """UniversalAgent configuration class.
    
    This configuration defines all the capabilities and settings for the UniversalAgent.
    """
    
    # Basic settings
    name: str = "UniversalAgent"
    sys_prompt: str = (
        "You are UniversalAgent, a comprehensive AI assistant capable of handling "
        "various tasks including coding, file operations, research, planning, and "
        "multimodal interactions. You have access to multiple models, tools, "
        "memory systems, and knowledge bases to provide the best possible assistance."
    )
    
    # Model configuration
    models: List[ModelConfig] = Field(default_factory=list)
    default_model: str = "openai"
    model_fallback_order: List[str] = Field(default_factory=lambda: ["openai", "anthropic", "gemini"])
    
    # Tool configuration
    tools: ToolConfig = Field(default_factory=ToolConfig)
    
    # Memory configuration
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    
    # RAG configuration
    rag: RAGConfig = Field(default_factory=RAGConfig)
    
    # Plan configuration
    plan: PlanConfig = Field(default_factory=PlanConfig)
    
    # TTS configuration
    tts: TTSConfig = Field(default_factory=TTSConfig)
    
    # Tracing configuration
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    
    # Advanced settings
    max_iterations: int = 20
    enable_parallel_tool_calls: bool = True
    enable_structured_output: bool = True
    response_timeout: int = 120
    retry_attempts: int = 3
    
    def __init__(self, **data):
        """Initialize config with defaults based on environment variables."""
        # Set defaults from environment if available
        if "OPENAI_API_KEY" in os.environ:
            if not any(m.name == "openai" for m in data.get("models", [])):
                data.setdefault("models", []).append(ModelConfig(
                    name="openai",
                    model_type="openai",
                    api_key=os.environ.get("OPENAI_API_KEY"),
                    model_name="gpt-4",
                    capabilities=["text", "vision", "tools"]
                ))
        
        if "ANTHROPIC_API_KEY" in os.environ:
            if not any(m.name == "anthropic" for m in data.get("models", [])):
                data.setdefault("models", []).append(ModelConfig(
                    name="anthropic",
                    model_type="anthropic",
                    api_key=os.environ.get("ANTHROPIC_API_KEY"),
                    model_name="claude-3-5-sonnet-20241022",
                    capabilities=["text", "vision", "tools"]
                ))
        
        if "DASHSCOPE_API_KEY" in os.environ:
            if not any(m.name == "dashscope" for m in data.get("models", [])):
                data.setdefault("models", []).append(ModelConfig(
                    name="dashscope",
                    model_type="dashscope",
                    api_key=os.environ.get("DASHSCOPE_API_KEY"),
                    model_name="qwen-max",
                    capabilities=["text", "vision", "tools", "tts"]
                ))
        
        super().__init__(**data)
    
    def get_model_config(self, name: str) -> Optional[ModelConfig]:
        """Get model configuration by name."""
        for model_config in self.models:
            if model_config.name == name:
                return model_config
        return None
    
    def has_capability(self, capability: str) -> bool:
        """Check if any configured model has the specified capability."""
        for model_config in self.models:
            if capability in model_config.capabilities:
                return True
        return False