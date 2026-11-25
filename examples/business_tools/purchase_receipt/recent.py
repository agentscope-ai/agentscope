# -*- coding: utf-8 -*-
"""Read recent purchase receipts through the ERP client's list API."""

from collections.abc import Awaitable, Callable

import httpx


def recent_purchase_receipts(
    base_url: str,
    get_token: Callable[[], Awaitable[str]],
) -> Callable[[int], Awaitable[dict]]:
    """Bind the purchase receipt list query to the host's ERP connection."""

    async def query_recent_purchase_receipts(limit: int = 5) -> dict:
        """查看最近的采购单（采购入库单），按 ERP 创建时间倒序。

        Args:
            limit: 要查看的单据条数，默认 5。
        """
        if limit < 1:
            raise ValueError("单据条数必须大于零")

        # PurchaseOrderView.vue loads the list through purchaseOrderApi.page.
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=15,
            trust_env=False,
        ) as client:
            response = await client.get(
                "purchase/order/page",
                params={"current": 1, "size": limit},
                headers={"Authorization": f"Bearer {await get_token()}"},
            )
        response.raise_for_status()
        result = response.json()
        if result.get("code") != 200:
            raise ValueError(result.get("message") or "ERP 采购单查询失败")
        page = result["data"]
        fields = (
            "billNo",
            "billDate",
            "createdTime",
            "supplierName",
            "goodsTotalAmount",
        )
        return {
            "total": page["total"],
            "records": [
                {field: row.get(field) for field in fields}
                for row in page["records"]
            ],
        }

    return query_recent_purchase_receipts
