# -*- coding: utf-8 -*-
"""文件上传 / 列举 / 下载 / 删除路由（沙箱感知重构版）。

上传文件统一经 ``workspace.get_backend()`` 落盘到 ``{workdir}/user-data/uploads``，
本模块提供独立的 ``GET /files/upload`` 端点用于下载已上传的原始文件或转换后的
``.md``（与通用文件下载 ``GET /files/{path}`` 互补：前者按 user/session/文件名
从元数据定位，后者按 workdir 相对路径直读）。


路径策略完全对齐 ``routers/workspace_files.py``：

- 通过 ``StorageBase.get_session`` 读取会话持久化的 ``workspace_id``；
- ``WorkspaceManagerBase.get_workspace`` 解析出沙箱内的 ``workspace.workdir``；
- 上传目录 = ``{workdir}/user-data/uploads``，由 ``UploadProvider`` 经
  ``workspace.get_backend()`` 落盘——双 PVC（session PVC）与共享 PVC
  （``/workspace/sessions/{id}``）天然保证 session 间隔离；
- 本地模式（``LocalWorkspaceManager``）走同一套 ``backend.write_file``，
  落盘到宿主机 workdir（同样 session 隔离），无需分支特判。

``.md`` 转换在 host 侧用第三方库完成（与沙箱无关），转换文本随原始文件
一并写入工作区，并存入 ``UploadedFile.markdown`` 供中间件渲染。
"""
from __future__ import annotations

import mimetypes
import os
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, status, UploadFile
from pydantic import BaseModel, Field

from agentscope.app.deps import get_current_user_id, get_storage, get_workspace_manager
from agentscope.app.storage import StorageBase
from agentscope.app.workspace_manager import WorkspaceManagerBase

from bocomadp.config.uploads_config import get_upload_config
from bocomadp.uploads import file_conversion
from bocomadp.uploads.db import UploadedFile, UploadedFileCreate, get_uploads_db
from bocomadp.uploads.manager import (
    FileSizeExceeded,
    PathTraversalError,
    TooManyFiles,
    UploadError,
    detect_image_mime,
    encode_image_base64,
    image_ext_to_mime,
    is_image,
    normalize_filename,
    to_upload_rel_path,
    to_virtual_path,
    validate_path_traversal,
)

