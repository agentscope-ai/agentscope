"""Source-driven discovery using names unrelated to the Yuanjing example."""

import asyncio
import json
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from textwrap import dedent

from agentscope.business import SaveBusinessRegistry, discovery_tool, generate, preview
from agentscope.permission import PermissionBehavior
from agentscope.tool import Toolkit

PARSER = (
    Path(__file__).resolve().parents[1]
    / "src/agentscope/business/node_modules/typescript"
)
VIEW = """
<template><button @click="sendCard">保存</button></template>
<script setup>
import { endpoints as wire } from './remote'
const basket = ref([])
function price(piece) {
  piece.balance = piece.ordered * piece.rate
  piece.ledger = piece.balance + 1
  piece.pending = missingHelper(piece.rate)
  piece.dependent = piece.pending * 2
}
function unrelated(piece) { piece.wrong = piece.rate * 99 }
function pack() { return { entries: basket.value } }
async function sendCard() {
  basket.value.forEach(p => price(p))
  const data = pack()
  const inserted = await wire.add(data)
  const key = inserted.value || inserted
  await wire.read(key)
  basket.value.forEach(p => unrelated(p))
}
</script>
"""


class PublicationLockTest(unittest.IsolatedAsyncioTestCase):
    async def test_second_process_waits_until_publication_finishes(self):
        from agentscope.business._save_registry import _publication_lock

        worker = dedent("""
            import asyncio, sys
            from pathlib import Path
            from agentscope.business._save_registry import _publication_lock

            async def run():
                path = Path(sys.argv[1])
                try:
                    async with asyncio.timeout(0.2), _publication_lock(path):
                        print("overlap", flush=True)
                except TimeoutError:
                    print("waiting", flush=True)
                async with _publication_lock(path):
                    print("acquired", flush=True)

            asyncio.run(run())
        """)
        # Register directory cleanup first so the child releases its SQLite
        # handle before the temporary directory is removed, even on failure.
        directory = self.enterContext(TemporaryDirectory())
        path = Path(directory) / "published.json"
        async with _publication_lock(path):
            child = await asyncio.create_subprocess_exec(
                sys.executable, "-c", worker, str(path), stdout=asyncio.subprocess.PIPE,
            )

            async def clean_up():
                if child.returncode is None:
                    child.kill()
                await child.wait()

            self.addAsyncCleanup(clean_up)
            waiting = await asyncio.wait_for(child.stdout.readline(), 15)
            self.assertEqual(waiting.splitlines(), [b"waiting"])
        stdout, _ = await asyncio.wait_for(child.communicate(), 10)
        self.assertEqual((child.returncode, stdout.splitlines()), (0, [b"acquired"]))


