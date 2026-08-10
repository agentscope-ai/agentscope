# -*- coding: utf-8 -*-
"""UploadsMiddleware —— 将上传文件以「大纲 + 虚拟路径引用」注入 human 消息。

对应 deer-flow 的 UploadsMiddleware（基于 HumanInputMiddleware）。
本框架使用 AgentScope 的 ``MiddlewareBase.on_reply`` 洋葱钩子，
通过覆写 ``on_reply`` 在消息进入 LLM 前改写 ``input_kwargs["messages"]``。

注入策略（对照 Plan 第 4 节，已修正为 outline + 引用，而非内联全文）：
- 从 ``message.additional_kwargs["files"]`` 取出文件列表；
- 优先用转换后的同名 ``.md`` 生成 outline（file_outline.create_outline）；
- 用 ``<context name="files">`` 包裹大纲 + 虚拟路径引用；
- 无 ``.md`` 时仅注入文件名 + 虚拟路径引用（Agent 用工具读原始文件）。
"""
from __future__ import annotations

import logging
from typing import Any, AsyncGenerator

try:
    from bocomadp.middleware.agent_middleware import MiddlewareBase
except Exception:  # pragma: no cover - agentscope 不可用时降级（如纯单测环境）
    class MiddlewareBase:  # type: ignore
        """最小兜底基类：仅在 AgentScope 不可用时使用，保证可导入与单测。"""

        async def on_reply(self, agent, input_kwargs, next_handler):
            async for event in next_handler():
                yield event

from bocomadp.uploads.manager import resolve_upload_path, to_virtual_path
from bocomadp.uploads.file_outline import create_outline

logger = logging.getLogger(__name__)


class UploadsMiddleware(MiddlewareBase):
    """人类输入中间件：把上传文件作为上下文注入。"""

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: AsyncGenerator,
    ) -> AsyncGenerator:
        messages = input_kwargs.get("messages")
        if not messages:
            async for event in next_handler():
                yield event
            return

        # 取最后一条 human 消息中的 files 元数据
        files = self._extract_files(messages)
        if not files:
            async for event in next_handler():
                yield event
            return

        blocks = []
        for fmeta in files:
            block = self._render_file_block(fmeta)
            if block:
                blocks.append(block)

        if blocks:
            usage_hint = (
                "\n\n提示：要列出本会话全部已上传文件，可用任一条 virtual_path "
                "调用 list_uploaded_files(virtual_path=...)，工具会自动反解出 "
                "user_id 与 session_id。"
            )
            injection = (
                "<context name=\"files\">\n"
                + "\n\n".join(blocks)
                + usage_hint
                + "\n</context>"
            )
            self._append_to_last_human(messages, injection)
            logger.info("UploadsMiddleware injected %d file block(s)", len(blocks))

        async for event in next_handler():
            yield event

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_files(messages: list) -> list[dict]:
        for msg in reversed(messages):
            # 兼容对象消息与 dict 消息两种形态
            f = getattr(msg, "additional_kwargs", None)
            if f is None and isinstance(msg, dict):
                f = msg.get("additional_kwargs")
            if isinstance(f, dict) and f.get("files"):
                return f["files"]
        return []

    @staticmethod
    def _render_file_block(fmeta: dict) -> str:
        filename = fmeta.get("filename") or (fmeta.get("virtual_path") or "").rsplit("/", 1)[-1]
        virtual_path = fmeta.get("virtual_path") or ""
        if not virtual_path:
            return ""
        try:
            real = resolve_upload_path(virtual_path)
        except Exception as e:  # 路径越权/非法：跳过该文件并记录
            logger.warning("skip file (resolve failed): %s (%s)", virtual_path, e)
            return ""

        md = real.with_suffix(".md")
        if md.exists():
            outline = create_outline(md).strip()
            if outline:
                return (
                    f"- 文件: {filename}\n"
                    f"  虚拟路径: {virtual_path}\n"
                    f"  大纲/预览:\n{outline}\n"
                    f"  (如需全文，请使用 list_uploaded_files / read 工具按虚拟路径读取)"
                )
        # 无 .md 时仅给文件名 + 路径引用
        return (
            f"- 文件: {filename}\n"
            f"  虚拟路径: {virtual_path}\n"
            f"  (暂无可预览文本，请使用工具读取原始文件)"
        )

    @staticmethod
    def _append_to_last_human(messages: list, text: str) -> None:
        for msg in reversed(messages):
            role = None
            if isinstance(msg, dict):
                role = msg.get("role") or msg.get("name")
            else:
                role = getattr(msg, "role", None) or getattr(msg, "name", None)
            if role in ("user", "human"):
                if isinstance(msg, dict):
                    content = msg.get("content")
                    if isinstance(content, str):
                        msg["content"] = f"{content}\n\n{text}"
                    elif isinstance(content, list):
                        content.append({"type": "text", "text": text})
                else:
                    content = getattr(msg, "content", None)
                    if isinstance(content, str):
                        msg.content = f"{content}\n\n{text}"
                    elif isinstance(content, list):
                        content.append({"type": "text", "text": text})
                return


# 模块级实例：MiddlewareRegistry.load_builtin() 会自动扫描并注册，
# 与 LoggingMiddleware 等并列，无需改 factory.py。
uploads_mw = UploadsMiddleware()