uploads_router = APIRouter(prefix="/files", tags=["upload"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class UploadListResponse(BaseModel):
    files: list[UploadedFile]
    count: int


class UploadLimits(BaseModel):
    """上传能力/限制信息（供前端展示与阈值判断）。"""

    max_file_size_mb: float
    max_files_per_session: int
    streaming_threshold_mb: float


# ---------------------------------------------------------------------------
# 解析 workspace（与 workspace_files.py 同源）
# ---------------------------------------------------------------------------
async def _resolve_workspace(
    user_id: str,
    agent_id: str,
    session_id: str,
    storage: StorageBase,
    workspace_manager: WorkspaceManagerBase,
):
    """按会话记录解析其绑定的 workspace（含 PER_SESSION 语义）。"""
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


def _raise_upload_error(err: Exception) -> None:
    if isinstance(err, PathTraversalError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(err))
    if isinstance(err, FileSizeExceeded | TooManyFiles):
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(err))
    if isinstance(err, UploadError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(err))
    raise err


def _upload_abs(backend, workdir: str, name: str) -> str:
    return backend.join_path(workdir, to_upload_rel_path(name))


# ---------------------------------------------------------------------------
# GET /files/limits — 上传能力/限制信息
# ---------------------------------------------------------------------------
@uploads_router.get("/limits", response_model=UploadLimits)
async def get_upload_limits() -> UploadLimits:
    cfg = get_upload_config()
    return UploadLimits(
        max_file_size_mb=cfg.max_file_size_mb,
        max_files_per_session=cfg.max_files_per_session,
        streaming_threshold_mb=cfg.streaming_threshold_mb,
    )


# ---------------------------------------------------------------------------
# GET /files/uploads — 列举某会话已上传文件
# ---------------------------------------------------------------------------
@uploads_router.get("/uploads", response_model=UploadListResponse)
async def list_uploads(
    agent_id: str,
    session_id: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
) -> UploadListResponse:
    db = get_uploads_db()
    files = db.list_by_session(user_id, agent_id, session_id)
    return UploadListResponse(files=files, count=len(files))


# ---------------------------------------------------------------------------
# POST /files/upload — 上传单个文件（沙箱 / 本地 统一走 backend）
# ---------------------------------------------------------------------------
@uploads_router.post("/upload", response_model=UploadedFile)
async def upload_file(
    agent_id: str = Form(...),
    session_id: str = Form(...),
    file: UploadFile = File(...),
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> UploadedFile:
    cfg = get_upload_config()
    if not cfg.enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="upload disabled")

    # 限制：单文件大小
    data = await file.read()
    if len(data) > cfg.max_file_size_bytes:
        raise FileSizeExceeded(
            f"file {len(data)//1024//1024}MB exceeds limit "
            f"{cfg.max_file_size_mb}MB",
        )

    # 限制：单会话文件数
    db = get_uploads_db()
    if db.count_by_session(user_id, agent_id, session_id) >= cfg.max_files_per_session:
        raise TooManyFiles(
            f"session {session_id!r} exceeds {cfg.max_files_per_session} files",
        )

    # 安全文件名
    try:
        stored_name = normalize_filename(file.filename or "file")
        validate_path_traversal(stored_name)
    except UploadError as e:
        _raise_upload_error(e)

    virtual_path = to_virtual_path(stored_name)

    # 解析工作区与 backend（沙箱 / 本地统一）
    workspace = await _resolve_workspace(
        user_id, agent_id, session_id, storage, workspace_manager,
    )
    backend = workspace.get_backend()
    workdir = workspace.workdir

    # 上传目录由来宾工作区的 _ensure_workspace_layout() 保证存在
    #（user-data/uploads）；backend.write_file 也会自动创建父目录，
    # 无需重复 mkdir。
    abs_target = _upload_abs(backend, workdir, stored_name)

    # 落盘原始文件
    try:
        await backend.write_file(abs_target, data)
    except Exception as e:  # noqa: BLE001
        _raise_upload_error(UploadError(f"write failed: {e}"))

    # 转换 .md（host 侧第三方库），并写回工作区
    converted = False
    convert_format = None
    convert_error = None
    markdown = None
    base64 = None
    mime_type = None
    content_type = file.content_type
    is_img = is_image(content_type, stored_name)
    try:
        if is_img:
            # 图片：前端已校验图片格式，后端直接固化为 base64 存入元数据，
            # 供 view_image_tool 直接解析（不生成 .md，不内联进消息正文）。
            # MIME 以文件头实测为准（内容优先，兼容扩展名与实际内容不一致
            # 的真实文件，如 JPEG 内容存成 .png 名），实测不到时按扩展名兜底。
            mime_type = detect_image_mime(data) or image_ext_to_mime(stored_name)
            if mime_type is None:
                convert_error = (
                    "unsupported image format; supported: jpg, jpeg, png, webp"
                )
            else:
                base64 = encode_image_base64(data)
        elif file_conversion.is_supported_format(stored_name):
            fmt, md_text = file_conversion.convert_file_bytes(
                stored_name, data, content_type,
            )
            if md_text:
                converted = True
                convert_format = fmt
                markdown = md_text
                md_name = f"{os.path.splitext(stored_name)[0]}.md"
                await backend.write_file(
                    _upload_abs(backend, workdir, md_name),
                    md_text.encode("utf-8"),
                )
    except Exception as e:  # noqa: BLE001
        convert_error = str(e)

    record = db.add(
        UploadedFileCreate(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=getattr(workspace, "workspace_id", None),
            original_name=file.filename or stored_name,
            stored_name=stored_name,
            virtual_path=virtual_path,
            size_bytes=len(data),
            content_type=content_type,
            converted=converted,
            convert_format=convert_format,
            convert_error=convert_error,
            markdown=markdown,
            base64=base64,
            mime_type=mime_type,
        ),
    )
    return record


# ---------------------------------------------------------------------------
# GET /files/upload/download — 下载已上传文件（支持原始文件或 .md）
#   特用独立路径，避免与通用文件下载 GET /files/{path} 冲突。
# ---------------------------------------------------------------------------
@uploads_router.get("/upload/download")
async def download_upload(
    agent_id: str,
    session_id: str,
    filename: str,
    md: bool = False,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
):
    validate_path_traversal(filename)
    db = get_uploads_db()
    stored = db.get_by_session_file(user_id, session_id, filename, agent_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="upload not found")

    workspace = await _resolve_workspace(
        user_id, agent_id, session_id, storage, workspace_manager,
    )
    backend = workspace.get_backend()
    workdir = workspace.workdir

    name = f"{os.path.splitext(filename)[0]}.md" if md else filename
    abs_path = _upload_abs(backend, workdir, name)
    try:
        raw = await backend.read_file(abs_path)
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"read failed: {e}")

    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    encoded = quote(name)
    headers = {"Content-Disposition": f'attachment; filename*=UTF-8\'\'{encoded}'}
    return Response(content=raw, media_type=media_type, headers=headers)


# ---------------------------------------------------------------------------
# DELETE /files/upload — 删除已上传文件
# ---------------------------------------------------------------------------
@uploads_router.delete("/upload", status_code=status.HTTP_204_NO_CONTENT)
async def delete_upload(
    agent_id: str,
    session_id: str,
    filename: str,
    user_id: str = Depends(get_current_user_id),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
):
    validate_path_traversal(filename)
    db = get_uploads_db()
    stored = db.get_by_session_file(user_id, session_id, filename, agent_id)
    if stored is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="upload not found")

    workspace = await _resolve_workspace(
        user_id, agent_id, session_id, storage, workspace_manager,
    )
    backend = workspace.get_backend()
    workdir = workspace.workdir

    for nm in (filename, f"{os.path.splitext(filename)[0]}.md"):
        abs_path = _upload_abs(backend, workdir, nm)
        try:
            if await backend.file_exists(abs_path):
                await backend.remove_file(abs_path)
        except Exception:  # noqa: BLE001
            pass

    db.delete(user_id, agent_id, session_id, stored.stored_name)