@unittest.skipUnless(
    shutil.which("node") and PARSER.exists(),
    "Install the optional source parser",
)
class WorkflowTest(unittest.TestCase):
    def test_agent_shows_save_target_and_denial_stops_registration(self):
        from unittest.mock import AsyncMock
        from utils import MockModel
        from agentscope.agent import Agent
        from agentscope.event import ConfirmResult, RequireUserConfirmEvent, UserConfirmResultEvent
        from agentscope.message import TextBlock, ToolCallBlock, UserMsg
        from agentscope.model import ChatResponse

        async def run():
            registry = SaveBusinessRegistry(
                self.source, self.openapi, self.root / "published",
                AsyncMock(), AsyncMock(), target_label="离线账套",
            )
            registry.register = AsyncMock(wraps=registry.register)
            register = registry.tools("owner", "agent")[0]
            request = {
                "business": "Receiving", "client_source": "Card.vue", "client_route": "/receiving",
                "save_route": "/v3/cards", "detail_route": "/v3/cards/{id}",
                "issue_selector": ".save-issue", "actions": [], "missing": {}, "complete": {},
            }
            call = ToolCallBlock(id="register", name=register.name, input=json.dumps(request))
            model = MockModel(context_size=16000)
            model.set_responses([
                ChatResponse(content=[call], is_last=True),
                ChatResponse(content=[TextBlock(text="已取消")], is_last=True),
            ])
            agent = Agent(name="test", system_prompt="Register the tool.", model=model,
                          toolkit=Toolkit(tools=[register]))
            events = [event async for event in agent.reply_stream(UserMsg(name="user", content="验收"))]
            prompt, = [event for event in events if isinstance(event, RequireUserConfirmEvent)]
            notice = prompt.model_dump()["tool_calls"][0]["confirmation_message"]
            self.assertIn("离线账套", notice)
            self.assertIn("两张", notice)
            registry.capture.assert_not_awaited()
            await agent.reply(UserConfirmResultEvent(
                reply_id=prompt.reply_id, confirm_results=[ConfirmResult(confirmed=False, tool_call=call)],
            ))
            registry.register.assert_not_awaited()
            registry.capture.assert_not_awaited()

        asyncio.run(run())

    def test_save_registry_previews_both_cases_before_real_writes(self):
        async def run():
            self.page.write_text(
                VIEW + "\n<!-- /v3/cards is saved here; .save-issue shows validation -->\n",
                encoding="utf-8",
            )
            cases = [
                {"action": "fill", "selector": "#purpose", "field": "purpose"},
                {"action": "click", "text": "保存"},
            ]
            captures = []
            validate_missing = True
            invalid_link = None

            async def capture(_plan, actions, live):
                captures.append(live)
                # Give another registry instance time to contend during preview.
                await asyncio.sleep(0)
                purpose = next((a["value"] for a in actions if a["action"] == "fill"), None)
                if purpose == "无效" and validate_missing:
                    return {"attempted": False, "issue": "用途无效"}
                if not purpose:
                    return {"attempted": False, "issue": "请填写用途"}
                request = {"purpose": purpose}
                if not live:
                    return {"attempted": True, "request": request}
                identifier = captures.count(True)
                row_id = 10 * identifier
                bad = live and identifier % 2 == 0
                row = {
                    "id": row_id,
                    "orderId": -1 if bad and invalid_link == "order" else identifier,
                    "batches": [{"detailId": -1 if bad and invalid_link == "detail" else row_id}],
                }
                return {
                    "id": identifier, "request": request,
                    "detail": {"order": {"id": identifier, "billNo": f"TEST-{identifier}"},
                               "details": [row], **request},
                }

            async def detail(_route, identifier):
                return {"id": identifier}

            registry = SaveBusinessRegistry(
                self.source, self.openapi, self.root / "published", capture, detail,
                target_label="测试账套 A",
            )
            register = registry.tools("owner", "agent")[0]
            decision = await register.check_permissions()
            self.assertEqual(decision.behavior, PermissionBehavior.ASK)
            self.assertTrue(decision.bypass_immune)
            self.assertIn("测试账套 A", decision.message)
            self.assertIn("两张", decision.message)
            missing = {"inputs": {}, "actions": cases[1:]}
            evidence = self.source / "validation.ts"
            evidence.write_text("export const rejectInvalid = true // 用途校验", encoding="utf-8")
            counterexamples = [{
                "name": "Unknown purpose",
                "rule": "Unknown purposes are rejected",
                "evidence": {"path": "validation.ts", "line": 1},
                "inputs": {"purpose": "无效"},
                "actions": [{**cases[0], "value": "无效"}, cases[-1]],
            }]
            request = dict(
                business="Receiving", client_source="Card.vue", client_route="/receiving",
                save_route="/v3/cards", detail_route="/v3/cards/{id}",
                issue_selector=".save-issue", actions=cases,
                missing=missing,
                complete={
                    "inputs": {"purpose": "测试"},
                    "actions": [{"action": "fill", "selector": "#purpose", "value": "测试"}, cases[1]],
                },
            )
            with self.assertRaisesRegex(ValueError, "提交 1 至 5 个"):
                await register(**request, counterexamples=[])
            for value in ({}, {"purpose": None}, {"purpose": ""}):
                with self.assertRaisesRegex(ValueError, "区别"):
                    await register(**request, counterexamples=[
                        {**counterexamples[0], **missing, "inputs": value},
                    ])
            self.assertEqual(captures, [])

            # Both clients mistakenly make the same save attempt. Parity
            # alone would accept this, but the counterexample requires zero.
            validate_missing = False
            denied = await register(**request, counterexamples=counterexamples)
            blocked = json.loads(denied.content[0].text)
            self.assertFalse(blocked["registered"])
            self.assertEqual(blocked["stage"], "counterexamples")
            self.assertTrue(blocked["counterexamples"][0]["originalAttemptedSave"])
            self.assertTrue(blocked["counterexamples"][0]["generatedAttemptedSave"])
            self.assertEqual(captures, [False, False])
            self.assertFalse(list(registry.storage.rglob("*.json")))
            self.assertFalse(list(registry.storage.rglob("*.pending")))

            validate_missing = True
            captures.clear()
            second_worker = SaveBusinessRegistry(
                self.source, self.openapi, registry.storage, capture, detail,
                target_label="测试账套 A",
            )
            report, concurrent = await asyncio.gather(
                register(**request, counterexamples=counterexamples),
                second_worker.tools("owner", "agent")[0](**request, counterexamples=counterexamples),
            )
            self.assertEqual(report.content[0].text, concurrent.content[0].text)
            result = json.loads(report.content[0].text)
            self.assertTrue(result["registered"])
            self.assertTrue(result["counterexamples"][0]["passed"])
            self.assertEqual(captures, [False] * 6 + [True, True])
            repeated = await register(**request, counterexamples=counterexamples)
            self.assertEqual(json.loads(repeated.content[0].text), result)
            self.assertEqual(captures.count(True), 2)
            self.assertEqual(len(registry.tools("owner", "agent")), 3)
            self.assertEqual(len(registry.tools("owner", "another")), 1)
            old_save = registry.tools("owner", "agent")[1]
            original = evidence.read_bytes()
            evidence.write_text("export const rejectInvalid = false")
            validate_missing = False
            captures.clear()
            with self.assertRaisesRegex(ValueError, "停用"):
                await old_save(purpose="测试")
            retry = await register(**request, counterexamples=counterexamples)
            self.assertEqual(json.loads(retry.content[0].text)["stage"], "counterexamples")
            self.assertEqual(captures, [False, False])
            evidence.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "停用"):
                await old_save(purpose="测试")
            self.assertEqual([t.name for t in registry.tools("owner", "agent")],
                             ["register_save_business_tool", "erp_read_loadCard"])
            validate_missing = True
            self.assertTrue(json.loads((await register(
                **request, counterexamples=counterexamples,
            )).content[0].text)["registered"])
            self.assertEqual(captures.count(True), 2)
            path = next(registry.storage.rglob("*.json"))
            legacy = json.loads(path.read_bytes())
            del legacy["verification"]["version"]
            path.write_text(json.dumps(legacy))
            self.assertEqual(len(registry.tools("owner", "agent")), 2)
            with self.assertRaisesRegex(ValueError, "停用"):
                await old_save(purpose="测试")

            for invalid_link in ("order", "detail"):
                captures.clear()
                registrar = registry.tools("owner", invalid_link)[0]
                failed = await registrar(**request, counterexamples=counterexamples)
                self.assertEqual(json.loads(failed.content[0].text)["stage"], "compare_saved")
                retry = await registrar(**request, counterexamples=counterexamples)
                self.assertEqual(json.loads(retry.content[0].text)["stage"], "verify_prior_save")
                self.assertEqual(captures.count(True), 2)
                self.assertEqual(len(registry.tools("owner", invalid_link)), 1)

            captures.clear()
            unlabeled = SaveBusinessRegistry(self.source, self.openapi, registry.storage, capture, detail)
            dry = await unlabeled.tools("owner", "no-target")[0](**request, counterexamples=counterexamples)
            self.assertEqual(json.loads(dry.content[0].text)["stage"], "target_required")
            self.assertNotIn(True, captures)

        asyncio.run(run())

    def test_readback_compares_flat_child_links_by_detail_position(self):
        from agentscope.business._save_registry import _same_saved_draft

        def saved(order_id, first, second, parent):
            return {"id": order_id, "request": {}, "detail": {
                "id": order_id,
                "details": [{"id": first, "orderId": order_id}, {"id": second, "orderId": order_id}],
                "batches": [{"detailId": parent, "orderId": order_id}],
            }}

        original = saved(1, 10, 11, 10)
        self.assertTrue(_same_saved_draft(original, saved(2, 20, 21, 20)))
        self.assertFalse(_same_saved_draft(original, saved(2, 20, 21, 21)))
        self.assertFalse(_same_saved_draft(original, saved(2, 20, 21, 99)))
        self.assertFalse(_same_saved_draft(original, saved(2, 20, 20, 20)))

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "client"
        self.source.mkdir()
        (self.source / "remote.ts").write_text("""
import http from 'axios'
export const endpoints = {
  add: packet => http.post('/v3/cards', packet),
  read: key => http.get(`/v3/cards/${key}`),
}
""")
        self.page = self.source / "Card.vue"
        self.page.write_text(VIEW, encoding="utf-8")
        numeric = {
            k: {"type": "number"}
            for k in (
                "balance",
                "ledger",
                "ordered",
                "rate",
                "pending",
                "dependent",
                "wrong",
            )
        }
        body = {
            "type": "object",
            "properties": {
                "entries": {
                    "type": "array",
                    "items": {"type": "object", "properties": numeric},
                }
            },
        }
        self.doc = {
            "openapi": "3.1.0",
            "paths": {
                "/v3/cards": {
                    "post": {
                        "tags": ["Receiving"],
                        "operationId": "insertCard",
                        "requestBody": {
                            "content": {"application/json": {"schema": body}}
                        },
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "integer"}
                                    }
                                }
                            }
                        },
                    }
                },
                "/v3/cards/{number}": {
                    "get": {
                        "operationId": "loadCard",
                        "parameters": [
                            {
                                "in": "path",
                                "required": True,
                                "name": "number",
                                "schema": {"type": "integer"},
                            }
                        ],
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object"}
                                    }
                                }
                            }
                        },
                    }
                },
            },
        }
        self.openapi = self.root / "spec.json"
        self.openapi.write_text(json.dumps(self.doc))

    def test_save_preserves_conditional_row_calculation(self):
        self.page.write_text(
            VIEW.replace("missingHelper(piece.rate)", "piece.rate + 1")
            .replace("p => price(p)", "p => { if (p.ordered > 0) price(p) }"),
            encoding="utf-8",
        )
        save = generate(self.source, self.openapi, "Receiving")["tools"]["save"]
        skipped = {"ordered": 0, "rate": 2, "balance": 10, "ledger": 11}
        result = preview(save, {"packet": {"entries": [skipped, {"ordered": 4, "rate": 2}]}})
        self.assertEqual(result["body"]["entries"][0], skipped)
        self.assertEqual(result["body"]["entries"][1]["ledger"], 9)

        self.page.write_text(
            self.page.read_text(encoding="utf-8").replace("piece.rate + 1", "unknown(piece.rate)"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "unknown"):
            generate(self.source, self.openapi, "Receiving")

    def test_repeated_save_hooks_keep_their_order_and_guards(self):
        self.page.write_text(
            VIEW.replace("piece.ordered * piece.rate", "(piece.balance || 0) + piece.rate")
            .replace("missingHelper(piece.rate)", "piece.rate")
            .replace("async function sendCard()", """
                function updateTotals() {
                    basket.value.forEach(p => { if (p.ordered > 0) price(p) })
                }
                async function sendCard()""")
            .replace("  basket.value.forEach(p => price(p))", """
                if (basket.value.length > 1) updateTotals()
                basket.value.forEach(p => { if (p.balance === 2) price(p) })
                updateTotals()"""),
            encoding="utf-8",
        )
        tool = generate(self.source, self.openapi, "Receiving")["tools"]["save"]
        rows = [{"ordered": 1, "rate": 2}, {"ordered": 0, "rate": 2, "balance": 7}]
        result = preview(tool, {"packet": {"entries": rows}})
        self.assertEqual([r["balance"] for r in result["body"]["entries"]], [6, 7])
        single = preview(tool, {"packet": {"entries": [rows[0]]}})
        self.assertEqual(single["body"]["entries"][0]["balance"], 2)

    def test_agent_discovers_renamed_fields_and_rebuilds_changed_formula(self):
        out = self.root / "bundle.json"

        async def invoke():
            kit = Toolkit(
                tools=[discovery_tool(self.source, self.openapi, out)]
            )
            tool = await kit.get_tool("generate_business_tools")
            return json.loads(
                (await tool(business="Receiving")).content[0].text
            )

        result = asyncio.run(invoke())
        self.assertEqual(result["handler"], "Card.vue#sendCard")
        self.assertEqual(result["fields"], ["balance", "ledger"])
        self.assertEqual(result["unresolved"], ["pending", "dependent"])
        data = json.loads(out.read_bytes())
        self.assertEqual(
            data["discovery"]["triggers"][0]["code"], '@click="sendCard"'
        )
        self.assertEqual(
            data["discovery"]["fields"][0]["reads"], ["ordered", "rate"]
        )
        self.assertIn(
            "unresolved field: piece.pending",
            data["discovery"]["omitted"][1]["reason"],
        )
        self.assertEqual(
            data["tools"]["detail"]["operation"]["route"], "/v3/cards/{number}"
        )
        inputs = {
            "packet": {
                "entries": [
                    {"ordered": 5, "rate": 2},
                    {"ordered": 4, "rate": 3},
                ]
            }
        }

        def amounts(bundle):
            return [
                r["ledger"]
                for r in preview(bundle["tools"]["save"], inputs)["body"][
                    "entries"
                ]
            ]

        self.assertEqual(amounts(data), [11, 13])
        self.page.write_text(
            VIEW.replace(
                "piece.ordered * piece.rate", "piece.ordered + piece.rate"
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            amounts(generate(self.source, self.openapi, "Receiving")), [8, 8]
        )

    def test_whole_editor_keeps_nested_and_non_numeric_payload_fields(self):
        self.page.write_text(
            VIEW.replace(
                "piece.pending = missingHelper(piece.rate)",
                "piece.pending = (piece.parts || []).reduce((n, part) => n + part.qty, 0)",
            )
            .replace(
                "return { entries: basket.value }",
                "return { entries: basket.value.map(row => ({ ...row, label: 'saved' })) }",
            )
            .replace("forEach(p => price(p))", "forEach(price)"),
            encoding="utf-8",
        )
        result = generate(self.source, self.openapi, "Receiving")
        report = result["discovery"]
        self.assertEqual(report["omitted"], [])
        self.assertIn(
            "pending", {r["field"] for r in report["wholeFunctionFields"]}
        )
        row = {"ordered": 3, "rate": 2, "parts": [{"qty": 4}, {"qty": 5}]}
        actual = preview(
            result["tools"]["save"], {"packet": {"entries": [row]}}
        )
        self.assertEqual(
            actual["body"],
            {
                "entries": [
                    {
                        **row,
                        "balance": 6,
                        "ledger": 7,
                        "pending": 9,
                        "dependent": 18,
                        "label": "saved",
                    }
                ]
            },
        )

    def test_unlinked_result_or_ambiguous_editor_is_rejected(self):
        self.page.write_text(
            VIEW.replace("await wire.read(key)", "await wire.read(42)"),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "found 0"):
            generate(self.source, self.openapi, "Receiving")
        self.page.write_text(VIEW, encoding="utf-8")
        (self.source / "Duplicate.vue").write_text(VIEW, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "found 2"):
            generate(self.source, self.openapi, "Receiving")

    def test_agent_reply_can_call_discovery_with_only_the_business_name(self):
        from utils import MockModel

        from agentscope.agent import Agent
        from agentscope.message import TextBlock, ToolCallBlock, UserMsg
        from agentscope.model import ChatResponse

        out = self.root / "from-agent.json"
        model = MockModel(context_size=16000)
        model.set_responses(
            [
                ChatResponse(
                    content=[
                        ToolCallBlock(
                            id="call",
                            name="generate_business_tools",
                            input='{"business":"Receiving"}',
                        )
                    ],
                    is_last=True,
                ),
                ChatResponse(content=[TextBlock(text="Done")], is_last=True),
            ]
        )
        agent = Agent(
            name="compiler",
            system_prompt="Generate the requested business tools.",
            model=model,
            toolkit=Toolkit(
                tools=[discovery_tool(self.source, self.openapi, out)]
            ),
        )
        asyncio.run(
            agent.reply(
                UserMsg(name="user", content="Generate tools for Receiving.")
            )
        )
        bundle = json.loads(out.read_bytes())
        self.assertEqual(bundle["discovery"]["handler"], "Card.vue#sendCard")
        self.assertEqual(model.cnt, 2)
