# -*- coding: utf-8 -*-
"""文件上传核心：路径隔离 / 虚拟路径协议 / backend 落盘。

设计要点（沙箱感知，与 ``routers/workspace_files.py`` 下载逻辑同源）

================================================================

1. 上传目录一律位于 **会话 workdir 内** 的 ``user-data/uploads``：

   - 沙箱模式（``workspace_manager`` 非 ``None``）：
     ``workdir`` 由 ``workspace_manager.get_workspace()`` 解析，
     双 PVC 模式 = session 级 PVC（RWO，session 间物理隔离），
     共享 PVC 模式 = ``/workspace/sessions/{session_id}``（子目录隔离）。
     所有读写经 ``workspace.get_backend()``（沙箱内执行）。
   - 本地模式（``ADP_K8S_ENABLED=false`` 的 ``LocalWorkspaceManager``）：
     同样走 ``backend.write_file``，落盘到宿主机 workdir 的
     ``user-data/uploads``（session 隔离由 workdir 保证），**无 host 特判**。
     方案 A 下所有模式统一以 workdir 相对路径 ``user-data/uploads`` 落盘，
     不再回退到历史 ``{workspace_dir}/{agent_id}/uploads`` 结构。

2. 不同 session 的隔离由沙箱布局天然保证，本模块不再做任何跨 session
   路径拼接。

3. 虚拟路径协议与下载同源，采用 **workdir 相对路径** 范式，不再编码
   ``agent_id/user_id/session_id``（这些由 workdir 本身隔离）：

       /workspace/user-data/uploads/{stored_name}

   这样可直接被 Agent 自身的文件工具（相对 workdir 解析）读取，与
   ``GET /files/{path}`` 下载逻辑保持一致的 ``/workspace/...`` 前缀。

4. 原始文件与同名 ``.md`` 共存于上传目录；``.md`` 由接口层在上传时
   转换（host 侧第三方库）后随原始文件一并写入沙箱。

注：本文件**不**直接持有 workspace_manager / storage —— 这些由路由层
注入并负责解析 workdir + 取得 backend，再调用本模块的纯函数式 IO 助手。
这样保持 manager 无框架依赖、可单测。
"""
from __future__ import annotations

import os
from pathlib import Path

from bocomadp.config.uploads_config import (
    VIRTUAL_PATH_PREFIX,
    get_upload_config,
)
from bocomadp.workspace._shared_pvc import (
    DEFAULT_USER_DATA_DIR,
    DEFAULT_USER_UPLOADS_DIR,
)


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------
class UploadError(Exception):
    """上传相关通用异常。"""


class PathTraversalError(UploadError):
    """路径穿越 / 越权访问。"""


class FileSizeExceeded(UploadError):
    """文件大小超限。"""


class TooManyFiles(UploadError):
    """单会话文件数超限。"""


# ---------------------------------------------------------------------------
# 文件名安全
# ---------------------------------------------------------------------------
def normalize_filename(name: str) -> str:
    """把任意客户端文件名归一化为安全的存储名（保留扩展名）。

    - 去除目录分隔符、空字节、控制字符；
    - 用 ``_`` 替换空格与不可打印字符；
    - 限制长度（``UploadConfig.max_filename_length``）；
    - 保住最后一个合法扩展名。
    """
    cfg = get_upload_config()
    name = (name or "file").strip().replace("\x00", "")
    # 去掉路径成分，只取 basename
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    # 替换空白与控制字符
    allowed = []
    for ch in name:
        if ch in ("/", "\\", "\x00"):
            continue
        if ord(ch) < 32 or ord(ch) == 127:
            continue
        allowed.append(ch)
    name = "".join(allowed).strip().strip(".")
    if not name:
        name = "file"

    stem, dot, ext = name.rpartition(".")
    if dot:
        stem = stem[: cfg.max_filename_length - len(ext) - 1]
        name = f"{stem}.{ext}"
    else:
        name = name[: cfg.max_filename_length]
    return name


def validate_path_traversal(filename: str) -> None:
    """校验存储文件名不含路径穿越成分。"""
    if not filename:
        raise PathTraversalError("empty filename")
    if "/" in filename or "\\" in filename or "\x00" in filename:
        raise PathTraversalError(f"invalid filename: {filename!r}")
    if filename in (".", ".."):
        raise PathTraversalError(f"invalid filename: {filename!r}")


def claim_unique_filename(base_dir: Path, filename: str) -> Path:
    """在给定 host 目录内申请一个不冲突的文件路径。

    用于流式上传的中间态 staging（host 侧临时目录，见
    ``_STAGING_ROOT``），避免并发上传覆盖同一文件名；与最终落盘的
    workdir ``user-data/uploads`` 命名空间相互独立。
    """
    target = base_dir / filename
    if not target.exists():
        return target
    stem, dot, ext = filename.rpartition(".")
    suffix = dot + ext if dot else ""
    base_stem = stem or filename
    i = 1
    while True:
        candidate = base_dir / f"{base_stem}__{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def is_image(content_type: str | None, filename: str) -> bool:
    """判断是否为图片（接口层据此拒绝 / 标记）。"""
    if content_type and content_type.startswith("image/"):
        return True
    return filename.lower().rsplit(".", 1)[-1] in {
        "png", "jpg", "jpeg", "gif", "bmp", "webp", "svg", "tiff",
    }


