# -*- coding: utf-8 -*-
"""
自定义中间件
============

实现数据权限过滤：
- DataPermissionMiddleware: 根据用户权限过滤工具返回的数据
"""
import json
import sys
import os
from typing import AsyncGenerator

# 添加当前目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import biz_system

from agentscope.middleware import MiddlewareBase


def _safe_print(msg: str) -> None:
    """Print with UTF-8 encoding to avoid GBK errors on Windows."""
    sys.stdout.buffer.write((msg + "\n").encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


class DataPermissionMiddleware(MiddlewareBase):
    """根据用户权限过滤工具返回的数据。

    拦截 on_acting 阶段，在工具执行完成后过滤返回的数据。
    """

    async def on_acting(
        self,
        agent,
        input_kwargs: dict,
        next_handler,
    ) -> AsyncGenerator:
        """拦截工具执行，过滤返回数据。"""
        tool_call = input_kwargs.get("tool_call")
        tool_name = tool_call.name if tool_call else ""

        # 执行工具，获取结果
        async for chunk in next_handler():
            # 如果是文本结果，尝试过滤
            if hasattr(chunk, "text") and chunk.text:
                try:
                    # 尝试解析为 JSON
                    data = json.loads(chunk.text)

                    # 根据工具类型应用过滤
                    if isinstance(data, list):
                        filtered_data = self._filter_data(tool_name, data, agent)
                        chunk.text = json.dumps(filtered_data, ensure_ascii=False)
                except (json.JSONDecodeError, AttributeError):
                    # 不是 JSON，不过滤
                    pass

            yield chunk

    def _filter_data(self, tool_name: str, data: list, agent) -> list:
        """根据工具名称和用户权限过滤数据。"""
        # 从 agent 获取 user_id（这里简化，实际从 state 获取）
        # 实际实现需要从 agent.state 或 session 获取 user_id
        # 这里演示用固定逻辑

        # 获取数据权限过滤函数
        # 注意：实际需要从 agent 的上下文获取 user_id
        # 这里简化处理，假设 agent.name 包含 user_id 信息
        user_id = self._extract_user_id(agent)
        if not user_id:
            return data

        permissions = biz_system.get_data_permissions(user_id)

        if tool_name == "query_order":
            filter_func = permissions.get("order_filter")
            return biz_system.filter_by_permission(data, filter_func)
        elif tool_name == "query_customer":
            filter_func = permissions.get("customer_filter")
            return biz_system.filter_by_permission(data, filter_func)
        elif tool_name == "query_invoice":
            filter_func = permissions.get("invoice_filter")
            return biz_system.filter_by_permission(data, filter_func)

        return data

    def _extract_user_id(self, agent) -> str:
        """从 agent 提取 user_id。

        实际实现应该从 agent.state 或 session 获取。
        这里简化处理。
        """
        # 简化：从 agent 的某个属性提取
        # 实际应该从 session 或 state 获取
        # 这里返回 None，让过滤逻辑跳过
        return None


class AuditMiddleware(MiddlewareBase):
    """审计日志中间件，记录所有工具调用。"""

    async def on_acting(
        self,
        agent,
        input_kwargs: dict,
        next_handler,
    ) -> AsyncGenerator:
        """记录工具调用。"""
        tool_call = input_kwargs.get("tool_call")
        tool_name = tool_call.name if tool_call else "unknown"
        tool_input = tool_call.input if tool_call else "{}"

        _safe_print(f"\n[AUDIT] 工具调用: {tool_name}")
        _safe_print(f"[AUDIT] 参数: {tool_input}")

        async for chunk in next_handler():
            yield chunk

        _safe_print(f"[AUDIT] 工具 {tool_name} 执行完成")
