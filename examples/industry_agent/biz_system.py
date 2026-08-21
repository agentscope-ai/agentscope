# -*- coding: utf-8 -*-
"""
模拟第三方业务系统
=================

包含：
- 用户数据（user_id, name, department）
- 权限数据（每个用户的工具权限和数据权限）
- 业务数据（订单、客户、发票）
"""
import json
from typing import Any


# ──────────────────────────────────────────────
# 1. 用户数据
# ──────────────────────────────────────────────

USERS = {
    # 销售部门
    "user_sales": {
        "id": "user_sales",
        "name": "张三（销售）",
        "department": "销售部",
    },
    "user_sales_2": {
        "id": "user_sales_2",
        "name": "张四（销售）",
        "department": "销售部",
    },
    "user_sales_3": {
        "id": "user_sales_3",
        "name": "张五（销售）",
        "department": "销售部",
    },
    # 财务部门
    "user_finance": {
        "id": "user_finance",
        "name": "李四（财务）",
        "department": "财务部",
    },
    "user_finance_2": {
        "id": "user_finance_2",
        "name": "李五（财务）",
        "department": "财务部",
    },
    # 管理部门
    "user_admin": {
        "id": "user_admin",
        "name": "王五（管理员）",
        "department": "管理部",
    },
    # HR部门
    "user_hr": {
        "id": "user_hr",
        "name": "赵六（HR）",
        "department": "人力资源部",
    },
    "user_hr_2": {
        "id": "user_hr_2",
        "name": "赵七（HR）",
        "department": "人力资源部",
    },
    # 技术部门
    "user_tech": {
        "id": "user_tech",
        "name": "孙七（技术）",
        "department": "技术部",
    },
    "user_tech_2": {
        "id": "user_tech_2",
        "name": "孙八（技术）",
        "department": "技术部",
    },
    # 市场部门
    "user_marketing": {
        "id": "user_marketing",
        "name": "周八（市场）",
        "department": "市场部",
    },
    "user_marketing_2": {
        "id": "user_marketing_2",
        "name": "周九（市场）",
        "department": "市场部",
    },
    # 客服部门
    "user_support": {
        "id": "user_support",
        "name": "吴九（客服）",
        "department": "客服部",
    },
    "user_support_2": {
        "id": "user_support_2",
        "name": "吴十（客服）",
        "department": "客服部",
    },
    # 物流部门
    "user_logistics": {
        "id": "user_logistics",
        "name": "郑十（物流）",
        "department": "物流部",
    },
    "user_logistics_2": {
        "id": "user_logistics_2",
        "name": "郑十一（物流）",
        "department": "物流部",
    },
}


# ──────────────────────────────────────────────
# 2. 权限数据
# ──────────────────────────────────────────────

# 工具权限：控制用户能用的工具组
# 分组定义见 main.py 的 TOOL_GROUPS
TOOL_PERMISSIONS = {
    # 销售：订单 + 客户
    "user_sales": ["order", "customer"],
    "user_sales_2": ["order", "customer"],
    "user_sales_3": ["order", "customer"],
    # 财务：订单 + 发票 + 报表
    "user_finance": ["order", "invoice", "report"],
    "user_finance_2": ["order", "invoice", "report"],
    # 管理：全部
    "user_admin": ["order", "customer", "invoice", "report"],
    # HR：客户
    "user_hr": ["customer"],
    "user_hr_2": ["customer"],
    # 技术：订单
    "user_tech": ["order"],
    "user_tech_2": ["order"],
    # 市场：客户 + 发票
    "user_marketing": ["customer", "invoice"],
    "user_marketing_2": ["customer", "invoice"],
    # 客服：订单 + 客户
    "user_support": ["order", "customer"],
    "user_support_2": ["order", "customer"],
    # 物流：订单
    "user_logistics": ["order"],
    "user_logistics_2": ["order"],
}

