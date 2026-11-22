"""Recover published saves through the agent, using a non-idempotent fake ERP."""
import asyncio
import hashlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agentscope.agent import Agent
from agentscope.business import SaveBusinessRegistry
from agentscope.event import ConfirmResult, RequireUserConfirmEvent, UserConfirmResultEvent
from agentscope.message import TextBlock, ToolCallBlock, UserMsg
from agentscope.model import ChatResponse
from agentscope.state import AgentState
from agentscope.tool import Toolkit
from utils import MockModel


HERE = Path(__file__).resolve().parent
CAPTURE = HERE.parent / "examples/business_tools/capture_client.cjs"
FAKE_BROWSER = HERE / "fixtures/business_tools/fake_save_browser.cjs"
SAVE = "erp_save_card"


def registry(root, *, mode="ok", target="offline ledger"):
    async def capture(plan, actions, live):
        assert live
        child = await asyncio.create_subprocess_exec(
            "node", str(CAPTURE), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            env={**os.environ, "BUSINESS_PLAYWRIGHT_MODULE": str(FAKE_BROWSER),
                 "FAKE_ERP_ROOT": str(root), "FAKE_SAVE_MODE": mode},
        )
        payload = {"origin": "https://erp.invalid", "clientRoute": "/card", "storage": {},
                   "actions": actions, "save": {**plan, "live": live}}
        stdout, stderr = await child.communicate(json.dumps(payload).encode())
        if child.returncode in (81, 82):
            raise ConnectionError("lost browser worker")
        if child.returncode:
            raise RuntimeError(stderr.decode())
        return json.loads(stdout)

    async def detail(route, receipt_id):
        assert route == "/cards/{id}"
        with (root / "requests").open("a") as log:
            log.write(f"GET {receipt_id}\n")
        if mode == "timeout":
            raise TimeoutError
        assert str(receipt_id) in (root / "drafts").read_text().splitlines()
        return {"order": {"id": receipt_id}, "purpose": "test"}

    return SaveBusinessRegistry(root, root / "spec", root / "registry", capture,
                                detail, target_label=target)


def worker(root, mode="ok"):
    model = MockModel(context_size=16000)
    tools = registry(root, mode=mode).tools("owner", "agent")
    class ContendingToolkit(Toolkit):
        async def call_tool(self, tool_call, state):
            # Both workers must be inside execution of the approved call
            # before either of them can claim the external write.
            (root / f"worker-{os.getpid()}.ready").touch()
            async with asyncio.timeout(15):
                while len(list(root.glob("worker-*.ready"))) < 2:
                    await asyncio.sleep(0.01)
            async for chunk in super().call_tool(tool_call, state):
                yield chunk

    toolkit = ContendingToolkit if mode == "concurrent" else Toolkit
    return Agent(name="test", system_prompt="Save the draft.", model=model,
                 toolkit=toolkit(tools=tools))


def call():
    return ToolCallBlock(id="save-call", name=SAVE, input='{"purpose":"test"}')


async def resume(root, mode="ok", approved=True, draft=None):
    agent = worker(root, mode)
    agent.state = AgentState.model_validate_json((root / "parked").read_text())
    agent.model.set_responses([ChatResponse(content=[TextBlock(text="done")], is_last=True)])
    approval = call()
    if draft is not None:
        approval.input = json.dumps(draft)
    await agent.reply(UserConfirmResultEvent(reply_id=agent.state.reply_id, confirm_results=[
        ConfirmResult(confirmed=approved, tool_call=approval),
    ]))
    block, = agent.state.get_reply_message(agent.name).get_content_blocks("tool_result")
    return block


class SaveRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(TemporaryDirectory()))
        (self.root / "spec").write_text("{}")
        scope = hashlib.sha256(json.dumps(["owner", "agent"]).encode()).hexdigest()
        self.directory = self.root / "registry" / scope
        self.directory.mkdir(parents=True)
        self.plan = {
            "business": "Card", "targetLabel": "offline ledger", "saveTool": SAVE,
            "readTool": "erp_read_card", "detailRoute": "/cards/{id}", "saveRoute": "/cards",
            "issueSelector": ".issue", "actions": [
                {"action": "fill", "selector": "#purpose", "field": "purpose"},
                {"action": "click", "selector": "#save"},
            ], "fields": ["purpose"], "ruleHashes": {},
            "apiSha256": hashlib.sha256(b"{}").hexdigest(),
        }
        (self.directory / "card.json").write_text(json.dumps({
            "plan": self.plan, "verification": {"version": 3, "registered": True},
        }))
        self.op = await self.park()

    async def park(self):
        agent = worker(self.root)
        agent.model.set_responses([ChatResponse(content=[call()], is_last=True)])
        prompts = [item async for item in agent.reply_stream(UserMsg("owner", "保存草稿"))
                   if isinstance(item, RequireUserConfirmEvent)]
        prompt, = prompts
        request, = prompt.tool_calls
        self.assertIn(request.confirmation_id, request.confirmation_message)
        self.assertIn("offline ledger", request.confirmation_message)
        (self.root / "parked").write_text(agent.state.model_dump_json())
        return request.confirmation_id

    async def save(self, mode="ok"):
        result = await resume(self.root, mode)
        return json.loads(result.output[0].text)

    async def read(self):
        agent = worker(self.root)
        lookup = ToolCallBlock(id="recover", name="erp_read_card",
                               input=json.dumps({"operation_id": self.op}))
        agent.model.set_responses([
            ChatResponse(content=[lookup], is_last=True),
            ChatResponse(content=[TextBlock(text="done")], is_last=True),
        ])
        await agent.reply(UserMsg("owner", "核对原操作"))
        result, = agent.state.get_reply_message(agent.name).get_content_blocks("tool_result")
        return json.loads(result.output[0].text)

    def counts(self):
        requests = (self.root / "requests").read_text().splitlines()
        drafts = (self.root / "drafts").read_text().splitlines()
        return requests.count("POST"), len(drafts), len(requests) - requests.count("POST")

    async def test_concurrent_workers_resume_the_same_confirmation(self):
        children = [await asyncio.create_subprocess_exec(
            sys.executable, __file__, "--resume", str(self.root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        ) for _ in range(2)]
        try:
            replies = await asyncio.wait_for(asyncio.gather(
                *(child.communicate() for child in children),
            ), 30)
            results = []
            for child, (out, err) in zip(children, replies):
                self.assertEqual(child.returncode, 0, err.decode())
                results.append(json.loads(out))
            self.assertEqual(results[0], results[1])
            self.assertEqual(results[0]["status"], "saved")
            self.assertEqual(results[0]["operation_id"], self.op)
            self.assertEqual(self.counts()[:2], (1, 1))
        finally:
            for child in children:
                if child.returncode is None:
                    child.kill()
                await child.wait()

    async def test_lost_post_response_requires_reconciliation(self):
        lost = await self.save("lost")
        self.assertEqual(lost["status"], "needs_reconciliation")
        self.assertEqual(lost["operation_id"], self.op)
        self.assertEqual(await self.save(), lost)
        self.assertEqual(await self.read(), lost)
        self.assertEqual(self.counts(), (1, 1, 0))

    async def test_readback_timeout_retries_only_get(self):
        pending = await self.save("timeout")
        self.assertEqual(pending["status"], "saved_needs_readback")
        self.assertEqual(self.counts(), (1, 1, 1))
        saved = await self.read()
        self.assertEqual(saved["detail"]["order"]["id"], pending["id"])
        self.assertEqual(await self.save(), saved)
        self.assertEqual(self.counts(), (1, 1, 2))

    async def test_browser_dies_between_receipt_and_python_result(self):
        result = await self.save("crash_after_receipt")
        self.assertEqual(result["status"], "saved")
        self.assertEqual(await self.read(), result)
        self.assertEqual(self.counts(), (1, 1, 1))

    async def test_draft_is_bound_to_confirmation_and_denial_cannot_submit(self):
        self.assertEqual((await self.read())["status"], "awaiting_confirmation")
        self.assertEqual((await resume(self.root, approved=False)).state, "denied")
        self.assertEqual((await resume(self.root, draft={"purpose": "changed"})).state, "error")
        self.assertFalse((self.root / "requests").exists())
        first = await self.save()
        self.assertNotEqual(await self.park(), self.op)
        second = await self.save()
        self.assertNotEqual(first["operation_id"], second["operation_id"])
        self.assertEqual(self.counts()[:2], (2, 2))

    async def test_other_users_and_ledgers_cannot_recover_the_operation(self):
        other_scope = hashlib.sha256(json.dumps(["other", "agent"]).encode()).hexdigest()
        other = self.directory.parent / other_scope
        other.mkdir()
        shutil.copyfile(self.directory / "card.json", other / "card.json")
        for tools in (
            registry(self.root).tools("other", "agent"),
            registry(self.root, target="another ledger").tools("owner", "agent"),
        ):
            with self.assertRaises(ValueError):
                await tools[-1](operation_id=self.op)
        self.assertFalse((self.root / "requests").exists())

    async def test_older_parked_save_without_receipt_needs_reconciliation(self):
        snapshot = json.loads((self.root / "parked").read_text())
        for msg in snapshot["context"]:
            for block in msg["content"]:
                block.pop("confirmation_id", None)
        (self.root / "parked").write_text(json.dumps(snapshot))
        self.assertEqual((await self.save())["status"], "needs_reconciliation")
        self.assertFalse((self.root / "requests").exists())


if __name__ == "__main__" and sys.argv[1:2] == ["--resume"]:
    result = asyncio.run(resume(Path(sys.argv[2]), "concurrent"))
    print(result.output[0].text)
