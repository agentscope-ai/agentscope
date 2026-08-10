# -*- coding: utf-8 -*-
"""文件上传核心包：落盘 / 路径隔离 / 转换 / 配置。

只导出供路由与中间件使用的稳定 API，避免循环依赖。
"""
from __future__ import annotations

from .manager import (
    UploadError,
    claim_unique_filename,
    cleanup_stale_upload_staging_files,
    get_session_upload_dir,
    is_image,
    normalize_filename,
    open_upload_file_no_symlink,
    resolve_upload_path,
    validate_path_traversal,
    VIRTUAL_PATH_PREFIX,
)
from bocomadp.config.uploads_config import UploadConfig, get_upload_config

__all__ = [
    "UploadError",
    "claim_unique_filename",
    "cleanup_stale_upload_staging_files",
    "get_session_upload_dir",
    "is_image",
    "normalize_filename",
    "open_upload_file_no_symlink",
    "resolve_upload_path",
    "validate_path_traversal",
    "VIRTUAL_PATH_PREFIX",
    "UploadConfig",
    "get_upload_config",
]
