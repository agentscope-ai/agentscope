# -*- coding: utf-8 -*-
"""Regression tests for turning client source and OpenAPI into tools."""

from typing import Any
import asyncio
import json
import shutil
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from agentscope.business import (
    BusinessRegistry,
    as_tool,
    build,
    discover,
    preview,
)
from agentscope.business._business import bind_operation
from agentscope.app.message_bus import InMemoryMessageBus
from agentscope.app.tool_login import ToolLogin, tool_login_scope
from agentscope.business._password_auth import (
    PasswordConnection,
    PasswordProfile,
)
from agentscope.tool import Toolkit

HERE = Path(__file__).parent
FIXTURE = HERE / "fixtures/business_tools/purchase"
PARSER = HERE.parent / "src/agentscope/business/node_modules/typescript"


class OpenAPIContractTest(unittest.TestCase):
    """Check OpenAPI schema conversion against request constraints."""

    def test_openapi30_nullable_amount_and_exclusive_minimum(self) -> None:
        """Openapi30 nullable amount and exclusive minimum."""
        document = {
            "openapi": "3.0.1",
            "paths": {
                "/purchase/order": {
                    "post": {
                        "operationId": "save",
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "number",
                                            "nullable": True,
                                            "minimum": 0,
                                            "exclusiveMinimum": True,
                                        },
                                    },
                                },
                            },
                        },
                    },
                },
            },
        }
        op = bind_operation(
            document,
            {"method": "POST", "template": "/purchase/order"},
        )
        self.assertEqual(
            op["responses"]["200"],
            {"type": ["number", "null"], "exclusiveMinimum": 0},
        )


