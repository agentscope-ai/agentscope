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

import os
import re
from pathlib import Path
from typing import Any, Literal

import yaml
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


class ModelEntry(BaseModel):
    """config.yaml 中单个模型 Provider 条目。

    启动时由 ``load_models_from_yaml`` 读取，通过 ``CredentialFactory``
    动态实例化 credential + model，注册到 ``ProviderManager``。
    """

    provider_id: str = Field(description="唯一标识，如 deepseek")
    display_name: str = Field(default="", description="前端显示名")
    provider_type: str = Field(
        default="deepseek",
        description=(
            "凭证类型，对应 CredentialFactory 中的 type 前缀："
            "deepseek / openai / anthropic / dashscope / gemini "
            "/ ollama / moonshot / xai"
        ),
    )
    model_name: str = Field(default="", description="模型名，如 deepseek-chat")
    api_key: str = Field(default="", description="API Key，支持 ${ENV_VAR} 语法")
    base_url: str = Field(default="", description="API base URL，留空用默认")
    is_active: bool = Field(default=False, description="是否设为活跃模型")
    supports_multimodal: bool = Field(default=False, description="是否支持多模态")
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="透传给 ChatModel.Parameters 的额外参数",
    )


class ProviderConfig(BaseModel):
    """模型 Provider 路由配置。

    ``config_file`` 指向 YAML 文件，启动时自动加载并注册到
    ``ProviderManager``。文件不存在时跳过（不影响启动）。
    """

    enabled: bool = Field(
        default=True,
        description="启动时从 config.yaml 加载并注册模型。",
    )
    config_file: str = Field(
        default="config.yaml",
        description="模型 Provider 配置文件路径（相对于工作目录）。",
    )
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


def _resolve_env(value: str) -> str:
    """将 ${ENV_VAR} 占位符替换为实际环境变量值。

    支持两种写法：
    - ``${DEEPSEEK_API_KEY}`` → ``os.environ["DEEPSEEK_API_KEY"]``
    - ``sk-xxx``              → 原样返回
    """
    pattern = re.compile(r"\$\{(\w+)\}")
    return pattern.sub(lambda m: os.environ.get(m.group(1), ""), value)


def load_models_from_yaml(
    path: str = "config.yaml",
) -> list[ModelEntry]:
    """从 YAML 文件加载模型 Provider 列表。

    文件不存在时返回空列表（不影响启动）。``api_key`` 中的
    ``${ENV_VAR}`` 占位符会被替换为实际环境变量值。
    """
    p = Path(path)
    if not p.exists():
        return []
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    entries_data = raw.get("models", [])
    result: list[ModelEntry] = []
    for item in entries_data:
        entry = ModelEntry(**item)
        entry.api_key = _resolve_env(entry.api_key)
        if entry.base_url:
            entry.base_url = _resolve_env(entry.base_url)
        result.append(entry)
    return result


def build_model_instance(entry: ModelEntry):
    """根据 ModelEntry 动态创建 ChatModel 实例。

    利用 ``CredentialFactory`` 按 ``provider_type`` 查找 credential
    类，实例化后通过 ``get_chat_model_class()`` 获取对应的
    ``ChatModelBase`` 子类并构造模型。
    """
    from agentscope.credential import CredentialFactory

    # 先用简写匹配（如 deepseek），失败则补 _credential 后缀再试
    credential_cls = CredentialFactory.get_credential_class(
        entry.provider_type,
    )
    if credential_cls is None and not entry.provider_type.endswith("_credential"):
        credential_cls = CredentialFactory.get_credential_class(
            f"{entry.provider_type}_credential",
        )
    if credential_cls is None:
        raise ValueError(
            f"Unknown provider_type: {entry.provider_type!r} "
            f"(provider_id={entry.provider_id})",
        )

    credential_kwargs: dict[str, Any] = {"api_key": entry.api_key}
    # 仅当 credential 类有 base_url 字段时才传入
    if entry.base_url and "base_url" in credential_cls.model_fields:
        credential_kwargs["base_url"] = entry.base_url
    credential = credential_cls(**credential_kwargs)

    model_cls = credential_cls.get_chat_model_class()
    model = model_cls(
        credential=credential,
        model=entry.model_name or entry.provider_id,
        parameters=entry.parameters or None,
    )
    return model


def load_config() -> AppConfig:
    """Load the application config from env / .env file."""
    return AppConfig()


def is_trace_correlation_enabled(config: AppConfig) -> bool:
    """Single source of truth for the trace-correlation gate.

    Used by both the TraceMiddleware (ASGI) and ``configure_logging`` so
    they cannot drift on when ``trace_id`` is bound.
    """
    return bool(getattr(config.logging, "enhance", None) and config.logging.enhance.enabled)
