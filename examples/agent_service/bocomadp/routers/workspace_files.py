# -*- coding: utf-8 -*-
"""Workspace 文件列表 / 下载路由（迁移自框架 ``_router._workspace``）。

接口清单：
- GET /workspace/files           列出会话工作区文件（仅返回 user-data/outputs 交付物目录）
- GET /workspace/files/download  按虚拟路径 /workspace/<rel> 下载原始文件

与框架实现逐字对齐（含 ``resolve_workspace_path`` 的 ``backend._path_module.sep``
边界校验 + Local 后端 realpath 二次校验）。框架侧的 ``stat_size`` 即将随 src
回退删除，故在此内联等价实现 ``_stat_size``，不依赖框架将被删除的接口。

workspace 一律通过会话记录（DB 持久化的 ``config.workspace_id``）解析，
任意隔离策略（含 PER_SESSION）下均精确指向对应会话的工作目录。
"""
from __future__ import annotations

import mimetypes
import os
from datetime import UTC, datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from agentscope.app.deps import (
    get_current_user_id,
    get_storage,
    get_workspace_manager,
)
from agentscope.app.storage import StorageBase
from agentscope.app.workspace_manager import WorkspaceManagerBase
from agentscope.workspace._utils import (
    DEFAULT_DATA_DIR,
    DEFAULT_SESSIONS_DIR,
    DEFAULT_SKILLS_DIR,
)
from ..workspace._shared_pvc import (
    DEFAULT_USER_DATA_DIR,
    DEFAULT_USER_OUTPUTS_DIR,
)

workspace_files_router = APIRouter(prefix="/workspace", tags=["workspace-files"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class WorkspaceFileInfo(BaseModel):
    """会话工作区中单个文件的元信息。"""

    virtual_path: str = Field(
        ...,
        description="虚拟路径，如 /workspace/outputs/report.pdf",
    )
    size: int = Field(..., description="文件大小（字节）")
    modified: str | None = Field(
        None,
        description="最后修改时间（YYYY-MM-DD HH:MM:SS, UTC）",
    )


class WorkspaceFilesListResponse(BaseModel):
    """工作区文件列表接口的响应。"""

    files: list[WorkspaceFileInfo]
    count: int
    total_size: int


async def _resolve_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
):
    """按会话记录解析其绑定的 workspace（含 PER_SESSION 语义）。

    从 DB 读取会话持久化的 ``config.workspace_id``，而非现算——
    沙箱后端据此定位对应会话的工作目录。
    """
    session_record = await storage.get_session(user_id, agent_id, session_id)
    if session_record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session {session_id!r} not found.",
        )
    return await workspace_manager.get_workspace(
        user_id,
        agent_id,
        session_id,
        session_record.config.workspace_id,
    )


# ---------------------------------------------------------------------------
# 工作区文件列表 / 下载
# ---------------------------------------------------------------------------

#: 会话工作区内部文件的虚拟路径前缀。
WORKSPACE_VIRTUAL_PREFIX = "/workspace"

#: 任意层级均从列表排除的内部目录。
_EXCLUDED_DIRS = {DEFAULT_DATA_DIR, DEFAULT_SESSIONS_DIR, DEFAULT_SKILLS_DIR}

#: 文件列表接口扫描的最大目录深度。
_MAX_SCAN_DEPTH = 10


def resolve_workspace_path(workdir: str, virtual_path: str, backend) -> str:
    """把 ``/workspace/<rel>`` 映射为 backend 侧 ``workdir`` 下的路径。

    Raises:
        `ValueError`:
            当虚拟路径不以 :data:`WORKSPACE_VIRTUAL_PREFIX` 开头、指向
            工作区根目录，或解析到 ``workdir`` 之外（穿越 / symlink 逃逸）时。
    """
    stripped = virtual_path.lstrip("/")
    prefix = WORKSPACE_VIRTUAL_PREFIX.lstrip("/")
    if stripped != prefix and not stripped.startswith(prefix + "/"):
        raise ValueError(f"Path must start with {WORKSPACE_VIRTUAL_PREFIX}")
    rel = stripped[len(prefix) :].lstrip("/")
    if not rel:
        raise ValueError("Path must name a file, not the workspace root")
    resolved = backend.abspath(rel, cwd=workdir)  # 纯字符串规范化，不 touch 文件系统
    sep = getattr(backend._path_module, "sep", "/")
    workdir_norm = backend.normpath(workdir).rstrip(sep)
    if resolved == workdir_norm or not resolved.startswith(workdir_norm + sep):
        raise ValueError("Access denied: path traversal detected")
    # 本地后端二次 realpath 校验（防 symlink 逃逸，C2/M6）：realpath 消解符号链接后
    # 仍须位于 workdir 内。远程后端（Docker/E2B）无法在宿主机 realpath——沙箱内文件由
    # agent 自身持有，symlink 逃逸风险与内置 Bash/Read 工具同信任级，明确接受。
    if backend._path_module is os.path:
        real = os.path.realpath(resolved)
        real_base = os.path.realpath(workdir_norm)
        if real != real_base and not real.startswith(real_base + os.sep):
            raise ValueError("Access denied: path traversal detected")
    return resolved


