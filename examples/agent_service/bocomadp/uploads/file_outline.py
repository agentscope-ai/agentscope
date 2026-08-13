# -*- coding: utf-8 -*-
"""文件大纲/预览生成（移植自 deer-flow utils/file_outline.py）。

用于 UploadsMiddleware 注入上下文：只给 LLM 看大纲/前 N 行，
而非全文，避免撑爆 context。Agent 需要全文时再用工具按虚拟路径读取。
"""
from __future__ import annotations

import re
from pathlib import Path

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.MULTILINE)


def create_outline(
    md_path: Path,
    max_chars: int = 1500,
    with_headings: bool = True,
) -> str:
    """从转换后的 .md 文件生成大纲。

    - Markdown：优先提取 ``#`` 标题层级作为大纲；
    - 其他文本：取前 ``max_chars`` 字符预览；
    - 文件不存在 / 读取失败：返回空串（调用方决定降级策略）。
    """
    try:
        text = Path(md_path).read_text(encoding="utf-8", errors="ignore")
    except (OSError, UnicodeDecodeError):
        return ""


def create_outline_text(
    text: str,
    max_chars: int = 1500,
    with_headings: bool = True,
) -> str:
    """从 markdown 文本字符串生成大纲（沙箱模式：`.md` 已固化在 DB 中）。

    逻辑与 :func:`create_outline` 一致，但输入是内存字符串，
    避免中间件同步读取沙箱内文件。
    """
    if not text:
        return ""

    if with_headings and _HEADING_RE.search(text):
        headings = _HEADING_RE.findall(text)
        lines = [
            f"{'  ' * (len(h) - 1)}- {title.strip()}"
            for h, title in headings
        ]
        outline = "\n".join(lines)
        if len(headings) <= 3:
            preview = text[:max_chars].strip()
            outline = f"{outline}\n\n预览：\n{preview}"
        return outline.strip()

    preview = text[:max_chars].strip()
    if len(text) > max_chars:
        preview += "\n…(内容已截断，使用工具读取全文)"
    return preview
