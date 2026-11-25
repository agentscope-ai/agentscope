# -*- coding: utf-8 -*-
"""Install purchase row navigation in the resident ERP chat tool factory."""
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from agentscope.app.tool_login import ToolLogin
from agentscope.business import RowActionSource, RowScope
from agentscope.permission import PermissionDecision
from agentscope.tool import FunctionTool

from examples.business_tools.purchase_receipt.authentication import (
    PurchaseAuthentication,
)
from examples.business_tools.purchase_receipt.recent import (
    purchase_receipt_row_tools,
)


def with_purchase_rows(
    previous: Callable[[str, str, str], Awaitable[list[FunctionTool]]],
    auth: PurchaseAuthentication,
    source: RowActionSource,
    database: Path,
    permission: PermissionDecision,
) -> Callable[[str, str, str], Awaitable[list[FunctionTool]]]:
    """Extend chats that already have the purchase list."""
    connection = auth.connection
    tenant = str(connection.context["tenant"])
    account = connection.context["account"]
    base = connection.base_url
    system = f"{base}#{account}"

    async def factory(
        user: str,
        agent: str,
        session: str,
    ) -> list[FunctionTool]:
        existing = await previous(user, agent, session)
        original = next(
            (
                t
                for t in existing
                if t.name == "query_recent_purchase_receipts"
            ),
            None,
        )
        if original is None:
            return existing
        bound = (user, agent, session)

        def scope() -> RowScope:
            if (
                ToolLogin.current_scope() != bound
                or str(connection.context["tenant"]) != tenant
                or connection.context["account"] != account
            ):
                raise PermissionError("当前聊天或 ERP 登录绑定已变化")
            return RowScope(system, tenant, user, session)

        async def check(client: httpx.AsyncClient) -> None:
            response = await client.get("auth/info")
            response.raise_for_status()
            envelope = response.json()
            info = envelope.get("data") or {}
            if (
                envelope.get("code") != 200
                or str(info.get("tenantId")) != tenant
                or info.get("username") != account
            ):
                raise PermissionError("无法确认当前 ERP 身份或账套")
            # Same view/edit/save condition as PurchaseOrderView.handleEdit.
            actions = {
                "purchase:order:view",
                "purchase:order:edit",
                "purchase:order:save",
            }
            if not (
                str(info.get("userType")) == "8"
                or info.get("permissionConfigured") is False
                or actions.intersection(info.get("permissions") or [])
            ):
                raise PermissionError("当前 ERP 用户无权打开采购入库单")

        @asynccontextmanager
        async def client_for(
            owner: RowScope,
        ) -> AsyncIterator[httpx.AsyncClient]:
            read_auth = auth.registered_read_auth()
            if scope() != owner or read_auth is None:
                raise PermissionError("缺少当前会话的 ERP 查询登录适配器")
            async with httpx.AsyncClient(
                base_url=base,
                auth=read_auth,
                trust_env=False,
                follow_redirects=False,
                timeout=15,
            ) as client:
                await check(client)
                yield client
                await check(client)

        rows = purchase_receipt_row_tools(
            source,
            database,
            scope=scope,
            can_read=lambda owner, action: scope() == owner
            and action in {"source", source.list_route, source.detail_route},
            client_for=client_for,
        )

        async def query_recent_purchase_receipts(limit: int = 5) -> dict:
            """按原顺序编号展示最近的采购入库单。保留 list_ref 供后续按行查询。"""
            owner = scope()

            async def present(page: dict) -> dict:
                # The list reader also verifies pending login profiles.
                # Check the current user's ERP rights before exposing its rows.
                async with client_for(owner):
                    return await rows.present_page(owner, page)

            return await auth.query(limit, present=present)

        listing = FunctionTool(
            query_recent_purchase_receipts,
            permission=permission,
            is_read_only=True,
            is_concurrency_safe=False,
        )
        inspect, _, opener = rows.tools()
        return [listing if t is original else t for t in existing] + [
            inspect,
            opener,
        ]

    return factory
