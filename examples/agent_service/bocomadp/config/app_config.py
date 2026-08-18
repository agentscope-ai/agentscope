# -*- coding: utf-8 -*-
"""框架级配置 AppConfig（config.yaml 主源 + 环境变量部署期覆盖）。

> ``config.yaml`` 为唯一配置载体，``AppConfig`` 为唯一 schema：
>
> - ``config.yaml`` 中的全部节点（``log_level`` / ``logging`` / ``service`` /
>   ``redis`` / ``tools`` / ``middlewares`` / ``mcp`` /
>   ``providers`` / ``app_name`` / ``workspace_dir``）作为主配置源；
> - ``BOCOMADP_`` 前缀环境变量 / ``.env`` 文件可在部署期覆盖（优先级更高）。
>
> 读取优先级（高 → 低，由 pydantic-settings 保证）：
> 进程环境变量 > ``.env`` 文件 > ``config.yaml``（$VAR 展开后）> 代码默认值。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import difflib
import yaml
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import YamlConfigSettingsSource

from .base import (
    BASE_DIR,
    CONFIG_YAML_FILE,
    DOTENV_FILE,
    expand_env_vars,
    load_config_yaml,
    resolve_path,
)

# 顶层业务节点白名单：这些键**有意**不在 AppConfig schema 内，
# 由独立读取器消费（models → load_models_from_yaml；audit → AuditConfig；
# cross_search → get_cross_search_config）。
# 新增此类业务节点时，必须加入本集合，否则启动校验会 fail-fast。
_BUSINESS_KEYS: frozenset[str] = frozenset(
    {"models", "audit", "cross_search", "uploads","agents"},
)

# 拼写校验相似度阈值：YAML 键与声明字段的相似度达到该值即视为疑似拼写错误。
_SPELL_CHECK_CUTOFF = 0.7


def _reject_unknown_yaml_keys(data: dict[str, Any]) -> None:
    """递归校验 YAML 键与 schema 字段匹配，发现疑似拼写错误时抛错。

    - 顶层：白名单（``_BUSINESS_KEYS``）之外的未知键，用 ``difflib`` 与声明
      字段做模糊匹配，高度相似视为拼写错误（fail-fast，而非静默用默认值）；
    - 嵌套：对声明为 ``BaseModel`` 子类的字段递归校验其子键。
    """

    def _walk(data: dict[str, Any], fields: dict, path: str = "") -> None:
        for key in data:
            if key in fields:
                # 递归下钻嵌套模型
                annotation = fields[key].annotation
                if (
                    isinstance(data[key], dict)
                    and isinstance(annotation, type)
                    and issubclass(annotation, BaseModel)
                    and getattr(annotation, "model_fields", None)
                ):
                    _walk(data[key], annotation.model_fields, f"{path}{key}.")
                continue
            if not path and key in _BUSINESS_KEYS:
                continue
            match = difflib.get_close_matches(
                str(key),
                [f for f in fields if f not in _BUSINESS_KEYS],
                n=1,
                cutoff=_SPELL_CHECK_CUTOFF,
            )
            hint = f"，是否应为 {match[0]!r}？" if match else ""
            raise ValueError(
                f"config.yaml 中键 {path + str(key)!r} 未在 "
                f"AppConfig schema 中声明{hint}；"
                "请检查拼写；若为有意新增的业务节点，"
                "请将其加入 bocomadp.config.app_config._BUSINESS_KEYS 白名单。",
            )

    _walk(data, AppConfig.model_fields)


def get_app_config() -> "AppConfig":
    """加载最新应用配置：每次调用重建 ``AppConfig``（config.yaml 实时重读 = 热加载）。

    适合**运行时**按需获取最新配置（如每次请求 / 每次 agent 组装）；
    启动装配（注册表 / Redis / 中间件）用一次快照即可，保持装配一致性。
    """
    return AppConfig()


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
    """Redis backend for AgentScope storage / message bus.

    ``main.py`` 中 ``RedisStorage`` 应通过 ``config.redis.host`` /
    ``config.redis.port`` 读取，禁止绕过本配置直接 ``os.getenv``。
    """

    host: str = Field(default="localhost")
    port: int = Field(default=6379)


class RunConcurrencyConfig(BaseModel):
    """/chat 并发控制上限(仿 deer-flow run_concurrency)。

    ``0`` 表示不限;``grace_secs`` 为装配窗口宽限秒数;``enabled=false``
    时中间件完全透传,跳过对账/占位/注册。
    """

    enabled: bool = Field(
        default=True,
        description="并发控制总开关;false 时中间件完全透传,跳过对账/占位/注册。",
    )
    max_running: int = Field(
        default=10,
        ge=0,
        description="全局并发上限(0=不限)。",
    )
    max_running_per_user: int = Field(
        default=3,
        ge=0,
        description="每用户并发上限(0=不限)。",
    )
    grace_secs: float = Field(
        default=6.0,
        gt=0,
        description="装配窗口宽限秒数(响应先于框架锁 key 创建的时间窗口)。",
    )


class DbConfig(BaseModel):
    """AgentScope 持久化存储后端（AsyncSQLAlchemyStorage）。

    ``main.py`` 中 ``AsyncSQLAlchemyStorage`` 通过 ``config.db.url`` 读取，
    禁止绕过本配置直接 ``os.getenv``。支持任意 SQLAlchemy async URL：
    ``postgresql+asyncpg://`` / ``sqlite+aiosqlite://`` / ``mysql+aiomysql://``。
    """

    url: str = Field(
        default=(
            "postgresql+asyncpg://agentscope:agentscope"
            "@localhost:5432/agentscope"
        ),
        description="SQLAlchemy async URL；AgentScope StorageBase 的关系库后端。",
    )
    create_tables: bool = Field(
        default=True,
        description=(
            "启动时 Base.metadata.create_all 自动建表，dev/单机够用；"
            "多副本生产应关闭，改用离线 alembic upgrade head 避免迁移竞争。"
        ),
    )
    pool_pre_ping: bool = Field(
        default=True,
        description=(
            "取出连接前先探测存活（asyncpg ping），"
            "服务端/网络静默断连时自动重建，避免 connection is closed。"
        ),
    )
    pool_recycle: int = Field(
        default=1800,
        description=(
            "空闲连接回收秒数（默认 30 分钟），"
            "早于防火墙/NAT 空闲超时主动重建连接。"
        ),
    )


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
    supports_thinking: bool = Field(
        default=False,
        description="是否支持 thinking 模式（deer-flow 前端据此展示开关）",
    )
    parameters: dict[str, Any] = Field(
        default_factory=dict,
        description="透传给 ChatModel.Parameters 的额外参数",
    )


class AgentEntry(BaseModel):
    """config.yaml 中单个场景（Agent 模板）条目。

    启动时由 ``load_agents_from_yaml`` 读取并在 lifespan 中幂等同步进
    框架 StorageBase；chat / deerflow 按 ``agent_id`` 从 storage 解析
    场景配置。
    """

    agent_id: str = Field(description="场景唯一标识，即 /api/chat/run 的 agent_id")
    name: str = Field(default="", description="场景显示名")
    system_prompt: str = Field(
        default="You are a helpful AI assistant.",
        description="场景系统提示词（Agent 的 system_prompt）",
    )
    model_provider: str = Field(
        default="",
        description="场景绑定的模型 provider_id；留空使用全局 active provider",
    )
    model_name: str = Field(
        default="",
        description="场景绑定的模型名（展示 / 校验用）",
    )
    max_iters: int = Field(default=20, description="ReAct 最大迭代次数")
    enabled_tools: list[str] = Field(
        default_factory=list,
        description="场景启用的工具白名单（空 = 全部工具）",
    )
    enabled_skills: list[str] = Field(
        default_factory=list,
        description="场景启用的技能白名单（空 = 全部技能）",
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
    config_file: str | Path | None = Field(
        default=None,
        description="模型 Provider 配置文件路径；None 时使用 agent_service/config.yaml。",
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
# Tools / middleware config (new framework modules)
# ---------------------------------------------------------------------------


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


class _ExpandedYamlSource(YamlConfigSettingsSource):
    """config.yaml 配置源：读取后先做 ``$VAR`` / ``${VAR}`` 环境变量展开，
    再做键拼写校验（fail-fast）。

    继承 ``YamlConfigSettingsSource``（其 ``get_field_value`` 支持嵌套模型
    按字段名递归填充），仅在解析前多一步环境变量展开，保证与
    ``load_config_yaml`` / ``expand_env_vars`` 的行为一致。
    键校验与读取同源（仅一次文件读取，无 lru_cache），修改文件内容后
    校验实时生效，与热加载语义一致。
    """

    def _read_file(self, file_path: Path) -> dict[str, Any]:
        data = super()._read_file(file_path)
        data = expand_env_vars(data) if data else data
        # 对必填项开启校验：拼写错误（如 redis.hots）不再静默忽略
        # （extra="ignore" 仅用于容忍业务节点），fail-fast 暴露问题。
        if data:
            _reject_unknown_yaml_keys(data)
        return data


class AppConfig(BaseSettings):
    """应用配置：config.yaml 为主源，BOCOMADP_* 环境变量部署期覆盖。

    优先级（高 → 低）：

    1. 进程环境变量（``BOCOMADP_`` 前缀）
    2. ``agent_service/.env`` 中的 ``BOCOMADP_*`` 键
    3. ``agent_service/config.yaml``（``$VAR`` 展开后）—— 主配置源
    4. 代码默认值

    嵌套字段用 ``__`` 分隔，例如::

        BOCOMADP_LOG_LEVEL=debug
        BOCOMADP_LOGGING__ENHANCE__ENABLED=true
        BOCOMADP_SERVICE__PORT=9000
        BOCOMADP_REDIS__HOST=redis.local
    """

    model_config = SettingsConfigDict(
        env_prefix="BOCOMADP_",
        env_file=DOTENV_FILE,
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """把 ``config.yaml``（含 $VAR 展开）作为主源插入优先级链。

        source 顺序即优先级：init > 进程环境变量 > .env > config.yaml > secrets。
        """
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _ExpandedYamlSource(settings_cls, yaml_file=CONFIG_YAML_FILE),
            file_secret_settings,
        )

    # ---- 全局（config.yaml 根节点） ----
    app_name: str = Field(default="交通银行智能体平台")
    workspace_dir: Path = Field(
        default_factory=lambda: BASE_DIR / "workspaces",
        description="工作区目录（AgentScope 沙箱文件读写根目录），相对路径基于 BASE_DIR 归一化。",
    )

    @field_validator("workspace_dir", mode="before")
    @classmethod
    def _normalize_workspace_dir(cls, v: Any) -> Path:
        """将 workspace_dir 归一化为绝对路径（相对路径基于 BASE_DIR 解析）。

        与启动工作目录无关：无论从哪个目录启动，路径都一致指向
        ``agent_service/`` 下的目标目录。
        """
        if v is None or v == "":
            return BASE_DIR / "workspaces"
        return resolve_path(v)

    # ---- core ----
    log_level: str = Field(default="info")
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    service: ServiceConfig = Field(default_factory=ServiceConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    db: DbConfig = Field(default_factory=DbConfig)
    run_concurrency: RunConcurrencyConfig = Field(
        default_factory=RunConcurrencyConfig,
        description="/chat 并发控制配置。",
    )

    # ---- QwenPaw migration placeholders (all default off) ----
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    governance: GovernanceConfig = Field(default_factory=GovernanceConfig)
    hooks: HooksConfig = Field(default_factory=HooksConfig)
    checkpoints: CheckpointsConfig = Field(default_factory=CheckpointsConfig)
    token_usage: TokenUsageConfig = Field(default_factory=TokenUsageConfig)
    local_models: LocalModelsConfig = Field(default_factory=LocalModelsConfig)

    # ---- New framework modules ----
    tools: ToolsConfig = Field(default_factory=ToolsConfig)
    middlewares: MiddlewaresConfig = Field(default_factory=MiddlewaresConfig)
    mcp: McpConfig = Field(default_factory=McpConfig)


def load_models_from_yaml(
    path: str | Path | None = None,
) -> list[ModelEntry]:
    """从 YAML 文件加载模型 Provider 列表。

    默认读取 ``agent_service/config.yaml``（绝对路径，与启动工作目录无关），
    读取后先做 ``$VAR`` / ``${VAR}`` 环境变量展开。文件不存在时返回空列表
    （不影响启动）。
    """
    if path:
        p = Path(path)
        if not p.exists():
            return []
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        data = expand_env_vars(raw) if isinstance(raw, dict) else {}
    else:
        data = expand_env_vars(load_config_yaml())
    entries_data = data.get("models", [])
    result: list[ModelEntry] = []
    for item in entries_data:
        result.append(ModelEntry(**item))
    return result


def load_agents_from_yaml(
    path: str | Path | None = None,
) -> list[AgentEntry]:
    """从 YAML 文件加载场景（Agent 模板）列表。

    与 ``load_models_from_yaml`` 同构：默认读取 ``agent_service/config.yaml``
    （绝对路径，与启动工作目录无关），读取后先做 ``$VAR`` / ``${VAR}``
    环境变量展开。文件不存在时返回空列表（不影响启动）。
    """
    if path:
        p = Path(path)
        if not p.exists():
            return []
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        data = expand_env_vars(raw) if isinstance(raw, dict) else {}
    else:
        data = expand_env_vars(load_config_yaml())
    entries_data = data.get("agents", [])
    result: list[AgentEntry] = []
    for item in entries_data:
        result.append(AgentEntry(**item))
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
    # 仅当 credential 类有 model 字段时才传入（如 ELLMCredential 必填）
    if "model" in credential_cls.model_fields:
        credential_kwargs["model"] = entry.model_name or entry.provider_id
    credential = credential_cls(**credential_kwargs)

    model_cls = credential_cls.get_chat_model_class()
    # AgentScope 的 ChatModel 期望 Parameters（pydantic 模型）而非 dict；
    # 将 config.yaml 的 parameters 字典转换为模型类对应的 Parameters 对象，
    # 否则调用阶段会报 'dict' object has no attribute 'max_tokens'。
    parameters = entry.parameters or None
    if parameters is not None:
        parameters = model_cls.Parameters(**parameters)
    model = model_cls(
        credential=credential,
        model=entry.model_name or entry.provider_id,
        parameters=parameters,
    )
    return model


def is_trace_correlation_enabled(config: AppConfig) -> bool:
    """Single source of truth for the trace-correlation gate.

    Used by both the TraceMiddleware (ASGI) and ``configure_logging`` so
    they cannot drift on when ``trace_id`` is bound.
    """
    return bool(
        getattr(config.logging, "enhance", None)
        and config.logging.enhance.enabled
    )
