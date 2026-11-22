# -*- coding: utf-8 -*-
"""Offline acceptance tests for the isolated source reader."""
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from utils import MockModel

from agentscope.business.source_catalog import SourceCatalog
from agentscope.message import TextBlock, ToolCallBlock
from agentscope.model import ChatResponse


EXAMPLE = (
    Path(__file__).resolve().parents[1] / "examples/source_qa/reimbursement"
)
FILES = ("Expenses.vue", "requests.ts", "routes.py")


def call(name: str, **arguments: str) -> ChatResponse:
    """Build a normal tool request for the offline chat model."""
    return ChatResponse(
        is_last=True,
        content=[
            ToolCallBlock(
                id=name,
                name=name,
                input=json.dumps(arguments),
            ),
        ],
    )


class SourceCatalogTest(IsolatedAsyncioTestCase):
    """Verify source lineage, incomplete evidence and reader isolation."""

    def setUp(self) -> None:
        # TestCase closes this context after each test.
        # pylint: disable-next=consider-using-with
        self.temp = Path(self.enterContext(TemporaryDirectory()))
        self.root = self.temp / "application"
        shutil.copytree(EXAMPLE, self.root)
        self.readers = {"alice"}
        self.catalog = SourceCatalog(
            "expenses", self.root, FILES, self.readers.__contains__
        )

    def entry(self, action: str) -> dict:
        """Resolve one action from the currently authorized revision."""
        match = self.catalog.search("alice", action)
        return self.catalog.explain(
            "alice", match["matches"][0]["id"], match["revision"]
        )

    def test_source_only_catalog_and_verifiable_citations(self) -> None:
        """Resolve every rule to the lines and bytes from which it was read."""
        found = self.catalog.search("alice")
        self.assertEqual(
            {m["name"] for m in found["matches"]}, {"提交", "审批", "撤回"}
        )
        for name in ("提交", "审批", "撤回"):
            entry = self.entry(name)
            for rule in entry["rules"]:
                ref = rule["evidence"]
                raw = (self.root / ref["file"]).read_bytes()
                lines = raw.decode("utf-8").splitlines(keepends=True)
                span = "".join(lines[ref["start"] - 1 : ref["end"]])
                self.assertIn(ref["text"], span)
                self.assertEqual(
                    ref["sha256"], hashlib.sha256(raw).hexdigest()
                )
                self.assertEqual(ref["revision"], found["revision"])
        rules = self.entry("撤回")["rules"]
        self.assertTrue(
            any(
                "pending" in r["statement"]
                for r in rules
                if r["kind"] == "visibility"
            )
        )
        self.assertTrue(
            any(
                "仅申请人" in r["statement"]
                for r in rules
                if r["kind"] == "rejection"
            )
        )

    def test_runtime_and_unresolved_policy_are_reported(self) -> None:
        """Report missing tenant values and external policy calls."""
        answer = self.catalog.offline_answer("alice", "这个租户的审批额度是多少")
        self.assertIn("还缺运行时值", answer)
        self.assertIn("tenant_config['approval_limit']", answer)
        path = self.root / "routes.py"
        path.write_text(
            path.read_text("utf-8").replace(
                '    if claim["applicant_id"]',
                '    external_policy(claim)\n    if claim["applicant_id"]',
            ),
            encoding="utf-8",
        )
        rules = self.entry("撤回")["rules"]
        self.assertTrue(
            any(
                r["kind"] == "missing" and "external_policy" in r["statement"]
                for r in rules
            )
        )
        self.assertFalse(any("仅申请人" in r["statement"] for r in rules))

    def test_revision_change_and_deleted_rule(self) -> None:
        """Invalidate old handles and remove deleted conclusions."""
        old = self.entry("撤回")
        path = self.root / "routes.py"
        text = path.read_text("utf-8").replace(
            '    if claim["applicant_id"] != actor["id"]:\n'
            '        raise ValueError("仅申请人可撤回")\n',
            "",
        )
        path.write_text(text, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "版本已变化"):
            self.catalog.explain("alice", old["id"], old["revision"])
        fresh = self.entry("撤回")
        self.assertNotEqual(fresh["revision"], old["revision"])
        self.assertNotIn("仅申请人", json.dumps(fresh, ensure_ascii=False))

    def test_permission_revocation_and_unavailable_ids(self) -> None:
        """Check the grant at listing and detail access, including old IDs."""
        old = self.entry("撤回")
        for who in ("bob", "alice"):
            self.readers.discard(who)
            with self.assertRaises(PermissionError):
                self.catalog.search(who)
            with self.assertRaises(PermissionError):
                self.catalog.explain(who, old["id"], old["revision"])
        self.readers.add("bob")
        with self.assertRaisesRegex(ValueError, "没有该能力"):
            self.catalog.explain("bob", "../other/secret", old["revision"])

    def test_manifest_boundaries_and_source_is_never_executed(self) -> None:
        """Reject escaping paths and links, and parse Python without import."""
        for name in ("../other.py", "/secret.py", "C:/secret.py", ".env"):
            with self.assertRaises(ValueError):
                SourceCatalog("expenses", self.root, [name], lambda _: True)
        link = self.root / "linked"
        outside = self.temp / "outside"
        outside.mkdir()
        (outside / "private.py").write_text("SECRET = 1", encoding="utf-8")
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, "winerror", None) != 1314:
                raise
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                check=True,
            )
        linked = SourceCatalog(
            "expenses", self.root, ["linked/private.py"], lambda _: True
        )
        with self.assertRaises(ValueError):
            linked.search("alice")
        with (self.root / "routes.py").open("a", encoding="utf-8") as out:
            out.write('\nraise RuntimeError("source was executed")\n')
        self.assertEqual(len(self.catalog.search("alice")["matches"]), 3)

    async def test_chat_exposes_only_two_read_tools_and_renders_evidence(
        self,
    ) -> None:
        """Check the tool schemas and discard fabricated model claims."""
        entry = self.entry("撤回")
        model = MockModel(context_size=16000)
        model.set_responses(
            [
                call("list_source_capabilities", query="撤回"),
                call(
                    "explain_source_capability",
                    capability_id=entry["id"],
                    revision=entry["revision"],
                ),
                ChatResponse(
                    content=[TextBlock(text="已为你撤回 BX-1007")], is_last=True
                ),
            ]
        )
        with (
            patch.object(
                model,
                "_call_api",
                # pylint: disable-next=protected-access
                wraps=model._call_api,
            ) as api,
            patch(
                "httpx.AsyncClient.send",
                side_effect=AssertionError("unexpected business HTTP"),
            ) as http,
        ):
            answer = await self.catalog.ask(
                "alice", "能替同事撤回吗？帮我撤回 BX-1007", model
            )
        for attempt in api.await_args_list:
            self.assertEqual(
                {t["function"]["name"] for t in attempt.kwargs["tools"]},
                {"list_source_capabilities", "explain_source_capability"},
            )
        self.assertIn("仅申请人可撤回", answer)
        self.assertIn("routes.py:", answer)
        self.assertIn("具体单据状态", answer)
        self.assertNotIn("已为你撤回", answer)
        http.assert_not_called()
