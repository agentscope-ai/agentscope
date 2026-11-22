"""Exercise the published save contract with an in-memory ticket system."""

import json
import sqlite3
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from agentscope.business import SaveBusinessRegistry, write_save_receipt
from agentscope.message import ToolCallBlock
from agentscope.state import AgentState
from agentscope.tool import Toolkit
from agentscope.tool._business_search import PublishedBusinessTool


async def invoke(tool, **kwargs):
    return json.loads((await tool(**kwargs)).content[0].text)


class ConnectedSaveTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        (self.root / "Ticket.vue").write_text("<!-- /tickets .issue -->", encoding="utf-8")
        content = {"application/json": {"schema": {"type": "object"}}}
        self.spec = self.root / "api.json"
        self.spec.write_text(json.dumps({"openapi": "3.1.0", "paths": {
            "/tickets": {"post": {"operationId": "create", "requestBody": {"content": content},
                                  "responses": {"201": {}}}},
            "/tickets/{key}": {"get": {"operationId": "retrieve", "responses": {"200": {}},
                                      "parameters": [{"in": "path", "name": "key", "required": True,
                                                      "schema": {"type": "string"}}]}},
        }}), encoding="utf-8")
        fill = {"action": "fill", "selector": "#title"}
        click = {"action": "click", "selector": "#create"}
        self.request = dict(
            business="工单", client_source="Ticket.vue", client_route="/tickets",
            save_route="/tickets", detail_route="/tickets/{key}", issue_selector=".issue",
            actions=[{**fill, "field": "title"}, click], missing={"inputs": {}, "actions": [click]},
            complete={"inputs": {"title": "repair"}, "actions": [{**fill, "value": "repair"}, click]},
            counterexamples=[{"name": "invalid", "rule": "forbidden title", "inputs": {"title": "!"},
                              "evidence": {"path": "Ticket.vue", "line": 1},
                              "actions": [{**fill, "value": "!"}, click]}],
        )
        self.records = {}
        self.reply = "complete"
        self.detail = AsyncMock(side_effect=lambda route, key: self.records[key])

    async def capture(self, plan, actions, live):
        title = next((s["value"] for s in actions if s["action"] == "fill"), "")
        if title in ("", "!") or (live and self.reply == "blocked"):
            return {"attempted": False, "issue": "invalid"}
        request = {"subject": title}
        if not live:
            return {"attempted": True, "request": request}
        key = f"{len(self.records) + 1:05d}"
        record = {"ticket": {"key": key, "attributes": {"subject": title}}}
        self.records[key] = record
        if self.reply == "receipt_only":
            return {"id": key}
        if self.reply == "unknown":
            return {}
        if self.reply == "crash":
            write_save_receipt(actions[-1]["saveReceipt"], key)
            raise ConnectionError("worker stopped after the ticket system accepted the request")
        if self.reply == "mismatch":
            record["ticket"]["attributes"]["subject"] = "different"
        return {"id": key, "detail": record, "request": request}

    def tools(self, target="offline support"):
        def project(plan, detail):
            ticket = detail["ticket"]
            fields = ticket["attributes"]
            return ticket["key"], {"title": fields["subject"]} if "subject" in fields else {}

        registry = SaveBusinessRegistry(
            self.root, self.spec, self.root / "published", self.capture, self.detail,
            target_label=target, tool_prefix="support", readback_view=project,
        )
        return registry.tools("employee", "assistant")

    async def publish(self):
        register, = self.tools()
        result = await invoke(register, **self.request)
        self.assertTrue(result["registered"])
        self.assertEqual(result["tools"], ["support_save_create", "support_read_retrieve"])
        _, save, read = self.tools()
        return save, read

    async def test_receipt_only_acceptance_retry_reads_without_recreating(self):
        self.reply = "receipt_only"
        await invoke(self.tools()[0], **self.request)
        self.detail.side_effect = TimeoutError()
        retry = await invoke(self.tools()[0], **self.request)
        self.assertEqual(retry["checkpoint"]["originalId"], "00001")
        self.assertEqual(retry["readbacks"]["originalId"]["status"], "saved_needs_readback")
        self.detail.side_effect = lambda route, key: self.records[key]
        retry = await invoke(self.tools()[0], **self.request)
        self.assertEqual(retry["readbacks"]["originalId"]["status"], "saved")
        await invoke(self.tools("different target")[0], **self.request)
        self.assertEqual(self.detail.await_count, 2)
        self.assertEqual(len(self.records), 1)

    async def test_ticket_denial_survives_restart_and_late_decisions(self):
        save, _ = await self.publish()
        inputs = {"title": "中文工单"}
        op, _ = save.bind_confirmation("s", "r", "c", inputs)
        call = ToolCallBlock(id="c", name=save.name, input=json.dumps(inputs), confirmation_id=op)
        toolkit = Toolkit(tools=self.tools())
        toolkit.deny_confirmation(call)
        _, save, read = self.tools()
        self.assertEqual((await invoke(read, operation_id=op))["status"], "denied")
        self.assertEqual((await invoke(save, _confirmation_id=op, _confirmed_inputs=inputs))["status"], "denied")
        self.assertEqual(len(self.records), 2)  # The two publication fixtures only.
        self.detail.assert_not_awaited()

        # The opposite ordering preserves a committed write and its receipt.
        next_op, _ = save.bind_confirmation("s", "r2", "c2", inputs)
        self.reply = "receipt_only"
        self.detail.side_effect = TimeoutError()
        pending = await invoke(save, _confirmation_id=next_op, _confirmed_inputs=inputs)
        self.assertEqual(pending["status"], "saved_needs_readback")
        toolkit.deny_confirmation(call.model_copy(update={"id": "c2", "confirmation_id": next_op}))
        self.detail.side_effect = lambda route, key: self.records[key]
        verified = await invoke(read, operation_id=next_op)
        self.assertEqual((verified["status"], verified["id"]), ("saved", pending["id"]))
        self.assertEqual(len(self.records), 3)

    async def test_ticket_can_be_declined_after_its_write_tool_is_suspended(self):
        save, _ = await self.publish()
        op, _ = save.bind_confirmation("s", "r", "c", {"title": "old form"})
        self.spec.write_text("{}", encoding="utf-8")
        register, read = self.tools()
        self.assertEqual(read.name, "support_read_retrieve")
        Toolkit(tools=[register, read]).deny_confirmation(
            ToolCallBlock(id="c", name=save.name, input="{}", confirmation_id=op),
        )
        self.assertEqual((await invoke(read, operation_id=op))["status"], "denied")
        self.assertEqual(len(self.records), 2)
        self.detail.assert_not_awaited()

    async def test_ticket_query_only_excludes_both_save_and_recovery(self):
        save, read = await self.publish()

        def list_tickets():
            """列出现有工单。"""
            return list(self.records.values())

        query = PublishedBusinessTool(list_tickets, name="support_list_tickets", business="工单",
                                      action="query", is_read_only=True)
        kit = Toolkit(tools=[query, save, read])
        search = await kit.check_tool_available("search_business_tools", [])
        state = AgentState()
        for phrase in ("不要新建工单，只查现有工单", "don't create, just list tickets"):
            with self.subTest(phrase=phrase):
                result = await invoke(search, query=phrase, _agent_state=state)
                self.assertEqual([match["name"] for match in result["matches"]], [query.name])
                self.assertEqual(state.tool_context.selected_business_tools, [query.name])
        self.assertEqual(len(self.records), 2)
        self.detail.assert_not_awaited()

    async def test_acceptance_requires_explicit_evidence_to_retry_a_write(self):
        self.reply = "blocked"
        await invoke(self.tools()[0], **self.request)
        self.assertFalse(self.records)
        self.reply = "unknown"
        await invoke(self.tools()[0], **self.request)
        retry = await invoke(self.tools()[0], **self.request)
        self.assertEqual(retry["stage"], "verify_prior_save")
        self.assertEqual(len(self.records), 1)
        self.detail.assert_not_awaited()

    async def test_mismatch_and_missing_field_survive_restart_until_verified(self):
        save, _ = await self.publish()
        inputs = {"title": "中文工单"}
        op, _ = save.bind_confirmation("session", "reply", "call", inputs)
        self.reply = "mismatch"
        result = await invoke(save, _confirmation_id=op, _confirmed_inputs=inputs)
        self.assertEqual(result["status"], "saved_needs_review")
        self.assertEqual(result["differing_fields"], ["title"])
        key = result["id"]
        self.assertEqual(key, "00003")
        _, save, read = self.tools()
        fields = self.records[key]["ticket"]["attributes"]
        del fields["subject"]
        result = await invoke(save, _confirmation_id=op, _confirmed_inputs=inputs)
        self.assertEqual(result["differing_fields"], ["title"])
        fields["subject"] = inputs["title"]
        self.assertEqual((await invoke(read, operation_id=op))["status"], "saved")
        self.assertEqual(len(self.records), 3)  # Two acceptance writes and one employee write.

    async def test_worker_loss_preserves_string_key_and_checks_draft(self):
        save, _ = await self.publish()
        self.reply = "crash"
        inputs = {"title": "worker crash"}
        op, _ = save.bind_confirmation("s", "r", "c", inputs)
        result = await invoke(save, _confirmation_id=op, _confirmed_inputs=inputs)
        self.assertEqual((result["status"], result["id"]), ("saved", "00003"))
        self.detail.assert_awaited_once_with("/tickets/{key}", "00003")
        self.assertEqual(len(self.records), 3)

    async def test_readback_uses_identity_and_target_before_matching_fields(self):
        save, _ = await self.publish()
        self.reply = "receipt_only"
        inputs = {"title": "same text"}
        self.detail.side_effect = lambda route, key: {
            "ticket": {"key": "different key", "attributes": {"subject": "same text"}},
        }
        op, _ = save.bind_confirmation("s", "r", "c", inputs)
        result = await invoke(save, _confirmation_id=op, _confirmed_inputs=inputs)
        self.assertEqual(result["status"], "saved_needs_readback")
        other = self.tools("separate support workspace")[-1]
        with self.assertRaisesRegex(ValueError, "目标系统已变更"):
            await other(receipt_id=result["id"])
        self.detail.assert_awaited_once_with("/tickets/{key}", "00003")
        self.assertEqual(len(self.records), 3)

    async def test_upgrade_never_certifies_old_rows_without_a_confirmed_draft(self):
        save, read = await self.publish()
        inputs = {"title": "legacy"}
        op, _ = save.bind_confirmation("s", "r", "c", inputs)
        database = next(self.root.rglob("save-operations.sqlite3"))
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("ALTER TABLE saves DROP COLUMN confirmed_inputs")
        # An existing approval can still bind its original inputs before the first write.
        result = await invoke(save, _confirmation_id=op, _confirmed_inputs=inputs)
        self.assertEqual(result["status"], "saved")
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("ALTER TABLE saves DROP COLUMN confirmed_inputs")
        self.assertEqual((await invoke(read, operation_id=op))["status"], "saved_needs_review")
        self.assertEqual(len(self.records), 3)
