# -*- coding: utf-8 -*-
"""上传文件落盘与路径隔离核心（移植自 deer-flow backend/.../uploads/manager.py）。

职责：
- 解析虚拟路径 virtual://uploads/{agent_id}/{user_id}/sessions/{session_id}/{filename} ↔ 真实路径
- 越权 / 路径穿越校验（防 ``..`` 逃逸）
- 文件名归一化（只取 basename）、冲突加 ``_N`` 后缀
- 原子落盘（先写 ``.part`` 再 ``os.replace``）、``O_NOFOLLOW`` 防软链接逃逸
- 启动时清理遗留 ``.part``（crash recovery）

设计要点：
- 上传根目录位于 workspace 内：``{workspace_dir}/{agent_id}/uploads``
  （见 ``bocomadp.config.uploads_config.get_agent_upload_root``），
  agent 通用文件工具基于 workdir 扫描时可直接看到上传文件。
- 隔离层级：{root}/{user_id}/sessions/{session_id}/files/
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from bocomadp.config.uploads_config import (
    VIRTUAL_PATH_PREFIX,
    get_agent_upload_root,
    get_upload_config,
)

# ---------------------------------------------------------------------------
# 常量 / 白名单
# ---------------------------------------------------------------------------
_ID_SAFE_RE = re.compile(r"^[a-zA-Z0-9._@-]+$")
_PART_SUFFIX = ".part"
_VIRTUAL_RE = re.compile(
    r"^virtual://uploads/(?P<agent_id>[^/]+)/(?P<user_id>[^/]+)/sessions/(?P<session_id>[^/]+)/(?P<filename>.+)$",
)


class UploadError(Exception):
    """上传/解析相关错误，路由层捕获后转 HTTP 响应。"""


def is_image(filename: str, content_type: str | None = None) -> bool:
    """判断是否为图片（接口层应拒绝图片上传）。

    与原 Plan「图片除外」一致：图片走多模态通道，不进上传落盘流程。
    """
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".tiff", ".svg"}
    if content_type and content_type.startswith("image/"):
        return True
    return Path(filename).suffix.lower() in image_exts


# ---------------------------------------------------------------------------
# 路径隔离 / 校验
# ---------------------------------------------------------------------------
def _check_id(name: str, label: str) -> None:
    """校验 user_id / session_id：仅做路径注入防护，放宽字符白名单。

    早期版本用 ``^[a-zA-Z0-9._-]+$`` 白名单，会拒绝邮箱、中文等合法
    user_id（如 ``wangjinchao602@qq.com``），导致上传直接 500。这里改为只
    拦截真正危险的输入（空值、路径分隔符、``..`` 穿越），允许邮箱/中文/UUID 等。
    """
    if not name:
        raise UploadError(f"invalid {label}: empty")
    # 拒绝路径分隔符与 '..' 穿越
    if "/" in name or "\\" in name or ".." in name or name in (".",):
        raise UploadError(f"invalid {label}: {name!r}")


def get_session_upload_dir(
    user_id: str,
    session_id: str,
    agent_id: str = "default",
) -> Path:
    """返回 {root}/{user_id}/sessions/{session_id}/files/ 并确保存在。

    ``root`` = ``{workspace_dir}/{agent_id}/uploads``（位于 agent workdir 下）。
    """
    _check_id(user_id, "user_id")
    _check_id(session_id, "session_id")
    d = (
        get_agent_upload_root(agent_id)
        / user_id
        / "sessions"
        / session_id
        / "files"
    )
    d.mkdir(parents=True, exist_ok=True)
    return d


def _session_root(user_id: str, session_id: str, agent_id: str = "default") -> Path:
    return get_agent_upload_root(agent_id) / user_id / "sessions" / session_id


def validate_path_traversal(path: Path) -> None:
    """确保解析出的真实路径仍落在 session files 目录内。"""
    # 由调用方在 resolve 后显式比较；此处提供通用校验
    if ".." in path.parts:
        raise UploadError("path traversal detected (..)")


def normalize_filename(filename: str, max_len: int | None = None) -> str:
    """只取 basename，拒绝路径分隔符与超长名。"""
    cfg = get_upload_config()
    max_len = max_len or cfg.max_filename_length
    name = Path(filename).name  # 丢弃任何目录成分
    if not name or name in (".", ".."):
        raise UploadError(f"invalid filename: {filename!r}")
    if len(name) > max_len:
        stem, suffix = Path(name).stem, Path(name).suffix
        keep = max_len - len(suffix)
        if keep <= 0:
            raise UploadError(f"filename too long: {filename!r}")
        name = stem[:keep] + suffix
    return name


def claim_unique_filename(directory: Path, filename: str) -> Path:
    """返回目录内不冲突的目标路径（冲突自动加 ``_1/_2`` 后缀）。"""
    target = directory / filename
    if not target.exists():
        return target
    stem, suffix = Path(filename).stem, Path(filename).suffix
    i = 1
    while True:
        candidate = directory / f"{stem}_{i}{suffix}"
        if not candidate.exists():
            return candidate
        i += 1


def _assert_within_session(
    path: Path,
    user_id: str,
    session_id: str,
    agent_id: str = "default",
) -> Path:
    """解析后断言真实路径在对应 session files 目录内。"""
    root = _session_root(user_id, session_id, agent_id)
    files_dir = root / "files"
    resolved = path.resolve()
    files_dir_resolved = files_dir.resolve()
    try:
        resolved.relative_to(files_dir_resolved)
    except ValueError:
        raise UploadError(
            "resolved path escapes session upload directory",
        )
    return resolved


def resolve_upload_path(virtual_path: str) -> Path:
    """把 virtual://uploads/{agent_id}/{user_id}/sessions/{session_id}/{filename}
    解析为受校验的真实文件路径。

    Returns:
        已解析且经过越权校验的真实路径（可能尚不存在，由调用方决定）。
    """
    m = _VIRTUAL_RE.match(virtual_path.strip())
    if not m:
        raise UploadError(f"invalid virtual upload path: {virtual_path!r}")
    agent_id = m.group("agent_id")
    user_id = m.group("user_id")
    session_id = m.group("session_id")
    filename_raw = m.group("filename")
    # 显式拒绝 filename 段内的路径穿越意图（normalize 虽会丢弃目录，
    # 但应尽早拦截越权意图）。
    if ".." in filename_raw or filename_raw.startswith("/"):
        raise UploadError(f"invalid filename segment: {filename_raw!r}")
    filename = normalize_filename(filename_raw)
    _check_id(agent_id, "agent_id")
    _check_id(user_id, "user_id")
    _check_id(session_id, "session_id")
    files_dir = get_session_upload_dir(user_id, session_id, agent_id)
    raw = files_dir / filename
    return _assert_within_session(raw, user_id, session_id, agent_id)


def to_virtual_path(
    user_id: str,
    session_id: str,
    filename: str,
    agent_id: str = "default",
) -> str:
    """构造虚拟路径（供 list / 下载 / 中间件注入使用）。"""
    return (
        f"{VIRTUAL_PATH_PREFIX}/{agent_id}/{user_id}/sessions/{session_id}/"
        f"{normalize_filename(filename)}"
    )


# ---------------------------------------------------------------------------
# 原子落盘
# ---------------------------------------------------------------------------
def open_upload_file_no_symlink(path: Path):
    """以 O_NOFOLLOW 打开目标文件，拒绝软链接逃逸。"""
    if path.is_symlink():
        raise UploadError(f"symlink not allowed: {path.name}")
    # os.open 不跟随符号链接 (O_NOFOLLOW)；目录攻击无法通过此句柄创建
    return os.fdopen(
        os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600),
        "wb",
    )


def atomic_write_file(directory: Path, filename: str, data: bytes) -> Path:
    """原子写入：先写 .part 再 os.replace，避免读到半成品。

    Returns:
        最终落盘的真实路径。
    """
    if len(data) > get_upload_config().max_file_size_bytes:
        raise UploadError("file exceeds max_file_size")
    fname = normalize_filename(filename)
    target = claim_unique_filename(directory, fname)
    part = Path(str(target) + _PART_SUFFIX)
    try:
        with open_upload_file_no_symlink(part) as f:
            f.write(data)
        os.replace(part, target)  # 原子替换
    except BaseException:
        if part.exists():
            part.unlink(missing_ok=True)
        raise
    return target


def cleanup_stale_upload_staging_files() -> int:
    """启动时清理遗留的 .part 文件（crash recovery）。

    上传根目录位于 workspace 内（每个 agent 一个 root），这里遍历
    workspace 下所有 agent 的 uploads 子目录进行清理。

    Returns:
        清理的文件数量。
    """
    from bocomadp.config.uploads_config import (
        UPLOAD_SUBDIR,
        get_workspace_dir,
    )

    ws_root = get_workspace_dir()
    if not ws_root.exists():
        return 0
    count = 0
    # workspace/{agent_id}/uploads/**/*.part
    for part in ws_root.glob(f"*/{UPLOAD_SUBDIR}/**/*{_PART_SUFFIX}"):
        try:
            part.unlink(missing_ok=True)
            count += 1
        except OSError:
            continue
    return count
