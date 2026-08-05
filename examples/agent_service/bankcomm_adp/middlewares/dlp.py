# -*- coding: utf-8 -*-
"""数据防泄漏（DLP）中间件。

对发往模型的输入做正则脱敏，
防止手机号、身份证号、银行卡号等敏感信息流入 LLM 或日志。

【占位实现】仅做正则替换；生产环境可对接行内专用 DLP 服务。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from agentscope.middleware import MiddlewareBase

from ..config import settings

# 脱敏规则：匹配 -> 替换模板
# 正则刻意写得保守，优先准确率避免误伤正常文本
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # 手机号：1开头11位
    (re.compile(r"\b1[3-9]\d{9}\b"), "1**-****-****"),
    # 身份证号：18位（末位可能是X）
    (re.compile(r"\b\d{17}[\dXx]\b"), "******************"),
    # 银行卡号：16~19位连续数字
    (re.compile(r"\b\d{16,19}\b"), "**** **** **** ****"),
]


def mask_text(text: str) -> str:
    """对文本中的敏感信息做掩码处理。"""
    if not text:
        return text
    masked = text
    for pattern, replacement in _PATTERNS:
        masked = pattern.sub(replacement, masked)
    return masked


class DLPMiddleware(MiddlewareBase):
    """在模型调用前对消息内容做脱敏。"""

    async def on_model_call(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., Any],
    ) -> Any:
        if settings.dlp_enabled:
            messages = input_kwargs.get("messages")
            if messages:
                for msg in messages:
                    self._mask_msg(msg)

        return await next_handler(**input_kwargs)

    def _mask_msg(self, msg: Any) -> None:
        """对单条 Msg 的文本内容做就地脱敏。"""
        # Msg 的 content 可能是字符串或 ContentBlock 列表
        content = getattr(msg, "content", None)
        if isinstance(content, str):
            msg.content = mask_text(content)
        elif isinstance(content, list):
            for block in content:
                text = getattr(block, "text", None)
                if isinstance(text, str):
                    block.text = mask_text(text)
