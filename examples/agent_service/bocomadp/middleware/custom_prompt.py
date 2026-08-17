# -*- coding: utf-8 -*-
"""CustomPromptMiddleware —— 请求级自定义提示词整体覆盖（对齐 deer-flow custom_prompt）。

deer-flow 在 agent 构建时用 ``custom_params["custom_prompt"]`` **整体替换**
``system_prompt``（绕过模板提示词）；bocomadp 对齐这一语义，实现
AgentScope 的 ``on_system_prompt`` transformer 钩子：

- 框架每次模型调用前经 ``Agent._get_system_prompt`` 组装 system 提示词
  （config 的 agent 级 system_prompt + skill 指令 + workspace 指令拼接），
  然后**依次应用**实现了 ``on_system_prompt`` 的中间件，返回值为最终
  提示词（transformer 模式，见 ``agentscope/agent/_agent.py``）；
- custom_params 携带非空 ``custom_prompt`` → 直接返回它，**整体覆盖**
  config.yaml 的 agent 级 system_prompt（与 deer-flow 等价）；
- 未携带 / 空串 → 原样返回框架拼好的提示词，零影响。

历史教训：早期版本用 ``on_reply`` 做消息级注入，但该钩子的
``input_kwargs`` 仅含 ``inputs`` / ``structured_schema``（消息在
``_reply_impl`` 内组装），注入逻辑从未生效；``on_system_prompt``
才是提示词覆盖的正确落点。
"""
from __future__ import annotations

import logging
from typing import Any

try:
    from bocomadp.middleware.agent_middleware import MiddlewareBase
except Exception:  # pragma: no cover - agentscope 不可用时降级（如纯单测环境）
    class MiddlewareBase:  # type: ignore
        """最小兜底基类：仅在 AgentScope 不可用时使用，保证可导入。"""

        async def on_system_prompt(self, agent: Any, current_prompt: str) -> str:
            return current_prompt

from bocomadp.deerflow.custom_params import get_custom_params

logger = logging.getLogger(__name__)


class CustomPromptMiddleware(MiddlewareBase):
    """把 custom_params 的 custom_prompt 整体覆盖为 system 提示词。

    ``on_system_prompt`` 是 transformer 模式钩子：框架每次模型调用
    前调用，返回值即最终提示词。ReAct 多轮迭代会重复进入，覆盖行为
    幂等（每轮都返回同一个 custom_prompt），无需去重逻辑。
    """

    async def on_system_prompt(self, agent: Any, current_prompt: str) -> str:
        prompt = str(get_custom_params().get("custom_prompt") or "")
        if prompt:
            if prompt != current_prompt:
                logger.info(
                    "CustomPromptMiddleware: custom_prompt overrides "
                    "system prompt (was %d chars, now %d chars)",
                    len(current_prompt),
                    len(prompt),
                )
            return prompt
        return current_prompt
