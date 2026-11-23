# -*- coding: utf-8 -*-
"""Purchase receipt list and detail readers for a host-bound ERP connection."""
import re
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from urllib.parse import quote

import httpx

from agentscope.business import RowActionSource, RowDetailTools, RowScope

DISPLAY_FIELDS = (
    "billNo",
    "billDate",
    "createdTime",
    "supplierName",
    "goodsTotalAmount",
)


async def _data(client: httpx.AsyncClient, path: str, **kwargs: dict) -> dict:
    response = await client.get(path.lstrip("/"), **kwargs)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 200:
        raise ValueError(payload.get("message") or "ERP 采购单查询失败")
    return payload["data"]


def purchase_receipt_row_tools(
    source: RowActionSource,
    database: Path,
    *,
    scope: Callable[[], RowScope],
    can_read: Callable[[RowScope, str], bool],
    client_for: Callable[
        [RowScope],
        AbstractAsyncContextManager[httpx.AsyncClient],
    ],
) -> RowDetailTools:
    """Attach these tools in place of the standalone recent-list tool.

    The host opens a fresh ERP client for the supplied authenticated scope.
    Its base URL includes the API prefix. The source supplies the GET routes.
    """

    async def page(owner: RowScope, limit: int) -> dict:
        async with client_for(owner) as client:
            return await _data(
                client,
                source.list_route,
                params={"current": 1, "size": limit},
            )

    async def detail(owner: RowScope, params: dict) -> dict:
        def segment(match: re.Match) -> str:
            value = str(params[match[1]])
            if value in {".", ".."} or "/" in value or "\\" in value:
                raise ValueError("不支持包含路径分隔符的采购单键")
            return quote(value, safe="")

        path = re.sub(r"\{([^{}]+)\}", segment, source.detail_route)
        async with client_for(owner) as client:
            return await _data(client, path)

    return RowDetailTools(
        source,
        database,
        name="purchase_receipts",
        title="采购入库单",
        field_labels={
            "billNo": "单号",
            "billDate": "单据日期",
            "createdTime": "创建时间",
            "supplierName": "供应商",
            "goodsTotalAmount": "金额",
        },
        visible_fields=DISPLAY_FIELDS,
        scope=scope,
        can_read=can_read,
        list_rows=page,
        read_detail=detail,
    )


def recent_purchase_receipts(
    base_url: str,
    get_token: Callable[[], Awaitable[str]],
) -> Callable[[int], Awaitable[dict]]:
    """Standalone list for legacy hosts without per-session row bindings."""

    async def query_recent_purchase_receipts(limit: int = 5) -> dict:
        """查看最近的采购单（采购入库单），按 ERP 创建时间倒序。

        Args:
            limit: 要查看的单据条数，默认 5。
        """
        if limit < 1:
            raise ValueError("单据条数必须大于零")
        async with httpx.AsyncClient(
            base_url=base_url,
            timeout=15,
            trust_env=False,
            headers={"Authorization": f"Bearer {await get_token()}"},
        ) as client:
            page = await _data(
                client,
                "/purchase/order/page",
                params={"current": 1, "size": limit},
            )
        return {
            "total": page["total"],
            "records": [
                {field: row.get(field) for field in DISPLAY_FIELDS}
                for row in page["records"]
            ],
        }

    return query_recent_purchase_receipts
