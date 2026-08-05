"""Application configuration.

Uses ``pydantic-settings`` to read from env vars (and ``.env`` if present).
All settings are grouped into nested models so that each concern has its
own section — this makes it easy to extend as you port modules in.

## Reserved sections for future QwenPaw module migration

The classes marked ``# PORT-FROM-QWENPAW`` below are placeholders for
modules you may migrate later. They are intentionally minimal (often just
an ``enabled`` flag) so that wiring them into ``main.py`` is a one-line
change once the module is ported in.

- :class:`ProviderConfig`        ← ``qwenpaw/providers/``
- :class:`GovernanceConfig`      ← ``qwenpaw/governance/``
- :class:`HooksConfig`           ← ``qwenpaw/hooks/``
- :class:`CheckpointsConfig`     ← ``qwenpaw/checkpoints/``
- :class:`TokenUsageConfig`      ← ``qwenpaw/token_usage/``
- :class:`LocalModelsConfig`     ← ``qwenpaw/local_models/``
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class LoggingEnhanceConfig(BaseModel):
    """Request trace logging enhancement — drives :func:`configure_logging`."""

    enabled: bool = Field(
        default=True,
        description="Install TraceContextFilter + trace formatter on root handlers.",
    )
    format: Literal["text", "json"] = Field(
        default="text",
        description="Enhanced log output format. JSON is recommended for prod.",
    )


class LoggingConfig(BaseModel):
    """Logging configuration."""

    enhance: LoggingEnhanceConfig = Field(default_factory=LoggingEnhanceConfig)


class ServiceConfig(BaseModel):
    """HTTP server settings."""

    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    reload: bool = Field(default=False, description="Enable uvicorn auto-reload (dev only).")


class RedisConfig(BaseModel):
    """Redis backend for AgentScope storage / message bus."""

    host: str = Field(default="localhost")
    port: int = Field(default=6379)


# ---------------------------------------------------------------------------
# PORT-FROM-QWENPAW placeholders
# ---------------------------------------------------------------------------
# Each is a minimal stub. Flip ``enabled`` to True and wire the module into
# main.py once you've migrated it from QwenPaw. Keep them disabled by default
# so the skeleton stays runnable without the dependency.


class ProviderConfig(BaseModel):
    """PORT-FROM-QWENPAW: ``qwenpaw/providers/``.

    Multi-provider routing (openai / dashscope / anthropic / gemini / ollama /
    openrouter). Migrate individual provider files as needed; each is a
    standalone ``ChatModelBase`` subclass with minimal cross-deps.
    """

    enabled: bool = False
    manager_class: str = Field(
        default="bocomadp.providers.ProviderManager",
        description="Dotted path to the provider manager (set after migration).",
    )


class GovernanceConfig(BaseModel):
    """PORT-FROM-QWENPAW: ``qwenpaw/governance/``.

    Agent-level governance (doom-loop gates, budget gates, rubric gates).
    Heavy (~5k lines). Only migrate if you need agent-loop safety rails.
    """

    enabled: bool = False


class HooksConfig(BaseModel):
    """PORT-FROM-QWENPAW: ``qwenpaw/hooks/``.

    Runtime-level hooks (error_hook, etc.). Migrate per-hook; each hook is
    a small module implementing the agentscope Hook protocol.
    """

    enabled: bool = False


class CheckpointsConfig(BaseModel):
    """PORT-FROM-QWENPAW: ``qwenpaw/checkpoints/``.

    Conversation checkpointing / branching. Migrate if you need session
    history replay and branching UI.
    """

    enabled: bool = False
    storage_dir: str = Field(default="./data/checkpoints")


class TokenUsageConfig(BaseModel):
    """PORT-FROM-QWENPAW: ``qwenpaw/token_usage/``.

    Per-turn / per-session token accounting. Lightweight (~1k lines) and
    self-contained — a good early migration candidate.
    """

    enabled: bool = False


class LocalModelsConfig(BaseModel):
    """PORT-FROM-QWENPAW: ``qwenpaw/local_models/``.

    Local model lifecycle (Ollama-style). Migrate only if you serve local
    models from this service.
    """

    enabled: bool = False


# ---------------------------------------------------------------------------
# Runtime / tools / middleware config (new framework modules)
# ---------------------------------------------------------------------------


class RuntimeConfig(BaseModel):
    """Configuration for the 8-phase runtime orchestrator.

    Controls the SSE heartbeat interval and whether the runtime
    SSE endpoint is mounted (vs. relying on AgentScope's built-in
    fire-and-forget chat).
    """

    enabled: bool = Field(
        default=True,
        description="Mount the /api/chat/run SSE endpoint.",
    )
    heartbeat_interval_seconds: float = Field(
        default=15.0,
        description="SSE keep-alive interval for long-idle periods.",
    )


class ToolsConfig(BaseModel):
    """Configuration for the custom tool registry."""

    enabled: bool = Field(
        default=True,
        description="Load built-in custom tools into every agent.",
    )
    load_custom: bool = Field(
        default=True,
        description="自动扫描 tools/custom/ 下的 @tool 函数。",
    )


class MiddlewaresConfig(BaseModel):
    """配置 agent 级中间件注册表。"""

    enabled: bool = Field(
        default=True,
        description="加载 agent_middleware.py 中的内置中间件。",
    )
    load_custom: bool = Field(
        default=True,
        description="自动扫描 middleware/custom/ 下的 Middleware 实例。",
    )


class McpConfig(BaseModel):
    """配置 MCP 注册表。"""

    enabled: bool = Field(
        default=True,
        description="加载 builtin_mcps.py 中的 MCPClient 实例。",
    )
    load_custom: bool = Field(
        default=True,
        description="自动扫描 mcp/custom/ 下的 MCPClient 实例。",
    )


class AppConfig(BaseSettings):
    """Root application config.

    Reads from env vars with prefix ``BOCOMADP_``.  Nested fields use
    ``__`` as the delimiter, e.g.::

        BOCOMADP_LOG_LEVEL=debug
        BOCOMADP_LOGGING__ENHANCE__ENABLED=true
        BOCOMADP_SERVICE__PORT=9000
        BOCOMADP_REDIS__HOST=redis.local

    Drop a ``.env`` file next to ``main.py`` for local dev.
    """

    model_config = SettingsConfigDict(
        env_prefix="BOCOMADP_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ---- core ----
    log_level: str = Field(default="info")
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)

    # ---- QwenPaw migration placeholders (all default off) ----
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    checkpoints: CheckpointsConfig = Field(default_factory=CheckpointsConfig)
    token_usage: TokenUsageConfig = Field(default_factory=TokenUsageConfig)
    local_models: LocalModelsConfig = Field(default_factory=LocalModelsConfig)

    # ---- New framework modules ----
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    middlewares: MiddlewaresConfig = Field(default_factory=MiddlewaresConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)


def load_config() -> AppConfig:
    """Load the application config from env / .env file."""
    return AppConfig()


def is_trace_correlation_enabled(config: AppConfig) -> bool:
    """Single source of truth for the trace-correlation gate.

    Used by both the TraceMiddleware (ASGI) and ``configure_logging`` so
    they cannot drift on when ``trace_id`` is bound.
    """
    return bool(getattr(config.logging, "enhance", None) and config.logging.enhance.enabled)
