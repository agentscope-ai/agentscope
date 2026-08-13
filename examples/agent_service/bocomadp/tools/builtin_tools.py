# -*- coding: utf-8 -*-
"""Built-in tools — example custom tools for the agent.

Each function here is decorated with ``@tool`` from agentscope so
it gets auto-registered when :meth:`ToolRegistry.load_builtin_tools`
imports this module.

## How to add a new tool

1. Write a function with type hints and a docstring.
2. Decorate it with ``@tool``.
3. The ``ToolRegistry`` will pick it up automatically.

## Custom tools

Put product-specific tools in ``custom/`` to keep built-in tools
clean. The ``custom/`` package is auto-imported if it exists.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from agentscope.tool import tool
except ImportError:
    # Fallback: if agentscope.tool is not available, create a no-op
    # decorator so the module still imports for syntax checking.
    def tool(*args, **kwargs):  # type: ignore
        """Fallback @tool decorator when agentscope is not installed."""
        if len(args) == 1 and callable(args[0]):
            fn = args[0]
            fn._is_tool = True  # type: ignore
            return fn

        def decorator(fn):
            fn._is_tool = True  # type: ignore
            return fn

        return decorator


@tool
def get_current_time() -> str:
    """Get the current date and time.

    Returns:
        str: Current date and time in ISO format.
    """
    from datetime import datetime

    return datetime.now().isoformat()


@tool
def echo(text: str) -> str:
    """Echo the input text back to the caller.

    Args:
        text (str): The text to echo.

    Returns:
        str: The same text.
    """
    return text


# ---------------------------------------------------------------------------
# 文件上传相关工具（配合上传能力 / UploadsMiddleware 使用）
# ---------------------------------------------------------------------------
@tool
def list_uploaded_files(
    user_id: str = "",
    session_id: str = "",
    virtual_path: str = "",
) -> str:
    """列出某用户/会话下已上传的文件。

    由上传能力写入的 <context name="files"> 只包含大纲与虚拟路径引用；
    当你需要确认当前会话有哪些文件、或需要完整虚拟路径时，调用本工具。

    按 ``(user_id, session_id)`` 过滤——方案 A 下文件隔离由工作区（workdir）
    物理保证，无需也不按虚拟路径中的 agent/user/session 反解。

    Args:
        user_id (str): 租户 id（与上传时一致）。当前会话下可留空由框架注入。
        session_id (str): 会话 id（与上传时一致）。当前会话下可留空由框架注入。
        virtual_path (str): 保留参数，便于模型在消息中附带上下文虚拟路径时
            直接透传；本工具按 user/session 查库，不会据此反解或过滤。

    Returns:
        str: 文件清单，每行一条，含文件名与 virtual_path（可传给
        read_uploaded_file 按虚拟路径读取原文或转换后的 .md）。
    """
    from bocomadp.uploads.db import get_uploads_db

    if not user_id or not session_id:
        return (
            "缺少 user_id / session_id。请直接传入当前会话的这两个值"
            "（框架通常会自动注入），或同时传入从 <context name=\"files\">"
            " 复制的 virtual_path 作为回显参考。"
        )

    # 上传元数据存于 host 侧 SQLite（两种模式通用），按 (user_id, session_id)
    # 直接查库。agent_id 传空字符串：方案 A 下隔离由工作区物理保证，
    # list_by_session 在 agent_id 为空时仅按 user/session 过滤，可正确返回
    # 该会话全部上传文件。
    rows = get_uploads_db().list_by_session(user_id, "", session_id)
    if not rows:
        return "该会话下暂无上传文件。"

    lines = []
    for r in rows:
        conv = f"已转文本({r.convert_format})" if r.converted else "仅原始文件"
        lines.append(f"- {r.original_name}  [{conv}]  virtual_path={r.virtual_path}")
    return "\n".join(lines)


@tool
def read_uploaded_file(
    virtual_path: str = "",
    user_id: str = "",
    session_id: str = "",
    agent_id: str = "",
    max_chars: int = 8000,
) -> str:
    """按虚拟路径读取上传文件的内容（沙箱感知）。

    适用场景：
    1. 读取转换后的 Markdown 全文（文件名以 .md 结尾）。
    2. 读取原始文本文件（前提是纯文本；二进制如 PDF 请读同名 .md）。

    方案 A 下的虚拟路径形如 ``/workspace/user-data/uploads/{filename}``，
    **不再编码** user/session，因此本工具需要直接传入 ``user_id`` /
    ``session_id``（框架通常会自动注入当前会话）才能唯一定位记录；
    ``virtual_path`` 仅用于反解文件名。``agent_id`` 可进一步精确过滤
    （同一 user/session 下不同 agent 上传同名文件时避免误命中）。

    沙箱模式下，上传文件物理位于会话 workdir 的 ``user-data/uploads/`` 内，
    本工具优先返回 host 侧固化的 markdown（上传时转换并存入元数据）；
    对于超大原文，建议直接用你自己的文件读取 / bash 工具按沙箱内相对路径
    ``user-data/uploads/<文件名>`` 读取（该路径相对 workdir，可直接访问）。

    Args:
        virtual_path (str): 上传接口返回 / list_uploaded_files 列出的
            virtual_path，例如 /workspace/user-data/uploads/report.pdf.md。
        user_id (str): 租户 id（与上传时一致）。当前会话下可留空由框架注入。
        session_id (str): 会话 id（与上传时一致）。当前会话下可留空由框架注入。
        agent_id (str): agent id（与上传时一致）。当前会话下可留空由框架注入；
            空串时仅按 user/session 过滤。
        max_chars (int): 返回的最大字符数，防止超大文件撑爆上下文，
            默认 8000。

    Returns:
        str: 文件文本内容（截断到 max_chars）；失败返回错误信息。
    """
    from bocomadp.uploads.db import get_uploads_db
    from bocomadp.uploads.manager import resolve_upload_parts

    if not user_id or not session_id:
        return (
            "缺少 user_id / session_id。请直接传入当前会话的这两个值"
            "（框架通常会自动注入），以便唯一定位上传记录。"
        )

    try:
        _, _, filename = resolve_upload_parts(virtual_path)
    except Exception as exc:  # noqa: BLE001
        return f"路径解析失败（可能越权或非法）: {exc}"

    rec = get_uploads_db().get_by_session_file(
        user_id, session_id, filename, agent_id,
    )
    if rec is None:
        return f"上传记录不存在: {virtual_path}"

    # 若是转换后的 .md，优先返回固化在元数据中的 markdown（host 侧缓存）。
    if filename.endswith(".md") and rec.markdown:
        text = rec.markdown
    elif rec.markdown and not filename.endswith(".md"):
        # 请求原始文件但已有 .md：提示可改读 .md
        text = (
            f"(原始文件为二进制/非纯文本，请读取同名 .md："
            f"{virtual_path}.md)\n\n"
            f"{rec.markdown[:max_chars]}"
        )
    else:
        # 无固化 markdown：沙箱内文件需经工作区文件工具读取。
        sandbox_rel = f"user-data/uploads/{filename}"
        return (
            f"该文件位于工作区内，路径为：{sandbox_rel}\n"
            f"（相对当前会话 workdir，可用你的文件读取 / bash 工具直接访问；"
            f"也可通过路由 /files/upload/download?filename={filename} "
            f"下载，附加 &md=1 可获得转换后的 .md 文本，两种部署模式均可）。"
        )

    if len(text) > max_chars:
        return text[:max_chars] + f"\n…(已截断，共 {len(text)} 字符)"
    return text
