# -*- coding: utf-8 -*-
"""Compile and execute the purchase receipt lookup by document number."""

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx

from agentscope.business._business import bind_operation, write_definition


def compile_lookup(
    contract: dict,
    output: Path,
    detail: dict,
    list_route: str,
    detail_route: str,
    number_field: str,
    exact_operator: str,
) -> dict:
    """Validate a lookup plan against the bound ERP operations and publish
    it."""
    if (
        list_route != "/purchase/order/page"
        or detail_route != detail["operation"]["route"]
        or number_field != "billNo"
        or exact_operator != "eq"
    ):
        raise ValueError("计划与采购入库单的列表、详情或单号精确匹配规则不一致")
    operation = bind_operation(
        contract,
        {"method": "GET", "template": list_route},
    )
    params = {p["name"] for p in operation["parameters"] if p["in"] == "query"}
    if not {"current", "size", "filters", number_field} <= params:
        raise ValueError("ERP OpenAPI 缺少采购单分页或单号查询参数")

    plan = {
        "listRoute": list_route,
        "detailRoute": detail_route,
        "numberField": number_field,
        "exactOperator": exact_operator,
    }
    write_definition(output, plan)
    return {
        "tool": "get_purchase_receipt_by_number",
        "available": "原会话下一条消息",
        "plan": plan,
    }


def lookup_purchase_receipt(
    plan: dict,
    base_url: str,
    get_token: Callable[[], Awaitable[str]],
    get_detail: Callable[[int], Awaitable[dict]],
) -> Callable[[str], Awaitable[dict]]:
    """Bind a compiled lookup plan to the same APIs used by the client."""
    number_field = plan["numberField"]

    def summary(row: dict) -> dict:
        return {
            field: row.get(field)
            for field in (
                "id",
                number_field,
                "billDate",
                "supplierName",
                "companyName",
            )
        }

    async def get_purchase_receipt_by_number(bill_no: str) -> dict:
        """按完整入库单号查询采购入库单。唯一精确命中时返回表头、明细和付款。

        Args:
            bill_no: 用户提供的采购入库单号。
        """
        bill_no = bill_no.strip()
        if not bill_no:
            raise ValueError("请输入采购入库单号")

        async with httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {await get_token()}"},
            timeout=15,
            trust_env=False,
        ) as client:

            async def read_page(filters: dict) -> dict:
                response = await client.get(
                    plan["listRoute"].lstrip("/"),
                    params={"current": 1, "size": 20, **filters},
                )
                response.raise_for_status()
                payload = response.json()
                if payload.get("code") != 200:
                    raise ValueError(payload.get("message") or "ERP 采购单查询失败")
                return payload["data"]

            page = await read_page(
                {
                    "filters": json.dumps(
                        [
                            {
                                "field": number_field,
                                "operator": plan["exactOperator"],
                                "value": bill_no,
                            },
                        ],
                        ensure_ascii=False,
                    ),
                },
            )
            records = {row["id"]: row for row in page["records"]}
            if any(
                row[number_field].casefold() != bill_no.casefold()
                for row in records.values()
            ):
                raise ValueError("ERP 单号精确筛选返回了其他单号，已停止回查")
            if page["total"] > 1 or len(records) > 1:
                return {
                    "status": "ambiguous",
                    "billNo": bill_no,
                    "total": page["total"],
                    "candidates": [summary(row) for row in records.values()],
                }
            if page["total"] == 1:
                if len(records) != 1:
                    raise ValueError("ERP 列表计数与返回记录不一致")
                receipt_id = next(iter(records))
                detail = await get_detail(receipt_id)
                if (
                    detail["order"][number_field].casefold()
                    != bill_no.casefold()
                ):
                    raise ValueError("ERP 详情的单号与查询结果不一致")
                return {"status": "found", "billNo": bill_no, "detail": detail}
            if records:
                raise ValueError("ERP 列表计数与返回记录不一致")

            fuzzy = await read_page({number_field: bill_no})
            candidates = {row["id"]: summary(row) for row in fuzzy["records"]}
            return {
                "status": "partial" if candidates else "not_found",
                "billNo": bill_no,
                "total": fuzzy["total"],
                "candidates": list(candidates.values()),
            }

    return get_purchase_receipt_by_number
