# -*- coding: utf-8 -*-
"""上传相关配置（对应 deer-flow sandbox_uploads_dir / 限制项）。

配置来源：config.yaml 的 ``uploads:`` 段（新增业务节点，需加入
``app_config._BUSINESS_KEYS`` 白名单，避免 fail-fast 拼写校验）。

路径策略：上传文件**直接放在 workspace 内**，真实路径为
``{workspace_dir}/{agent_id}/uploads/...``。agent 的通用文件工具
（read/grep/glob/bash）基于 workdir（即 ``{workspace_dir}/{agent_id}``）扫描时，
可直接看到用户上传的文件，避免「文件找不到 / 列目录找不到」的问题。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from bocomadp.config.base import BASE_DIR, expand_env_vars, load_config_yaml, resolve_path

VIRTUAL_PATH_PREFIX = "virtual://uploads"

# 上传目录相对每个 agent workdir 的子目录名
UPLOAD_SUBDIR = "uploads"


class UploadConfig(BaseModel):
    """上传限制与目录配置。"""

    enabled: bool = Field(default=True, description="是否开启文件上传能力。")
    base_dir: str | Path = Field(
        default="uploads",
        description=(
            "上传目录名（位于每个 agent 的 workspace workdir 之下）。"
            "最终真实路径为 {workspace_dir}/{agent_id}/{base_dir}/..."
        ),
    )
    max_file_size_mb: float = Field(
        default=50.0, description="单文件大小上限（MB）。"
    )
    max_files_per_session: int = Field(
        default=50, description="单个 session 最大文件数。"
    )
    streaming_threshold_mb: float = Field(
        default=10.0,
        description="超过此阈值走流式/分块上传（MB）。",
    )
    max_pdf_pages: int = Field(
        default=1000, description="PDF 页数上限，超出拒绝。"
    )
    # 文件名约束（移植 deer-flow security）
    max_filename_length: int = Field(default=255)

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)

    @property
    def streaming_threshold_bytes(self) -> int:
        return int(self.streaming_threshold_mb * 1024 * 1024)


def get_upload_config() -> UploadConfig:
    """从 config.yaml 的 uploads 段读取（缺失则用默认值）。

    注意：AppConfig 使用 extra="ignore"，uploads 不会进入 AppConfig 实例，
    因此这里直接从原始 yaml（含 $VAR 展开）读取该业务节点。
    """
    try:
        data = expand_env_vars(load_config_yaml()).get("uploads")
    except Exception:
        data = None
    if isinstance(data, dict):
        return UploadConfig(**data)
    return UploadConfig()


def get_workspace_dir() -> Path:
    """返回 workspace 根目录（与 AppConfig.workspace_dir 一致）。"""
    from bocomadp.config.app_config import get_app_config

    return Path(get_app_config().workspace_dir)


def get_agent_upload_root(agent_id: str) -> Path:
    """返回某 agent 的上传根目录：``{workspace_dir}/{agent_id}/uploads``。

    该目录位于 agent 的 workspace workdir 之下，agent 的通用文件工具
    （read/grep/glob/bash）基于 workdir 扫描时可直接看到上传文件。
    """
    root = get_workspace_dir() / agent_id / UPLOAD_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    return root
