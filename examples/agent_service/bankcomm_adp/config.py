# -*- coding: utf-8 -*-
"""扩展包配置。

所有配置项通过环境变量注入，前缀统一为 ``ADP_``，例如：:

    ADP_AUDIT_ENABLED=false ADP_DLP_ENABLED=false python main.py

（仅保留中间件 / 工具 / 健康检查所需的配置；已剔除 JWT 与组织架构相关项。
为避免给官方 ``agent_service`` 引入额外第三方依赖，这里刻意不使用
``pydantic-settings``，仅用标准库 ``os.environ`` 读取。）
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

# examples/agent_service/ 目录
BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool) -> bool:
    """读取布尔型环境变量。"""
    raw = _getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _getenv(name: str) -> str | None:
    """带 ``ADP_`` 前缀读取环境变量，并兼容从 ``.env`` 文件加载的值。"""
    import os

    full_name = f"ADP_{name}"
    val = os.environ.get(full_name)
    if val is not None:
        return val

    # 可选：若 agent_service/.env 存在，则按 KEY=VALUE 逐行加载（首次调用缓存）
    _load_dotenv_once()
    return os.environ.get(full_name)


@lru_cache
def _load_dotenv_once() -> None:
    """从 ``BASE_DIR/.env`` 加载配置（不覆盖已有环境变量）。"""
    import os

    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for raw_line in env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # 去除可选的引号
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


class Settings:
    """扩展包全局配置（基于环境变量）。"""

    @property
    def app_name(self) -> str:
        return _getenv("APP_NAME") or "交通银行智能体平台"

    # ---------- 管控 ----------
    @property
    def audit_enabled(self) -> bool:
        return _env_bool("AUDIT_ENABLED", True)

    @property
    def audit_log_path(self) -> Path:
        return Path(_getenv("AUDIT_LOG_PATH") or str(BASE_DIR / "logs" / "audit.jsonl"))

    @property
    def dlp_enabled(self) -> bool:
        return _env_bool("DLP_ENABLED", True)

    # ---------- 运行时目录 ----------
    @property
    def workspace_dir(self) -> Path:
        return Path(_getenv("WORKSPACE_DIR") or str(BASE_DIR / "workspaces"))

    # ---------- K8s 沙箱（通过 workspace 模块读取 ADP_K8S_* 变量） ----------
    @property
    def k8s_enabled(self) -> bool:
        """是否启用 K8s 沙箱模式。

        启用后，智能体的代码执行将在 K8s Pod 中进行，
        而非本地文件系统或 Docker。需要配合预构建镜像使用。
        """
        return _env_bool("K8S_ENABLED", True)


@lru_cache
def get_settings() -> Settings:
    """获取全局配置单例。"""
    return Settings()


settings = get_settings()
