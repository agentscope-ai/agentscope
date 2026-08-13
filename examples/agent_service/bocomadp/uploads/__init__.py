# -*- coding: utf-8 -*-
"""文件上传核心包：路径隔离 / 虚拟路径协议 / 转换 / 配置。

只导出供路由与中间件使用的稳定 API，避免循环依赖。
"""
from __future__ import annotations

from .manager import (
    FileSizeExceeded,
    PathTraversalError,
    TooManyFiles,
    UploadError,
    claim_unique_filename,
    cleanup_stale_upload_staging_files,
    is_image,
    normalize_filename,
    resolve_upload_parts,
    to_virtual_path,
    validate_path_traversal,
    VIRTUAL_PATH_PREFIX,
)
from .file_conversion import convert_file_bytes, is_supported_format
from ..config.uploads_config import UploadConfig, get_upload_config

__all__ = [
    "UploadError",
    "PathTraversalError",
    "FileSizeExceeded",
    "TooManyFiles",
    "claim_unique_filename",
    "cleanup_stale_upload_staging_files",
    "is_image",
    "normalize_filename",
    "validate_path_traversal",
    "resolve_upload_parts",
    "to_virtual_path",
    "VIRTUAL_PATH_PREFIX",
    "convert_file_bytes",
    "is_supported_format",
    "UploadConfig",
    "get_upload_config",
]
