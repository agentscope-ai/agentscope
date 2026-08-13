# -*- coding: utf-8 -*-
"""上传相关配置（对应 deer-flow sandbox_uploads_dir / 限制项）。

方案 A：上传路径协议与下载同源

==============================

上传文件与文件下载（``routers/workspace_files.py``）采用**完全一致的路径范式**，
即 workdir 相对路径、并以 ``/workspace`` 为虚拟前缀：

- 上传目录位于会话工作区内部，相对路径为
  ``{workdir}/user-data/uploads/...``；
- 虚拟路径 = ``/workspace/user-data/uploads/{stored_name}``，**不再编码**
  ``agent_id/user_id/session_id``（这些由 workdir 本身隔离）；
- ``workdir`` 由 ``workspace_manager.get_workspace()`` 解析——双 PVC 模式下
  是 session 级 PVC（RWO，session 间物理隔离），共享 PVC 模式下是
  ``/workspace/sessions/{session_id}``（子目录隔离）；
- 所有落盘 / 读取都走 ``workspace.get_backend()``（在沙箱内执行），
  **绝不在宿主机直接 ``Path.mkdir`` / ``os.open``**（沙箱 PVC 在宿主机不可见）；
  本地模式（``ADP_K8S_ENABLED=false``）同样走 ``LocalWorkspaceManager`` +
  backend，落盘到宿主机 workdir，session 隔离由 workdir 保证，无 host 特判。

因此「不同 session 上传文件隔离」由沙箱布局天然保证，无需额外逻辑。
"""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from bocomadp.config.base import BASE_DIR, expand_env_vars, load_config_yaml, resolve_path

# 虚拟路径前缀：与沙箱下载路径 /workspace/... 对齐（方案 A）。
# 上传虚拟路径 = /workspace/user-data/uploads/{stored_name}
VIRTUAL_PATH_PREFIX = "/workspace"

# 上传目录相对每个 agent workdir 的子目录名（位于 user-data 之下）
UPLOAD_SUBDIR = "uploads"


class UploadConfig(BaseModel):
    """上传限制与目录配置。"""

    enabled: bool = Field(default=True, description="是否开启文件上传能力。")
    base_dir: str | Path = Field(
        default="uploads",
        description=(
            "上传目录名（位于每个 session workdir 的 user-data 之下）。"
            "沙箱模式真实路径为 {workdir}/user-data/{base_dir}/..."
            "本地模式真实路径为 {workspace_dir}/{agent_id}/{base_dir}/..."
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