# ---------------------------------------------------------------------------
# 图片支持（上传时固化为 base64，供 view_image_tool 解析）
# ---------------------------------------------------------------------------
# 仅支持视觉模型可消费的常见格式（对齐 deer-flow view_image_tool）：
# jpg / jpeg / png / webp。gif/bmp/svg/tiff 等虽被 is_image 识别，
# 但不做 base64 固化（上传记录 is_image 为 True 但 base64 为空，
# 中间件提示"暂不支持解析"）。
_IMAGE_EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def image_ext_to_mime(filename: str) -> str | None:
    """按扩展名返回支持的图片 MIME；不支持返回 ``None``。"""
    ext = filename.lower().rsplit(".", 1)[-1]
    return _IMAGE_EXT_TO_MIME.get(f".{ext}")


def detect_image_mime(data: bytes) -> str | None:
    """按 magic bytes 检测图片 MIME（与扩展名校验配合防伪冒）。"""
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if (
        len(data) >= 12
        and data.startswith(b"RIFF")
        and data[8:12] == b"WEBP"
    ):
        return "image/webp"
    return None


def encode_image_base64(data: bytes) -> str:
    """图片字节 → base64 字符串（上传时固化进元数据）。"""
    import base64

    return base64.b64encode(data).decode("utf-8")


# ---------------------------------------------------------------------------
# 虚拟路径协议（与 builtin 工具 / 中间件 / 下载逻辑兼容）
# ---------------------------------------------------------------------------
# 方案 A：虚拟路径与下载同源，采用 workdir 相对范式，不编码 agent/user/session
# （这些由 workdir 本身隔离）：
#     /workspace/user-data/uploads/{stored_name}
_PVC_UPLOADS_PREFIX = f"{VIRTUAL_PATH_PREFIX}/{DEFAULT_USER_DATA_DIR}/{DEFAULT_USER_UPLOADS_DIR}"


def to_virtual_path(stored_name: str) -> str:
    """构造虚拟路径：``/workspace/user-data/uploads/{stored_name}``。

    与下载逻辑（``GET /files/{path}``，前缀 ``/workspace``）保持一致的范式，
    可直接被 Agent 自身的文件工具（相对 workdir 解析）读取。不再编码
    ``agent_id/user_id/session_id``，因为 workdir 已天然隔离不同会话。
    """
    validate_path_traversal(stored_name)
    return f"{_PVC_UPLOADS_PREFIX}/{stored_name}"


def to_upload_rel_path(stored_name: str) -> str:
    """返回上传文件相对于 workdir 的路径：``user-data/uploads/{stored_name}``。

    路由层落盘 / builtin 工具读取沙箱文件时共用，避免重复拼接常量。
    """
    validate_path_traversal(stored_name)
    return f"{DEFAULT_USER_DATA_DIR}/{DEFAULT_USER_UPLOADS_DIR}/{stored_name}"


def resolve_upload_parts(virtual_path: str) -> tuple[str, str, str]:
    """从虚拟路径反解 ``(user_id, session_id, filename)``。

    .. warning::
       方案 A 下虚拟路径 ``/workspace/user-data/uploads/{stored_name}`` 已**不再
       编码** ``user_id/session_id``。本函数仅用于兼容「从虚拟路径取文件名」的
       场景，返回的 user_id / session_id 恒为空串，调用方应改用路由参数或
       ``get_by_session_file`` 按 ``(user_id, session_id, stored_name)`` 定位。

    Raises:
        `UploadError`: 格式非法时。
    """
    vp = (virtual_path or "").strip()
    if not vp.startswith(f"{_PVC_UPLOADS_PREFIX}/"):
        raise UploadError(f"invalid virtual path: {virtual_path!r}")
    filename = vp[len(_PVC_UPLOADS_PREFIX) + 1 :]
    validate_path_traversal(filename)
    return "", "", filename


# ---------------------------------------------------------------------------
# staging 清理（host 侧，仅流式上传中间态用；本模块保留供调用方按需清理）
# ---------------------------------------------------------------------------
import tempfile  # noqa: E402

_STAGING_ROOT = Path(tempfile.gettempdir()) / "as_uploads_staging"


def cleanup_stale_upload_staging_files(max_age_seconds: int = 3600) -> int:
    """清理超过阈值的 host 侧 staging 临时文件（流式上传中间态）。"""
    if not _STAGING_ROOT.exists():
        return 0
    import time

    now = time.time()
    removed = 0
    for p in _STAGING_ROOT.iterdir():
        try:
            if now - p.stat().st_mtime > max_age_seconds:
                p.unlink()
                removed += 1
        except OSError:
            continue
    return removed


__all__ = [
    "UploadError",
    "PathTraversalError",
    "FileSizeExceeded",
    "TooManyFiles",
    "VIRTUAL_PATH_PREFIX",
    "normalize_filename",
    "validate_path_traversal",
    "claim_unique_filename",
    "is_image",
    "image_ext_to_mime",
    "detect_image_mime",
    "encode_image_base64",
    "to_virtual_path",
    "to_upload_rel_path",
    "resolve_upload_parts",
    "cleanup_stale_upload_staging_files",
]