# 数据权限：控制用户能看哪些数据
DATA_PERMISSIONS = {
    "user_sales": {
        "order_filter": lambda o: o["salesperson"] == "张三",  # 只看自己的订单
        "customer_filter": lambda c: True,  # 看所有客户
    },
    "user_finance": {
        "order_filter": lambda o: True,  # 看所有订单
        "invoice_filter": lambda i: i["amount"] < 100000,  # 只看 < 10万的发票
    },
    "user_admin": {
        "order_filter": lambda o: True,  # 看所有订单
        "customer_filter": lambda c: True,  # 看所有客户
        "invoice_filter": lambda i: True,  # 看所有发票
    },
    "user_hr": {
        "customer_filter": lambda c: c["level"] == "VIP",  # 只看 VIP 客户
    },
    "user_tech": {
        "order_filter": lambda o: o["status"] == "已完成",  # 只看已完成的订单
    },
    "user_marketing": {
        "customer_filter": lambda c: True,  # 看所有客户
        "invoice_filter": lambda i: i["amount"] >= 50000,  # 只看 >= 5万的发票
    },
    "user_support": {
        "order_filter": lambda o: o["status"] in ["进行中", "待确认"],  # 只看进行中和待确认的订单
        "customer_filter": lambda c: True,  # 看所有客户
    },
    "user_logistics": {
        "order_filter": lambda o: o["status"] == "已完成",  # 只看已完成的订单
    },
}


# ──────────────────────────────────────────────
# 3. 业务数据
# ──────────────────────────────────────────────

ORDERS = [
    {"id": "A001", "customer": "华为", "amount": 50000, "salesperson": "张三", "status": "已完成"},
    {"id": "A002", "customer": "腾讯", "amount": 80000, "salesperson": "张三", "status": "进行中"},
    {"id": "A003", "customer": "阿里", "amount": 120000, "salesperson": "李四", "status": "已完成"},
    {"id": "A004", "customer": "百度", "amount": 30000, "salesperson": "王五", "status": "待确认"},
    {"id": "A005", "customer": "字节", "amount": 200000, "salesperson": "张三", "status": "已完成"},
]

CUSTOMERS = [
    {"id": "C001", "name": "华为", "contact": "张经理", "phone": "13800138001", "level": "VIP"},
    {"id": "C002", "name": "腾讯", "contact": "李经理", "phone": "13800138002", "level": "VIP"},
    {"id": "C003", "name": "阿里", "contact": "王经理", "phone": "13800138003", "level": "普通"},
    {"id": "C004", "name": "百度", "contact": "赵经理", "phone": "13800138004", "level": "普通"},
    {"id": "C005", "name": "字节", "contact": "刘经理", "phone": "13800138005", "level": "VIP"},
]

INVOICES = [
    {"id": "I001", "order_id": "A001", "amount": 50000, "date": "2026-06-01", "status": "已开票"},
    {"id": "I002", "order_id": "A002", "amount": 80000, "date": "2026-06-15", "status": "待开票"},
    {"id": "I003", "order_id": "A003", "amount": 120000, "date": "2026-06-20", "status": "已开票"},
    {"id": "I004", "order_id": "A004", "amount": 30000, "date": "2026-07-01", "status": "已开票"},
    {"id": "I005", "order_id": "A005", "amount": 200000, "date": "2026-07-02", "status": "待开票"},
]


# ──────────────────────────────────────────────
# 4. 业务系统 API（模拟）
# ──────────────────────────────────────────────

def get_user_info(user_id: str) -> dict[str, Any] | None:
    """获取用户信息。"""
    return USERS.get(user_id)


def get_tool_permissions(user_id: str) -> list[str]:
    """获取用户的工具权限。"""
    return TOOL_PERMISSIONS.get(user_id, [])


def get_data_permissions(user_id: str) -> dict:
    """获取用户的数据权限（过滤函数）。"""
    return DATA_PERMISSIONS.get(user_id, {})


def query_orders_from_db() -> list[dict]:
    """从数据库查询所有订单（模拟）。"""
    return ORDERS.copy()


def query_customers_from_db() -> list[dict]:
    """从数据库查询所有客户（模拟）。"""
    return CUSTOMERS.copy()


def query_invoices_from_db() -> list[dict]:
    """从数据库查询所有发票（模拟）。"""
    return INVOICES.copy()


# ──────────────────────────────────────────────
# 5. 辅助函数
# ──────────────────────────────────────────────

def filter_by_permission(data: list[dict], filter_func) -> list[dict]:
    """用过滤函数过滤数据。"""
    if filter_func is None:
        return data
    return [item for item in data if filter_func(item)]
