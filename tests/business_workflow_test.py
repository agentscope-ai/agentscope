"""Source-driven discovery using names unrelated to the Yuanjing example."""

import asyncio
import json
import shutil
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentscope.business import SaveBusinessRegistry, discovery_tool, generate, preview
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


@unittest.skipUnless(
    shutil.which("node") and PARSER.exists(),
    "Install the optional source parser",
)
class WorkflowTest(unittest.TestCase):
    def test_save_registry_previews_both_cases_before_real_writes(self):
        async def run():
            self.page.write_text(
                VIEW + "\n<!-- /v3/cards is saved here; .save-issue shows validation -->\n"
            )
            cases = [
                {"action": "fill", "selector": "#purpose", "field": "purpose"},
                {"action": "click", "text": "保存"},
            ]
            captures = []

            async def capture(_plan, actions, live):
                captures.append(live)
                purpose = next((a["value"] for a in actions if a["action"] == "fill"), None)
                if not purpose:
                    return {"attempted": False, "issue": "请填写用途"}
                request = {"purpose": purpose}
                if not live:
                    return {"attempted": True, "request": request}
                identifier = captures.count(True)
                return {
                    "id": identifier, "request": request,
                    "detail": {"id": identifier, "billNo": f"TEST-{identifier}", **request},
                }

            async def detail(_route, identifier):
                return {"id": identifier}

            registry = SaveBusinessRegistry(
                self.source, self.openapi, self.root / "published", capture, detail
            )
            register = registry.tools("owner", "agent")[0]
            report = await register(
                business="Receiving", client_source="Card.vue", client_route="/receiving",
                save_route="/v3/cards", detail_route="/v3/cards/{id}",
                issue_selector=".save-issue", actions=cases,
                missing={"inputs": {}, "actions": cases[1:]},
                complete={
                    "inputs": {"purpose": "测试"},
                    "actions": [{"action": "fill", "selector": "#purpose", "value": "测试"}, cases[1]],
                },
            )
            result = json.loads(report.content[0].text)
            self.assertTrue(result["registered"])
            self.assertEqual(captures, [False, False, False, False, True, True])
            self.assertEqual(len(registry.tools("owner", "agent")), 3)
            self.assertEqual(len(registry.tools("owner", "another")), 1)

        asyncio.run(run())

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
        self.page.write_text(VIEW)
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
            )
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
            .replace("forEach(p => price(p))", "forEach(price)")
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
            VIEW.replace("await wire.read(key)", "await wire.read(42)")
        )
        with self.assertRaisesRegex(ValueError, "found 0"):
            generate(self.source, self.openapi, "Receiving")
        self.page.write_text(VIEW)
        (self.source / "Duplicate.vue").write_text(VIEW)
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