@unittest.skipUnless(
    shutil.which("node") and PARSER.exists(),
    "Install the optional Node source parser to compile the fixture",
)
class BusinessToolTest(unittest.TestCase):
    """Exercise discovery, verification and execution of query tools."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.source = FIXTURE / "client"
        cls.openapi = FIXTURE / "openapi.json"
        cls.artifact = build(cls.source, None, cls.openapi)
        cls.inputs = {"expected": 10, "received": 9, "price": 3, "loading": 2}

    def test_discovers_entry_and_retains_exact_source(self) -> None:
        """Discovers entry and retains exact source."""
        actions = discover(self.source, self.openapi)
        self.assertEqual(
            [a["entry"] for a in actions["actions"]],
            ["Receipt.vue#save"],
        )
        self.assertEqual(actions["diagnostics"], [])
        fn = self.artifact["plan"]["functions"]["amount.ts#payable"]
        ref = fn["steps"][-1]["evidence"]
        lines = (self.source / ref["file"]).read_text().splitlines()
        self.assertIn(
            ref["code"],
            "\n".join(lines[ref["start"] - 1 : ref["end"]]),
        )
        self.assertEqual(
            set(self.artifact["plan"]["hashes"]),
            {"Receipt.vue", "amount.ts", "service.ts"},
        )

    def test_registry_only_publishes_matching_client_cases(self) -> None:
        """Registry only publishes matching client cases."""

        async def run(root: Path) -> None:
            (root / "report.js").write_text(
                "import axios from 'axios';\n"
                "export const report = (params) => "
                "axios({url: '/inventory/report', method: 'get', params});\n",
            )
            api = root / "openapi.json"
            api.write_text(
                json.dumps(
                    {
                        "openapi": "3.1.0",
                        "paths": {
                            "/inventory/report": {
                                "get": {
                                    "operationId": "inventory_report",
                                    "parameters": [
                                        {
                                            "name": "size",
                                            "in": "query",
                                            "schema": {"type": "integer"},
                                        },
                                    ],
                                    "responses": {
                                        "200": {
                                            "content": {
                                                "application/json": {
                                                    "schema": {
                                                        "type": "object",
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                ),
            )

            async def get_token() -> str:
                return "preview"

            mismatch = True

            async def capture(
                route: str,
                pattern: str,
                actions: list[dict],
            ) -> dict:
                self.assertEqual(route, "/inventory")
                self.assertEqual(pattern, "/api/inventory/report")
                size = actions[0]["value"]
                return {
                    "request": {
                        "method": "GET",
                        "path": "/inventory/report",
                        "query": {"size": size},
                    },
                    "status": 200,
                    "response": {
                        "data": int(size) + int(mismatch and size == "2"),
                    },
                }

            async def send(
                _client: httpx.AsyncClient,
                request: httpx.Request,
                **_kwargs: Any,
            ) -> httpx.Response:
                return httpx.Response(
                    200,
                    request=request,
                    json={"data": int(request.url.params["size"])},
                )

            registry = BusinessRegistry(
                root,
                api,
                root / "registry",
                "http://127.0.0.1:8000/api",
                get_token,
                capture,
            )
            register = registry.tools("user", "agent")[0]
            inputs = {
                "business": "Inventory report",
                "entry": "report.js#report",
                "client_route": "/inventory",
                "samples": [
                    {
                        "name": value,
                        "actions": [
                            {
                                "action": "fill",
                                "selector": "input",
                                "value": value,
                            },
                        ],
                    }
                    for value in ("1", "2")
                ],
            }
            with patch.object(httpx.AsyncClient, "send", send):
                with self.assertRaisesRegex(ValueError, "工具返回与原客户端不一致"):
                    await register(**inputs)
                self.assertEqual(len(registry.tools("user", "agent")), 1)
                mismatch = False
                await register(**inputs)
                self.assertEqual(
                    [tool.name for tool in registry.tools("user", "agent")],
                    ["register_business_tool", "erp_inventory_report"],
                )
                self.assertEqual(len(registry.tools("user", "other-agent")), 1)

        with TemporaryDirectory() as directory:
            asyncio.run(run(Path(directory)))

    def test_registered_query_resumes_exact_request_after_private_login(
        self,
    ) -> None:
        """Registered query resumes exact request after private login."""

        async def run(status: int, service_code: int) -> None:
            # pylint: disable-next=consider-using-with
            source = Path(self.enterContext(TemporaryDirectory()))
            (source / "report.js").write_text(
                "import axios from 'axios';\nexport const report = params => "
                "axios({url:'/report', method:'get', params});",
            )
            spec = source / "api.json"
            spec.write_text(
                json.dumps(
                    {
                        "openapi": "3.1.0",
                        "paths": {
                            "/report": {
                                "get": {
                                    "operationId": "report",
                                    "parameters": [
                                        {
                                            "name": "id",
                                            "in": "query",
                                            "schema": {"type": "integer"},
                                        },
                                    ],
                                    "responses": {
                                        "200": {
                                            "content": {
                                                "application/json": {
                                                    "schema": {
                                                        "type": "object",
                                                    },
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                ),
            )
            artifact = build(source, "report.js#report", spec)
            scope = ("owner", "agent", "chat")
            broker = ToolLogin(InMemoryMessageBus(), timeout=5)
            profile = PasswordProfile.model_validate(
                {
                    "login_path": "/auth",
                    "request_fields": {"password": "password"},
                    "accepted": {"status": 200},
                    "rejected": [{"status": 401}],
                    "expired": [{"status": 401}, {"fields": {"code": 401}}],
                    "token_path": "session.token",
                    "session_path": "session",
                    "client_route": "/query",
                    "query_path": "/default",
                    "evidence": [{"path": "login.ts", "start": 1, "end": 1}]
                    * 3,
                },
            )
            logins, queries = [], []

            async def login(req: httpx.Request) -> httpx.Response:
                self.assertEqual((req.method, req.url.path), ("POST", "/auth"))
                supplied = json.loads(req.content)["password"]
                logins.append(supplied)
                return httpx.Response(
                    200,
                    json={"session": {"token": supplied}},
                )

            challenge = asyncio.Event()

            async def upstream(
                _client: httpx.AsyncClient,
                request: httpx.Request,
            ) -> httpx.Response:
                queries.append(
                    (
                        request.method,
                        str(request.url),
                        request.headers["authorization"],
                    ),
                )
                if len(queries) == 1:
                    challenge.set()
                    return httpx.Response(
                        status,
                        request=request,
                        json={"code": service_code},
                    )
                return httpx.Response(200, request=request, json={"id": 42})

            async def dispatch(
                client: httpx.AsyncClient,
                request: httpx.Request,
            ) -> httpx.Response:
                if request.url.path == "/auth":
                    return await login(request)
                return await upstream(client, request)

            connection = PasswordConnection(
                broker=broker,
                service="ERP",
                title="ERP",
                base_url="https://erp.test",
                context={},
                previous_password=lambda: "old",
                transport=httpx.MockTransport(login),
            )

            async def no_token() -> None:
                self.fail("Unexpected fallback to a static token")

            with TemporaryDirectory() as root:
                registry = BusinessRegistry(
                    self.source,
                    spec,
                    Path(root),
                    "https://erp.test",
                    no_token,
                    None,
                    get_read_auth=lambda: connection.read_auth(profile),
                )
                # pylint: disable-next=protected-access
                directory = registry._directory(
                    *scope[:2],
                )
                directory.mkdir(parents=True)
                (directory / "detail.json").write_text(
                    json.dumps({"artifact": artifact}),
                )
                query = registry.tools(*scope[:2])[1]
                with tool_login_scope(*scope), patch.object(
                    httpx.AsyncClient,
                    "_send_single_request",
                    new=dispatch,
                ):
                    result = asyncio.create_task(query(params={"id": 42}))
                    await asyncio.wait_for(challenge.wait(), 3)
                    if service_code == 403:
                        with self.assertRaises(httpx.HTTPStatusError):
                            await result
                        self.assertIsNone(broker.pending(scope))
                        self.assertEqual(len(queries), 1)
                        return
                    for _ in range(100):
                        if broker.pending(scope):
                            break
                        await asyncio.sleep(0.01)
                    view = broker.pending(scope)
                    self.assertEqual(view.step.fields[0].kind, "secret")
                    await broker.submit(
                        scope,
                        view.id,
                        view.revision,
                        {"password": "new"},
                    )
                    report = json.loads((await result).content[0].text)
                    self.assertEqual(
                        report["authentication"]["status"],
                        "resumed_after_login",
                    )
                self.assertEqual(logins, ["old", "new"])
                self.assertEqual(queries[0][:2], queries[1][:2])
                self.assertEqual(len(queries), 2)
                self.assertEqual(
                    queries[1],
                    ("GET", "https://erp.test/report?id=42", "Bearer new"),
                )

        for status, service_code in ((401, 401), (200, 401), (403, 403)):
            with self.subTest(status=status):
                asyncio.run(run(status, service_code))

    def test_guard_default_and_body_contract(self) -> None:
        """Guard default and body contract."""
        req = preview(self.artifact, self.inputs)
        self.assertEqual(
            req,
            {
                "method": "POST",
                "path": "/purchase/order",
                "body": {
                    "details": [
                        {"expectPcs": 10, "inPcs": 9, "payableAmount": 32},
                    ],
                },
            },
        )
        inputs = dict(self.inputs)
        del inputs["loading"]
        self.assertEqual(
            preview(self.artifact, inputs)["body"]["details"][0][
                "payableAmount"
            ],
            30,
        )
        with self.assertRaisesRegex(ValueError, "Negative received quantity"):
            preview(self.artifact, {**self.inputs, "received": -1})
        with self.assertRaisesRegex(ValueError, "Request body"):
            preview(self.artifact, {**self.inputs, "expected": -1})

    def test_unsupported_source_does_not_emit_a_tool(self) -> None:
        """Unsupported source does not emit a tool."""
        with TemporaryDirectory() as temp:
            root = Path(temp) / "client"
            shutil.copytree(self.source, root)
            fn = root / "amount.ts"
            fn.write_text(
                fn.read_text().replace("Math.round", "unknownPricingService"),
            )
            with self.assertRaisesRegex(ValueError, "Unresolved function"):
                build(root, "Receipt.vue#save", self.openapi)

    def test_projection_recalculates_every_row_and_hashes_wrapper(
        self,
    ) -> None:
        """Projection recalculates every row and hashes wrapper."""
        with TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "adapter.js").write_text(
                "import axios from 'axios';\n"
                "const api = axios.create({});\nexport default api;",
            )
            (root / "save.js").write_text(
                "import request from './adapter';\nexport const save = data "
                "=> request.post('/purchase/order',data)",
            )
            (root / "rows.js").write_text(
                "function calc(row) {"
                "const expected=Number(row.expectPcs || 0); "
                "row.payableAmount=expected*row.price;}",
            )
            profile = {
                "entry": "save.js#save",
                "httpClient": "./adapter",
                "derive": [
                    {
                        "source": "rows.js#calc",
                        "assignment": "row.payableAmount",
                        "each": "data.details",
                    },
                ],
            }
            artifact = build(root, None, self.openapi, profile)
            inputs = {
                "data": {
                    "details": [
                        {
                            "expectPcs": 10,
                            "inPcs": 9,
                            "price": 3,
                            "payableAmount": 99,
                        },
                        {"expectPcs": 4, "inPcs": 2, "price": 6},
                    ],
                },
            }
            self.assertIn("adapter.js", artifact["plan"]["hashes"])
            self.assertEqual(
                [
                    r["payableAmount"]
                    for r in preview(artifact, inputs)["body"]["details"]
                ],
                [30, 24],
            )
            self.assertEqual(inputs["data"]["details"][0]["payableAmount"], 99)
            adapter = root / "adapter.js"
            adapter.write_text(adapter.read_text() + "\n// changed\n")
            rebuilt = build(root, None, self.openapi, profile)
            self.assertNotEqual(
                artifact["plan"]["hashes"],
                rebuilt["plan"]["hashes"],
            )

    def test_toolkit_dispatch_and_business_failure(self) -> None:
        """Toolkit dispatch and business failure."""
        original_client = httpx.AsyncClient
        received = []

        def handler(request: httpx.Request) -> httpx.Response:
            received.append(json.loads(request.content))
            self.assertEqual(
                str(request.url),
                "https://erp.example/api/purchase/order",
            )
            return httpx.Response(
                200,
                json={"code": 422, "message": "closed", "id": 5},
            )

        def client(**kwargs: Any) -> httpx.AsyncClient:
            return original_client(
                transport=httpx.MockTransport(handler),
                **kwargs,
            )

        async def invoke(artifact: dict) -> dict:
            toolkit = Toolkit(
                tools=[as_tool(artifact, "https://erp.example/api")],
            )
            tool = await toolkit.get_tool("save_purchase_receipt")
            return json.loads((await tool(**self.inputs)).content[0].text)

        with patch("agentscope.business._business.httpx.AsyncClient", client):
            self.assertEqual(
                asyncio.run(invoke(self.artifact))["result"]["id"],
                5,
            )
            artifact = deepcopy(self.artifact)
            artifact["profile"]["success"] = {"field": "code", "equals": 200}
            with self.assertRaisesRegex(ValueError, "closed"):
                asyncio.run(invoke(artifact))
        self.assertEqual(received[0]["details"][0]["payableAmount"], 32)
