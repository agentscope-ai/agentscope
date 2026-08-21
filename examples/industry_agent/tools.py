# -*- coding: utf-8 -*-
"""
行业自定义工具
==============

按功能分组定义工具函数：
- [订单组]: query_order, query_order_statistics, query_customer_orders
- [客户组]: query_customer
- [发票组]: query_invoice, query_invoice_by_order
- [报表组]: query_sales_performance
"""
import json
import sys
import os

# 添加当前目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import biz_system


# ═══════════════════════════════════════════════
# 1. [订单组] 订单查询与分析
# ═══════════════════════════════════════════════

def query_order(order_id: str = "") -> str:
    """查询订单信息。

    Args:
        order_id: 订单ID，如 "A001"。留空则查询所有订单。

    Returns:
        订单信息的 JSON 字符串。
    """
    orders = biz_system.query_orders_from_db()

    if order_id:
        orders = [o for o in orders if o["id"] == order_id]
        if not orders:
            return json.dumps({"error": f"订单 {order_id} 不存在"}, ensure_ascii=False)

    return json.dumps(orders, ensure_ascii=False)


def query_order_statistics() -> str:
    """查询订单统计信息。

    返回总订单数、总金额、平均金额、各状态分布。

    Returns:
        统计信息的 JSON 字符串。
    """
    orders = biz_system.query_orders_from_db()
    if not orders:
        return json.dumps({"error": "暂无订单数据"}, ensure_ascii=False)

    total_amount = sum(o["amount"] for o in orders)
    status_counts = {}
    for o in orders:
        s = o["status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    return json.dumps({
        "total_orders": len(orders),
        "total_amount": total_amount,
        "avg_amount": round(total_amount / len(orders), 2),
        "status_distribution": status_counts,
    }, ensure_ascii=False)


def query_customer_orders(customer_name: str) -> str:
    """查询指定客户的所有订单。

    Args:
        customer_name: 客户名称，如 "华为"。

    Returns:
        该客户所有订单的 JSON 字符串。
    """
    orders = biz_system.query_orders_from_db()
    matched = [o for o in orders if customer_name in o["customer"]]
    if not matched:
        return json.dumps(
            {"error": f"未找到客户 {customer_name} 的订单"}, ensure_ascii=False,
        )

    total = sum(o["amount"] for o in matched)
    return json.dumps({
        "customer": customer_name,
        "order_count": len(matched),
        "total_amount": total,
        "orders": matched,
    }, ensure_ascii=False)


# ═══════════════════════════════════════════════
# 2. [客户组] 客户查询
# ═══════════════════════════════════════════════

def query_customer(customer_name: str = "") -> str:
    """查询客户信息。

    Args:
        customer_name: 客户名称，如 "华为"。留空则查询所有客户。

    Returns:
        客户信息的 JSON 字符串。
    """
    customers = biz_system.query_customers_from_db()

    if customer_name:
        customers = [c for c in customers if customer_name in c["name"]]
        if not customers:
            return json.dumps({"error": f"客户 {customer_name} 不存在"}, ensure_ascii=False)

    return json.dumps(customers, ensure_ascii=False)


# ═══════════════════════════════════════════════
# 3. [发票组] 发票查询
# ═══════════════════════════════════════════════

def query_invoice(invoice_id: str = "") -> str:
    """查询发票信息。

    Args:
        invoice_id: 发票ID，如 "I001"。留空则查询所有发票。

    Returns:
        发票信息的 JSON 字符串。
    """
    invoices = biz_system.query_invoices_from_db()

    if invoice_id:
        invoices = [i for i in invoices if i["id"] == invoice_id]
        if not invoices:
            return json.dumps({"error": f"发票 {invoice_id} 不存在"}, ensure_ascii=False)

    return json.dumps(invoices, ensure_ascii=False)


def query_invoice_by_order(order_id: str) -> str:
    """根据订单ID查询关联的发票。

    Args:
        order_id: 订单ID，如 "A001"。

    Returns:
        关联发票信息的 JSON 字符串。
    """
    invoices = biz_system.query_invoices_from_db()
    matched = [i for i in invoices if i["order_id"] == order_id]
    if not matched:
        return json.dumps(
            {"error": f"订单 {order_id} 没有关联发票"}, ensure_ascii=False,
        )
    return json.dumps(matched, ensure_ascii=False)


# ═══════════════════════════════════════════════
# 4. [报表组] 业绩与汇总
# ═══════════════════════════════════════════════

def query_sales_performance() -> str:
    """查询销售业绩汇总。

    返回每个销售员的订单数、总金额、订单列表。

    Returns:
        业绩汇总的 JSON 字符串。
    """
    orders = biz_system.query_orders_from_db()
    performance = {}
    for o in orders:
        sp = o["salesperson"]
        if sp not in performance:
            performance[sp] = {"order_count": 0, "total_amount": 0, "orders": []}
        performance[sp]["order_count"] += 1
        performance[sp]["total_amount"] += o["amount"]
        performance[sp]["orders"].append(o["id"])

    return json.dumps(performance, ensure_ascii=False)