async def _stat_size(backend, path: str) -> int | None:
    """返回 ``path`` 的大小（字节），不可 stat 时返回 None。

    统一通过 ``backend.exec_shell`` 在沙箱执行 ``wc -c`` 获取大小：
    - 远程后端（K8s/Docker/E2B 等）：命令在沙箱内执行，路径在沙箱内
      存在；不可在宿主机 ``os.stat``（``_path_module is os.path`` 在
      POSIX 上对 ``posixpath`` 后端恒真，会误判为本地后端）。
    - 本地后端：``exec_shell`` 即 ``create_subprocess_exec`` 子进程，
      语义与直接 ``os.stat`` 等价。

    内联替代框架 ``BackendBase.stat_size``（该实现即将随 src 回退删除）。
    """
    result = await backend.exec_shell(["wc", "-c", path])
    if not result.ok():
        return None
    try:
        return int(result.stdout.decode("utf-8", errors="replace").split()[0])
    except (ValueError, IndexError):
        return None


async def _scan_workspace_files(
    backend,
    path: str,
    rel: str,
    depth: int,
    files: list[WorkspaceFileInfo],
) -> None:
    """递归收集 ``path`` 下的 ``WorkspaceFileInfo`` 条目。

    ``rel`` 是条目相对工作区根的位置，统一用 ``"/"`` 拼接（绝不用后端分隔符），
    使 ``virtual_path`` 在各后端间保持一致。任意层级跳过隐藏项（前导 ``"."``）
    与 ``_EXCLUDED_DIRS``；``depth`` 达到 ``_MAX_SCAN_DEPTH`` 后停止递归。
    """
    try:
        entries = await backend.list_dir(path)
    except OSError:
        # Workdir 可能还不存在（会话从未写文件）——返回空列表。
        return
    for entry in entries:
        if entry.startswith(".") or entry in _EXCLUDED_DIRS:
            continue
        entry_path = backend.join_path(path, entry)
        entry_rel = f"{rel}/{entry}" if rel else entry
        if await backend.is_dir(entry_path):
            if depth < _MAX_SCAN_DEPTH:
                await _scan_workspace_files(
                    backend,
                    entry_path,
                    entry_rel,
                    depth + 1,
                    files,
                )
            continue
        mtime = await backend.stat_mtime(entry_path)
        modified = None
        if mtime is not None:
            modified = datetime.fromtimestamp(mtime, tz=UTC).strftime(
                "%Y-%m-%d %H:%M:%S",
            )
        files.append(
            WorkspaceFileInfo(
                virtual_path=f"{WORKSPACE_VIRTUAL_PREFIX}/{entry_rel}",
                size=await _stat_size(backend, entry_path) or 0,
                modified=modified,
            ),
        )


@workspace_files_router.get("/files", response_model=WorkspaceFilesListResponse)
async def list_workspace_files(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> WorkspaceFilesListResponse:
    """列出会话工作区中的文件，仅返回 ``user-data/outputs`` 交付物目录。"""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    backend = workspace.get_backend()
    files: list[WorkspaceFileInfo] = []
    outputs_dir = backend.join_path(
        workspace.workdir,
        DEFAULT_USER_DATA_DIR,
        DEFAULT_USER_OUTPUTS_DIR,
    )
    # rel 前缀保持与磁盘布局一致（user-data/outputs），
    # 使 virtual_path 形如 /workspace/user-data/outputs/<file>，
    # 下载接口按虚拟路径解析即可直接命中，无需改动。
    await _scan_workspace_files(
        backend,
        outputs_dir,
        f"{DEFAULT_USER_DATA_DIR}/{DEFAULT_USER_OUTPUTS_DIR}",
        0,
        files,
    )
    files.sort(key=lambda f: f.virtual_path)
    return WorkspaceFilesListResponse(
        files=files,
        count=len(files),
        total_size=sum(f.size for f in files),
    )


@workspace_files_router.get("/files/download")
async def download_workspace_file(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    path: str = Query(description="虚拟路径，如 /workspace/report.pdf"),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> Response:
    """按虚拟路径下载会话工作区中的文件。"""
    workspace = await _resolve_workspace(
        user_id,
        agent_id,
        session_id,
        storage,
        workspace_manager,
    )
    backend = workspace.get_backend()
    try:
        resolved = resolve_workspace_path(workspace.workdir, path, backend)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    if not await backend.file_exists(resolved):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"File not found: {path}",
        )
    if await backend.is_dir(resolved):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Path is not a file: {path}",
        )
    content = await backend.read_file(resolved)
    filename = backend.basename(resolved)
    content_type = (
        mimetypes.guess_type(filename)[0]
        or "application/octet-stream"
    )
    # RFC 5987：响应头按 latin-1 编码，非 ASCII 文件名（如中文）必须用
    # ``filename*=UTF-8''<percent-encoded>`` 形式传输，否则 UnicodeEncodeError。
    # ASCII 文件名走常规 ``filename``；二者都给出时浏览器优先取 ``filename*``。
    try:
        filename.encode("latin-1")
    except UnicodeEncodeError:
        fallback = filename.encode("ascii", errors="ignore").decode(
            "ascii",
        ).strip() or "download"
        content_disposition = (
            f'attachment; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(filename, safe='')}"
        )
    else:
        content_disposition = f'attachment; filename="{filename}"'
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": content_disposition,
        },
    )
