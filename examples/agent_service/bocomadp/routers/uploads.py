# -*- coding: utf-8 -*-
"""文件上传 REST 接口（前缀 /api/uploads）。

接口清单：
- GET  /api/uploads/limits              限制信息
- GET  /api/uploads/files               列出某 session 文件
- POST /api/uploads/files               同步上传单文件 (<= 流式阈值)
- POST /api/uploads/files/streaming     分块流式上传大文件
- DELETE /api/uploads/files            删除文件
- GET  /api/uploads/files/download      按虚拟路径下载原始文件

安全：图片在接口层拒绝；magic bytes + 扩展名二次校验；越权路径校验。
落盘：atomic_write_file（.part -> os.replace）；上传后触发 convert_file -> .md。
"""
from __future__ import annotations

import base64
import binascii
import mimetypes
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel

from bocomadp.config.uploads_config import get_upload_config
from bocomadp.uploads.file_conversion import convert_file, UnsupportedFileType
from bocomadp.uploads.manager import (
    UploadError,
    atomic_write_file,
    get_session_upload_dir,
    is_image,
    resolve_upload_path,
    to_virtual_path,
)

uploads_router = APIRouter(prefix="/uploads", tags=["uploads"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class LimitsResp(BaseModel):
    max_file_size_mb: float
    max_files_per_session: int
    streaming_threshold_mb: float


class FileMeta(BaseModel):
    filename: str
    virtual_path: str
    converted: bool
    artifact_url: str | None = None


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def _check_image(filename: str, content_type: str | None) -> None:
    if is_image(filename, content_type):
        raise HTTPException(
            status_code=415,
            detail="image upload is not supported via this endpoint; use multimodal input instead",
        )


def _magic_check(data: bytes, ext: str) -> None:
    """二次校验 magic bytes（简单白名单）。"""
    if ext == ".pdf" and not data.startswith(b"%PDF"):
        raise HTTPException(status_code=400, detail="file content is not a valid PDF")
    if ext in {".docx", ".pptx", ".xlsx"} and data[:4] != b"PK\x03\x04":
        # 均为 zip 容器
        raise HTTPException(status_code=400, detail="file content does not match extension")
    if ext == ".zip" and data[:4] != b"PK\x03\x04":
        raise HTTPException(status_code=400, detail="file content is not a valid zip")


def _persist_and_convert(
    user_id: str, session_id: str, filename: str, data: bytes
) -> FileMeta:
    cfg = get_upload_config()
    if len(data) > cfg.max_file_size_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file exceeds max size {cfg.max_file_size_mb}MB",
        )
    directory = get_session_upload_dir(user_id, session_id)
    n_files = len([p for p in directory.iterdir() if p.is_file()])
    if n_files >= cfg.max_files_per_session:
        raise HTTPException(status_code=429, detail="too many files in this session")

    ext = Path(filename).suffix.lower()
    _check_image(filename, None)
    _magic_check(data[:8], ext)

    try:
        real = atomic_write_file(directory, filename, data)
    except UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # 触发转换 -> .md
    converted = False
    artifact_url = None
    try:
        result = convert_file(real)
        converted = result["converted"]
        if converted:
            artifact_url = (
                f"/api/uploads/files/download?"
                f"user_id={user_id}&session_id={session_id}"
                f"&filename={real.name}.md"
            )
    except UnsupportedFileType:
        converted = False
    except Exception:
        converted = False

    return FileMeta(
        filename=real.name,
        virtual_path=to_virtual_path(user_id, session_id, real.name),
        converted=converted,
        artifact_url=artifact_url,
    )


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------
@uploads_router.get("/limits", response_model=LimitsResp)
async def limits() -> LimitsResp:
    cfg = get_upload_config()
    return LimitsResp(
        max_file_size_mb=cfg.max_file_size_mb,
        max_files_per_session=cfg.max_files_per_session,
        streaming_threshold_mb=cfg.streaming_threshold_mb,
    )


@uploads_router.get("/files", response_model=list[FileMeta])
async def list_files(
    user_id: str = Query(..., description="租户 id"),
    session_id: str = Query(..., description="会话 id"),
) -> list[FileMeta]:
    directory = get_session_upload_dir(user_id, session_id)
    out: list[FileMeta] = []
    for p in sorted(directory.iterdir()):
        if p.suffix == ".md" or not p.is_file():
            continue
        out.append(
            FileMeta(
                filename=p.name,
                virtual_path=to_virtual_path(user_id, session_id, p.name),
                converted=(p.with_suffix(".md")).exists(),
            )
        )
    return out


@uploads_router.post("/files", response_model=FileMeta)
async def upload_file(
    user_id: str = Form(..., description="租户 id"),
    session_id: str = Form(..., description="会话 id"),
    file: UploadFile = File(..., description="待上传文件"),
) -> FileMeta:
    data = await file.read()
    try:
        return _persist_and_convert(
            user_id, session_id, file.filename or "file", data
        )
    except UploadError as e:
        # 参数/校验类错误返回 400，而非裸 500
        raise HTTPException(status_code=400, detail=str(e)) from e


@uploads_router.post("/files/streaming", response_model=FileMeta)
async def upload_streaming(
    user_id: str = Form(..., description="租户 id"),
    session_id: str = Form(..., description="会话 id"),
    file: UploadFile = File(..., description="分块上传文件"),
    chunk_index: int = Header(..., alias="X-Chunk-Index", description="当前分块序号(0-based)"),
    chunk_total: int = Header(..., alias="X-Chunk-Total", description="总分块数"),
) -> FileMeta:
    directory = get_session_upload_dir(user_id, session_id)
    # 临时聚合路径：用唯一 id 区分并发会话
    staging = directory / f".stream_{uuid.uuid4().hex}.part"
    data = await file.read()
    try:
        with open(staging, "ab") as f:
            f.write(data)
        # 收齐最后一片后触发落地 + 转换
        if chunk_index + 1 >= chunk_total:
            full = staging.read_bytes()
            staging.unlink(missing_ok=True)
            return _persist_and_convert(user_id, session_id, file.filename or "file", full)
        return FileMeta(
            filename=file.filename or "file",
            virtual_path="",
            converted=False,
        )
    except Exception:
        staging.unlink(missing_ok=True)
        raise


@uploads_router.delete("/files")
async def delete_file(
    user_id: str = Query(..., description="租户 id"),
    session_id: str = Query(..., description="会话 id"),
    filename: str = Query(..., description="文件名"),
) -> dict:
    virtual = to_virtual_path(user_id, session_id, filename)
    try:
        real = resolve_upload_path(virtual)
    except UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not real.exists():
        raise HTTPException(status_code=404, detail="file not found")
    real.unlink(missing_ok=True)
    md = real.with_suffix(".md")
    md.unlink(missing_ok=True)
    return {"deleted": True, "filename": real.name}


@uploads_router.get("/files/download")
async def download_file(
    user_id: str = Query(..., description="租户 id"),
    session_id: str = Query(..., description="会话 id"),
    filename: str = Query(..., description="文件名（可带 .md 后缀取转换结果）"),
) -> "Response":
    from fastapi.responses import Response  # noqa: WPS433

    virtual = to_virtual_path(user_id, session_id, filename)
    try:
        real = resolve_upload_path(virtual)
    except UploadError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    if not real.exists():
        raise HTTPException(status_code=404, detail="file not found")
    content = real.read_bytes()
    media = mimetypes.guess_type(str(real))[0] or "application/octet-stream"
    return Response(content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{real.name}"'})


__all__ = ["uploads_router"]
