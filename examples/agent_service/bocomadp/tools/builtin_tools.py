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

    取 user_id/session_id 的优先级：
      1. 直接传入 user_id + session_id；
      2. 传入 virtual_path（格式
         virtual://uploads/{agent_id}/{user_id}/sessions/{session_id}/{filename}），
         工具会自动反解出 user_id 与 session_id。

    Args:
        user_id (str): 租户 id（与上传时一致）。可留空，改为传 virtual_path。
        session_id (str): 会话 id（与上传时一致）。可留空，改为传 virtual_path。
        virtual_path (str): 上传接口返回 / 文件上下文中列出的虚拟路径，
            可单独用于反解 user_id 与 session_id。

    Returns:
        str: 文件清单，每行一条，含文件名与 virtual_path（可传给
        read_uploaded_file 按虚拟路径读取原文或转换后的 .md）。
    """
    import re

    from bocomadp.uploads.manager import get_session_upload_dir, to_virtual_path

    if not user_id or not session_id:
        vp = virtual_path or ""
        m = re.search(
            r"virtual://uploads/[^/]+/(?P<user_id>[^/]+)/sessions/(?P<session_id>[^/]+)/",
            vp,
        )
        if m:
            user_id = user_id or m.group("user_id")
            session_id = session_id or m.group("session_id")

    if not user_id or not session_id:
        return (
            "缺少 user_id / session_id。请直接传入这两个值，或传入包含它们的"
            " virtual_path（例如从消息中的 <context name=\"files\"> 复制）。"
        )

    try:
        directory = get_session_upload_dir(user_id, session_id)
    except Exception as exc:  # noqa: BLE001
        return f"无法访问上传目录: {exc}"

    files = [p for p in sorted(directory.iterdir()) if p.is_file() and p.suffix != ".md"]
    if not files:
        return "该会话下暂无上传文件。"

    lines = []
    for p in files:
        vpath = to_virtual_path(user_id, session_id, p.name)
        md = p.with_suffix(".md")
        conv = "已转文本" if md.exists() else "仅原始文件"
        lines.append(f"- {p.name}  [{conv}]  virtual_path={vpath}")
    return "\n".join(lines)


@tool
def read_uploaded_file(virtual_path: str, max_chars: int = 8000) -> str:
    """按虚拟路径读取上传文件的内容。

    适用于两种场景：
    1. 读取转换后的 Markdown 全文（文件名以 .md 结尾，如
       virtual://uploads/u1/s1/report.pdf.md）。
    2. 读取原始文本文件（前提是纯文本；二进制如 PDF 请读同名 .md）。

    Args:
        virtual_path (str): 上传接口返回 / list_uploaded_files 列出的
            virtual_path，例如 virtual://uploads/u1/s1/report.pdf.md。
        max_chars (int): 返回的最大字符数，防止超大文件撑爆上下文，
            默认 8000。

    Returns:
        str: 文件文本内容（截断到 max_chars）；失败返回错误信息。
    """
    from bocomadp.uploads.manager import resolve_upload_path

    try:
        real = resolve_upload_path(virtual_path)
    except Exception as exc:  # noqa: BLE001
        return f"路径解析失败（可能越权或非法）: {exc}"

    if not real.exists():
        return f"文件不存在: {virtual_path}"

    try:
        text = real.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:  # noqa: BLE001
        return f"无法读取（可能是二进制文件，请改用同名 .md）: {exc}"

    if len(text) > max_chars:
        return text[:max_chars] + f"\n…(已截断，共 {len(text)} 字符)"
    return text
