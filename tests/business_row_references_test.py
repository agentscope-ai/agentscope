# -*- coding: utf-8 -*-
"""Source-backed row selection across two different business applications."""
import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

import httpx
from utils import MockModel

from agentscope.agent import Agent, ContextConfig
from agentscope.app.middleware._row_card import RowCardMiddleware
from agentscope.business import RowActionSource, RowDetailTools, RowScope
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.tool import Toolkit
from examples.business_tools.purchase_receipt.recent import (
    purchase_receipt_row_tools,
)

FIXTURES = Path(__file__).parent / "fixtures/row_actions"


def contract(path: str, key_type: str, params: tuple[str, ...]) -> dict:
    """A minimal GET-only detail contract."""
    return {
        "get": {
            "operationId": path,
            "parameters": [
                {
                    "in": "path",
                    "name": name,
                    "required": True,
                    "schema": {"type": key_type},
                }
                for name in params
            ],
            "responses": {"200": {"description": "Success"}},
        },
    }


class BusinessRowReferencesTest(IsolatedAsyncioTestCase):
    """Use identical display values and inspect the actual detail operation."""

    def setUp(self) -> None:
        # The TestCase owns this context until all async calls finish.
        # pylint: disable-next=consider-using-with
        self.root = Path(self.enterContext(TemporaryDirectory()))
        shutil.copytree(FIXTURES, self.root, dirs_exist_ok=True)
        self.database = self.root / "selection.db"
        self.api = self.root / "openapi.json"
        self.paths = {
            "/purchase/order/page": contract("purchases", "integer", ()),
            "/purchase/order/{id}": contract("purchase", "integer", ("id",)),
            "/desk/inbox": contract("inbox", "string", ()),
            "/desk/cases/{key}": contract("ticket", "string", ("key",)),
        }
        self.write_api()
        self.purchase = RowActionSource(
            self.root,
            self.api,
            "Purchase.vue",
            "handleEdit",
            "/purchase/order/page",
            "/purchase/order/{id}",
        )
        self.ticket = RowActionSource(
            self.root,
            self.api,
            "Tickets.vue",
            "showTicket",
            "/desk/inbox",
            "/desk/cases/{key}",
        )
        self.viewer = RowScope("connector", "tenant-a", "alice", "chat")
        self.allowed = {
            "source",
            self.purchase.list_route,
            self.purchase.detail_route,
            self.ticket.list_route,
            self.ticket.detail_route,
        }
        self.calls: list = []
        self.rows: list = []

    def write_api(self) -> None:
        """Write the current local GET contract."""
        self.api.write_text(
            json.dumps({"openapi": "3.1.0", "paths": self.paths}),
            encoding="utf-8",
        )

    def host(self, source: RowActionSource) -> RowDetailTools:
        """Attach both mock systems to the same runtime."""

        def can_read(viewer: RowScope, action: str) -> bool:
            return viewer == self.viewer and action in self.allowed

        if source == self.purchase:

            def send(req: httpx.Request) -> httpx.Response:
                self.assertEqual(req.method, "GET")
                self.calls.append(req.url.path)
                if req.url.path.endswith("/page"):
                    limit = int(req.url.params["size"])
                    data = {
                        "records": self.rows[:limit],
                        "total": len(self.rows),
                    }
                else:
                    key = int(req.url.path.rsplit("/", 1)[1])
                    row = next(r for r in self.rows if r.get("id") == key)
                    data = {
                        "details": row.get("details", []),
                        "ord": req.url.path,
                    }
                return httpx.Response(200, json={"code": 200, "data": data})

            return purchase_receipt_row_tools(
                source,
                self.database,
                scope=lambda: self.viewer,
                can_read=can_read,
                client_for=lambda _: httpx.AsyncClient(
                    base_url="https://fake.invalid/api/",
                    transport=httpx.MockTransport(send),
                ),
            )

        async def page(owner: RowScope, limit: int) -> dict:
            self.assertEqual(owner, self.viewer)
            self.calls.append(source.list_route)
            return {"records": self.rows[:limit], "total": len(self.rows)}

        async def detail(owner: RowScope, args: dict) -> dict:
            self.assertEqual(owner, self.viewer)
            self.calls.append(args)
            row = next(
                r for r in self.rows if r.get("ticketKey") == args["key"]
            )
            return {"resolved": args, "details": row.get("details", [])}

        return RowDetailTools(
            source,
            self.database,
            name="tickets",
            title="工单",
            field_labels={"subject": "主题"},
            visible_fields=("subject",),
            list_rows=page,
            read_detail=detail,
            scope=lambda: self.viewer,
            can_read=can_read,
        )

    async def test_same_display_and_snapshot_selection(self) -> None:
        """A fresh worker selects the original second row after a new list."""
        for source, field, keys, expected in (
            (self.purchase, "id", (101, 202, 303), "/api/purchase/order/202"),
            (self.ticket, "ticketKey", ("001", "002", "003"), {"key": "002"}),
        ):
            with self.subTest(system=source.page):
                first, second, new = keys
                self.calls.clear()
                self.rows = [
                    {
                        field: n,
                        "billNo": "同号",
                        "subject": "重复工单",
                        "supplierName": "同一供应商",
                        "details": ["第一条明细" if n == first else "第二条明细"],
                    }
                    for n in (first, second)
                ]
                host = self.host(source)
                plan = await host.inspect_row_action()
                self.assertEqual(
                    list(plan["fields"].values())[0]["path"],
                    [field],
                )
                for ref in plan["evidence"]:
                    span = (
                        (self.root / ref["file"])
                        .read_text("utf-8")
                        .splitlines()
                    )
                    self.assertIn(
                        ref["code"],
                        "\n".join(span[ref["start"] - 1 : ref["end"]]),
                    )
                shown = await host.list_recent(2)
                self.assertEqual(shown["records"][0], shown["records"][1])
                self.assertNotIn(field, shown["records"][0])
                self.rows.insert(0, {**self.rows[0], field: new})
                refreshed = await host.list_recent(2)
                self.assertNotEqual(shown["list_ref"], refreshed["list_ref"])
                opened = await self.host(source).open_row(shown["list_ref"], 2)
                self.assertEqual(opened["details"], ["第二条明细"])
                self.assertIn(expected, opened.values())
                self.assertEqual(self.calls[-1], expected)
                self.assertEqual(len(self.calls), 3)

    async def test_agent_followup_uses_previous_tool_result(self) -> None:
        """Exercise the registered tools and the previous turn's actual ref."""

        def call(name: str, **args: object) -> ChatResponse:
            block = ToolCallBlock(id=name, name=name, input=json.dumps(args))
            return ChatResponse(content=[block], is_last=True)

        done = ChatResponse(content=[TextBlock(text="完成")], is_last=True)
        for source in (self.purchase, self.ticket):
            with self.subTest(source=source.page):
                self.rows = [
                    {"id": 1, "ticketKey": "T1", "details": ["A"]},
                    {"id": 2, "ticketKey": "T2", "details": ["B"]},
                ]
                self.calls.clear()
                tools = self.host(source).tools()
                inspect, listing, opener = tools
                model = MockModel(context_size=16000)
                agent = Agent(
                    name="reader",
                    system_prompt="根据工具返回的原列表和行号打开详情。",
                    model=model,
                    toolkit=Toolkit(tools=tools),
                    context_config=ContextConfig(
                        compression_tool_enabled=False,
                    ),
                )
                model.set_responses(
                    [call(inspect.name), call(listing.name, limit=2), done],
                )
                await agent.reply(UserMsg("alice", "检查源码，然后展示最近两张"))
                results = agent.state.get_reply_message(
                    agent.name,
                ).get_content_blocks("tool_result")
                shown = json.loads(results[-1].output[0].text)
                self.assertEqual(shown["records"][0], shown["records"][1])
                self.rows.insert(
                    0,
                    {"id": 3, "ticketKey": "T3", "details": ["C"]},
                )
                model.set_responses(
                    [
                        call(
                            opener.name,
                            list_ref=shown["list_ref"],
                            row_number=2,
                        ),
                        done,
                    ],
                )
                await agent.reply(UserMsg("alice", "打开刚才第二张"))
                (result,) = agent.state.get_reply_message(
                    agent.name,
                ).get_content_blocks("tool_result")
                self.assertEqual(
                    json.loads(result.output[0].text)["details"],
                    ["B"],
                )
                self.assertEqual(len(self.calls), 2)

    async def test_quoted_cards_use_the_persisted_message_and_tool_result(
        self,
    ) -> None:
        """Run two list turns, both old-card actions and failure cases."""
        for source in (self.purchase, self.ticket):
            with self.subTest(system=source.page):
                history = {}
                lookup = AsyncMock(side_effect=history.get)
                model = MockModel(context_size=32000)
                tools = self.host(source).tools()
                agent = Agent(
                    name="reader",
                    system_prompt="展示业务列表。",
                    model=model,
                    toolkit=Toolkit(tools=tools),
                    middlewares=[RowCardMiddleware(lookup)],
                    context_config=ContextConfig(
                        compression_tool_enabled=False,
                    ),
                )
                self.rows = [
                    {"id": 1, "ticketKey": "T1", "details": ["first"]},
                    {"id": 2, "ticketKey": "T2", "details": ["original"]},
                ]
                self.calls.clear()
                quotes = []
                for i in range(2):
                    model.set_responses(
                        [
                            ChatResponse(
                                content=[
                                    ToolCallBlock(
                                        id=f"list-{i}",
                                        name=tools[1].name,
                                        input='{"limit":2}',
                                    ),
                                ],
                                is_last=True,
                            ),
                            ChatResponse(
                                content=[TextBlock(text="已查询")],
                                is_last=True,
                            ),
                        ],
                    )
                    await agent.reply(UserMsg("alice", "查最新两张"))
                    message = agent.state.get_reply_message(agent.name)
                    history[message.id] = message.model_copy(deep=True)
                    (result,) = message.get_content_blocks("tool_result")
                    page = json.loads(result.output[0].text)
                    self.assertEqual(page["records"][0], page["records"][1])
                    self.assertTrue(page["row_card"]["columns"])
                    quotes.append(
                        {"message_id": message.id, "tool_call_id": result.id},
                    )
                    if i == 0:
                        old_ref = page["list_ref"]
                        self.rows.insert(
                            0,
                            {"id": 3, "ticketKey": "T3", "details": ["new"]},
                        )

                async def open_quote(
                    quote: dict,
                    text: str,
                    reader: Agent = agent,
                ) -> str:
                    await reader.reply(
                        UserMsg(
                            "alice",
                            text,
                            metadata={"row_card_reply": quote},
                        ),
                    )
                    return reader.state.get_reply_message(
                        reader.name,
                    ).get_text_content()

                for text in ("打开第 2 行", "打开第二张"):
                    self.assertIn(
                        "original",
                        await open_quote(quotes[0], text),
                    )
                self.assertIn(
                    "first",
                    await open_quote(quotes[1], "Open row 2"),
                )
                self.assertEqual(len(self.calls), 5)
                for bad in (
                    dict(quotes[0], message_id="absent"),
                    dict(quotes[0], tool_call_id="list-1"),
                ):
                    self.assertIn("找不到", await open_quote(bad, "打开第二张"))
                self.assertIn("指定", await open_quote(quotes[0], "打开哪张？"))
                original = self.viewer
                self.viewer = replace(original, tenant="tenant-b")
                self.assertIn("无法打开", await open_quote(quotes[0], "打开第二张"))
                self.viewer = original
                self.allowed.remove(source.detail_route)
                self.assertIn("无法打开", await open_quote(quotes[0], "打开第二张"))
                self.allowed.add(source.detail_route)
                with closing(sqlite3.connect(self.database)) as connection:
                    connection.execute(
                        "UPDATE row_lists SET expires=0 WHERE ref=?",
                        (old_ref,),
                    )
                    connection.commit()
                self.assertIn("无法打开", await open_quote(quotes[0], "打开第二张"))
                self.assertEqual(len(self.calls), 5)

    async def test_account_permission_and_session_isolation(self) -> None:
        """Block mismatched identities and revoked read permissions."""
        for source in (self.ticket, self.purchase):
            with self.subTest(source=source.page):
                self.rows = [{"ticketKey": "007", "id": 7}]
                host = self.host(source)
                ref = (await host.list_recent())["list_ref"]
                viewer = self.viewer
                before = list(self.calls)
                for field in ("tenant", "user", "session", "system"):
                    self.viewer = replace(viewer, **{field: "other"})
                    with self.assertRaisesRegex(ValueError, "失效"):
                        await host.open_row(ref, 1)
                self.viewer = viewer
                for permission in (source.detail_route, source.list_route):
                    self.allowed.remove(permission)
                    with self.assertRaises(PermissionError):
                        await host.open_row(ref, 1)
                    self.allowed.add(permission)
                self.assertEqual(self.calls, before)

    async def test_revoked_during_detail_and_deleted_record(self) -> None:
        """Honor object deletion and a revocation during the GET."""
        self.rows = [{"ticketKey": "T100"}]
        host = self.host(self.ticket)
        ref = (await host.list_recent())["list_ref"]

        async def revoke(owner: RowScope, args: dict) -> dict:
            self.assertEqual((owner, args), (self.viewer, {"key": "T100"}))
            self.allowed.remove(self.ticket.detail_route)
            return {"secret": "restricted"}

        async def deleted(owner: RowScope, args: dict) -> dict:
            self.assertEqual((owner, args), (self.viewer, {"key": "T100"}))
            raise LookupError("deleted")

        host.read_detail = revoke
        with self.assertRaises(PermissionError):
            await host.open_row(ref, 1)
        self.allowed.add(self.ticket.detail_route)
        host.read_detail = deleted
        with self.assertRaisesRegex(LookupError, "deleted"):
            await host.open_row(ref, 1)
        self.assertEqual(self.calls, [self.ticket.list_route])

    async def test_nested_composite_keys_and_changed_source(self) -> None:
        """Two hidden source fields can supply differently named parameters."""
        view = self.root / self.ticket.page
        before = view.read_text("utf-8")
        self.rows = [{"ticketKey": "T-01", "meta": {"board": "B-02"}}]
        old_ref = (await self.host(self.ticket).list_recent())["list_ref"]
        view.write_text(
            before.replace(
                "entry.ticketKey",
                "entry.meta.board, entry.ticketKey",
            ),
            encoding="utf-8",
        )
        service = self.root / "service.ts"
        service.write_text(
            service.read_text("utf-8")
            .replace("(lookup)", "(board, lookup)")
            .replace(
                "/desk/cases/${lookup}",
                "/desk/${board}/cases/${lookup}",
            ),
            encoding="utf-8",
        )
        source = replace(
            self.ticket,
            detail_route="/desk/{project}/cases/{key}",
        )
        self.paths[source.detail_route] = contract(
            "ticketByBoard",
            "string",
            ("project", "key"),
        )
        self.write_api()
        self.allowed.add(source.detail_route)
        host = self.host(source)
        with self.assertRaises(ValueError):
            await host.open_row(old_ref, 1)
        shown = await host.list_recent()
        self.assertEqual(
            (await host.open_row(shown["list_ref"], 1))["resolved"],
            {"project": "B-02", "key": "T-01"},
        )
        view.write_text(
            view.read_text("utf-8").replace(
                "entry.ticketKey",
                "entry.otherKey",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "变化"):
            await host.open_row(shown["list_ref"], 1)

    async def test_missing_key_and_unproved_source(self) -> None:
        """Missing, lossy or unproven keys cannot trigger a detail request."""
        self.rows = [{"billNo": "B"}, {"id": 2**53 + 1}]
        host = self.host(self.purchase)
        shown = await host.list_recent()
        for n, message in ((1, "缺少"), (2, "无损"), (3, "超出"), (0, "从 1")):
            with self.assertRaisesRegex(ValueError, message):
                await host.open_row(shown["list_ref"], n)
        self.assertEqual(self.calls, ["/api/purchase/order/page"])
        view = self.root / self.ticket.page
        view.write_text(
            view.read_text("utf-8").replace(
                "entry.ticketKey",
                "environment.ticketKey",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "字段证据"):
            await self.host(self.ticket).inspect_row_action()

    async def test_unrelated_list_and_extra_request_fields_are_rejected(
        self,
    ) -> None:
        """Require the bound list result and the entire detail request."""
        page = self.root / self.ticket.page
        raw = page.read_text("utf-8")
        page.write_text(
            raw.replace(
                "tickets.value = inbox.items;",
                "tickets.value = otherRows;",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "列表 GET"):
            await self.host(self.ticket).inspect_row_action()
        page.write_text(
            raw.replace(
                "return api.read(entry.ticketKey);",
                "entry.ticketKey = nextKey; return api.read(entry.ticketKey);",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "改写"):
            await self.host(self.ticket).inspect_row_action()
        page.write_text(raw, encoding="utf-8")
        service = self.root / "service.ts"
        service.write_text(
            service.read_text("utf-8").replace(
                "cases/${lookup}`)",
                "cases/${lookup}`, { params: { version: state } })",
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "额外的请求配置"):
            await self.host(self.ticket).inspect_row_action()
        self.assertEqual(self.calls, [])
