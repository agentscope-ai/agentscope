# -*- coding: utf-8 -*-
"""OSS 打包下载接口 — 迁移 deer-flow downloads.py 的 /file-download。

功能链：_find_report_dir → _collect_files → _package_zip → _upload_to_oss → 签名 URL。
与 bocomadp 现有文件接口（workspace_files.py）同层、同鉴权（X-User-ID）、
同机制（backend 访问容器内文件）。

错误语义（与 deer-flow 一致）：所有错误（含鉴权/会话不存在）统一返回
HTTP 200 + success="false" + error，不产生 HTTP 4xx/5xx。

搜索布局（K8s 目标布局，统一 backend 访问）：
  ① {workdir}/user-data/uploads/*_intermediate/proofread_report.md
  ② {workdir}/user-data/outputs/proofread_report.md
  ③ {workdir} 递归
注：宿主侧 uploads 尚未迁移到容器内 user-data/uploads 前，① 级可能不命中，
②/③ 级兜底始终可用（① 级目录缺失不会阻断下载）。

凭证（方案 A）：Fernet 密文硬编码 + 环境变量 AGENTSCOPE_OSS_KEY 解密，
密文由 scripts/encrypt_oss_credentials.py 生成后填入下方 _ENC 常量。
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
import zipfile
from datetime import datetime

import oss2
from cryptography.fernet import Fernet
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel

from agentscope.app.deps import get_storage, get_workspace_manager
from agentscope.app.storage import StorageBase
from agentscope.app.workspace_manager import WorkspaceManagerBase

from bocomadp.workspace._shared_pvc import (
    DEFAULT_USER_DATA_DIR,
    DEFAULT_USER_OUTPUTS_DIR,
)

from .workspace_files import _resolve_workspace

logger = logging.getLogger(__name__)
oss_download_router = APIRouter(prefix="/workspace", tags=["oss-download"])

# ── 非敏感常量（迁移 deer-flow 值）──────────────────────────────
_OSS_ENDPOINT = "oss-cn-hefei-ceshi-d01-a.ops.hfr1cloud.bocomm.uatcld"
_OSS_DEFAULT_BUCKET = "fbrs-oss-ua1"
_OSS_SECURE = False
_OSS_PATH = "var/app/file/fbrs_airp"
ZIP_NAME_TEMPLATE = "proofread_report_{}_{}.zip"
ZIP_MIME_TYPE = "application/zip"
_SIGN_URL_EXPIRES = 3600 * 24 * 365 * 10

# ── 敏感凭证（方案 A：Fernet 密文 + 环境变量密钥）────────────────
# 生成: AGENTSCOPE_OSS_KEY=<key> python scripts/encrypt_oss_credentials.py <id> <secret>
# 密钥: 环境变量 AGENTSCOPE_OSS_KEY（Fernet key，urlsafe-base64 32 字节）
_OSS_ACCESS_KEY_ID_ENC = "gAAAAABqe9sB1AXSbS-kB3f6pQZj7-_BcoIw_fAPkE7RCpM77Wnr_v8EF_5wZQw4QUw5yy6DJorweHsDRqiYtlPNFnCZ9nMgYoU_qEhujRzy14Dq-fL2dGw="
_OSS_ACCESS_KEY_SECRET_ENC = "gAAAAABqe9sB2kjSmTAl3Kot4LG1PfT7RcJEGrAZrd70e7oUCcJx2PQk_Rtv3jQqBQWmowvM7U2pWNgnrDtsn9ejMdJSExFr92de675qjpYqz2-5wYwpasU="

_MAX_SCAN_DEPTH = 10


class FileDownloadResponse(BaseModel):
    """OSS 打包下载接口响应模型（对齐 deer-flow 字段语义）。"""

    download_url: str
    success: str
    error: str = ""


def _decrypt_credential(enc: str) -> str:
    """Fernet 解密；密钥缺失/解密失败 → HTTPException(500)。"""
    key = os.environ.get("AGENTSCOPE_OSS_KEY")
    if not key:
        raise HTTPException(500, "AGENTSCOPE_OSS_KEY is not configured")
    try:
        return Fernet(key.encode()).decrypt(enc.encode()).decode()
    except Exception as exc:
        raise HTTPException(500, "Failed to decrypt OSS credential") from exc


_bucket_client: oss2.Bucket | None = None


def _get_oss_bucket() -> oss2.Bucket:
    """懒初始化 OSS Bucket 客户端（模块级单例）— 对齐 deer-flow。"""
    global _bucket_client
    if _bucket_client is not None:
        return _bucket_client
    protocol = "https" if _OSS_SECURE else "http"
    auth = oss2.Auth(
        _decrypt_credential(_OSS_ACCESS_KEY_ID_ENC),
        _decrypt_credential(_OSS_ACCESS_KEY_SECRET_ENC),
    )
    _bucket_client = oss2.Bucket(
        auth,
        f"{protocol}://{_OSS_ENDPOINT}",
        _OSS_DEFAULT_BUCKET,
    )
    return _bucket_client


async def _find_report_dir(workdir: str, backend) -> str | None:
    """在 {workdir}/user-data/uploads 下找第一个 *_intermediate 目录。

    uploads 目录不存在或无 *_intermediate 匹配时返回 None——
    不在此处报错，由 ``_collect_files`` 的 ② outputs / ③ workdir
    递归兜底继续搜索（① 级缺失不应让 outputs 里的报告无法下载）。
    """
    uploads_dir = backend.join_path(
        workdir,
        DEFAULT_USER_DATA_DIR,
        "uploads",
    )
    try:
        entries = await backend.list_dir(uploads_dir)
    except OSError:
        return None
    for entry in entries:
        if entry.endswith("_intermediate") and await backend.is_dir(
            backend.join_path(uploads_dir, entry),
        ):
            return backend.join_path(uploads_dir, entry)
    return None


async def _rglob(backend, path: str, target: str, depth: int = 0) -> list[str]:
    """递归搜索 basename == target 的文件，返回 backend 侧完整路径列表。"""
    out: list[str] = []
    if depth > _MAX_SCAN_DEPTH:
        return out
    try:
        entries = await backend.list_dir(path)
    except OSError:
        return out
    for entry in entries:
        if entry.startswith("."):
            continue
        full = backend.join_path(path, entry)
        if await backend.is_dir(full):
            out.extend(await _rglob(backend, full, target, depth + 1))
        elif entry == target:
            out.append(full)
    return out


async def _collect_files(
    workdir: str,
    backend,
    report_dir: str | None,
) -> list[tuple[str, bytes]]:
    """三级降级搜索 proofread_report.md（对齐 deer-flow _collect_files）。

    ① report_dir（当前 *_intermediate 目录，可为 None）② outputs ③ workdir 递归。
    返回 [(archive_path, content_bytes), ...]；无文件 → HTTPException(404)。
    """
    search_paths = [
        report_dir,
        backend.join_path(
            workdir,
            DEFAULT_USER_DATA_DIR,
            DEFAULT_USER_OUTPUTS_DIR,
        ),
        workdir,
    ]
    for idx, path in enumerate(search_paths):
        if path is None:
            continue
        if idx < 2:
            md = backend.join_path(path, "proofread_report.md")
            if await backend.file_exists(md):
                content = await backend.read_file(md)
                return [("proofread_report.md", content)]
        else:
            matches = await _rglob(backend, path, "proofread_report.md")
            if matches:
                content = await backend.read_file(matches[0])
                return [("proofread_report.md", content)]
    raise HTTPException(
        status_code=404,
        detail="No downloadable files found for session",
    )


def _package_zip(
    files: list[tuple[str, bytes]],
    session_id: str,
) -> tuple[io.BytesIO, str]:
    """zip 打包（对齐 deer-flow）：ZIP_DEFLATED + writestr；名称含 session_id[:8]。"""
    now = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    zip_name = ZIP_NAME_TEMPLATE.format(now, session_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for archive_path, content in files:
            zf.writestr(archive_path, content)
    buf.seek(0)
    return buf, zip_name


def _upload_to_oss(zip_buffer: io.BytesIO, zip_name: str) -> str:
    """put_object + sign_url，返回签名下载链接；OssError → HTTPException(502)。"""
    bucket = _get_oss_bucket()
    try:
        bucket.put_object(
            f"{_OSS_PATH}{zip_name}",
            zip_buffer.getvalue(),
            headers={"Content-Type": ZIP_MIME_TYPE},
        )
        download_url = bucket.sign_url(
            "GET",
            f"{_OSS_PATH}{zip_name}",
            _SIGN_URL_EXPIRES,
            params={
                "response-content-disposition": (
                    f'attachment; filename="{zip_name}"'
                ),
            },
        )
        logger.info("Uploaded %s to OSS, url generated", zip_name)
        return download_url
    except oss2.exceptions.OssError as exc:
        logger.exception("OSS upload failed for %s", zip_name)
        raise HTTPException(
            status_code=502,
            detail=f"OSS upload failed: {exc}",
        ) from exc


@oss_download_router.get("/file-download", response_model=FileDownloadResponse)
async def file_download(
    agent_id: str = Query(...),
    session_id: str = Query(...),
    x_user_id: str | None = Header(None),
    storage: StorageBase = Depends(get_storage),
    workspace_manager: WorkspaceManagerBase = Depends(get_workspace_manager),
) -> FileDownloadResponse:
    """读取审校文件 → 打包 zip → 上传 OSS → 返回签名下载链接。

    所有错误统一返回 HTTP 200 + success="false" + error。
    """
    try:
        if not x_user_id:
            return FileDownloadResponse(
                success="false",
                download_url="",
                error="X-User-ID header is required.",
            )
        workspace = await _resolve_workspace(
            x_user_id,
            agent_id,
            session_id,
            storage,
            workspace_manager,
        )
        backend = workspace.get_backend()
        report_dir = await _find_report_dir(workspace.workdir, backend)
        # report_dir 可能为 None（uploads 无 *_intermediate），
        # _collect_files 内部跳过 ① 级，由 ②/③ 兜底搜索。
        files = await _collect_files(workspace.workdir, backend, report_dir)
        zip_buffer, zip_name = await asyncio.to_thread(
            _package_zip,
            files,
            session_id[:8],
        )
        download_url = await asyncio.to_thread(
            _upload_to_oss,
            zip_buffer,
            zip_name,
        )
        logger.info("session=%s upload oss file %s", session_id, zip_name)
        return FileDownloadResponse(
            success="true",
            download_url=download_url,
            error="",
        )
    except HTTPException as exc:
        return FileDownloadResponse(
            success="false",
            download_url="",
            error=str(exc.detail),
        )
    except Exception as exc:
        logger.exception("File download failed")
        return FileDownloadResponse(
            success="false",
            download_url="",
            error=str(exc),
        )


__all__ = ["oss_download_router", "FileDownloadResponse", "_get_oss_bucket"]
