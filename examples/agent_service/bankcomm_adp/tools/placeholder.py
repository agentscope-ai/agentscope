# -*- coding: utf-8 -*-
"""企业内部系统工具占位实现。

每个函数都加了完整类型标注与 docstring，``FunctionTool`` 会自动
从中提取工具名、描述、参数 schema，无需手写 OpenAPI spec。

接入真实系统时，把函数体替换为对应的 API 调用即可。
"""
from __future__ import annotations


async def query_employee_info(employee_id: str) -> str:
    """查询员工基本信息（占位）。

    Args:
        employee_id: 员工工号，如 "10001"。

    Returns:
        员工姓名、所属部门、岗位等基本信息。
    """
    # TODO: 替换为行内 HR 系统调用
    return (
        f"[占位] 工号 {employee_id} 的员工信息："
        f"张三，科技部架构组，高级工程师。"
    )


async def query_internal_doc(keyword: str) -> str:
    """在企业内部文档库中检索（占位）。

    Args:
        keyword: 检索关键词。

    Returns:
        匹配到的文档标题与摘要列表。
    """
    # TODO: 替换为行内知识库 / Confluence 检索
    return f"[占位] 关于「{keyword}」检索到 2 篇文档：\n1. 新员工入职指引\n2. 信息安全规范"


async def submit_it_ticket(
    title: str,
    description: str,
    priority: str = "normal",
) -> str:
    """提交一个 IT 工单（占位）。

    Args:
        title: 工单标题。
        description: 问题详细描述。
        priority: 优先级，可选 low / normal / high / urgent，默认 normal。

    Returns:
        工单号。
    """
    # TODO: 替换为行内 ITSM 系统调用
    return f"[占位] 已提交工单，工单号 IT-2026-{abs(hash(title)) % 100000:05d}"
