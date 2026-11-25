# -*- coding: utf-8 -*-
"""Held-out reimbursement source with renamed symbols and relocated guards."""
import json
from pathlib import Path
from unittest import TestCase

from agentscope.business.source_catalog import SourceCatalog


class SourceCatalogVariantTest(TestCase):
    """Supply only the permitted files, without mappings or expected rules."""

    def setUp(self) -> None:
        root = Path(__file__).parent / "fixtures/source_catalog/variant"
        self.catalog = SourceCatalog(
            "review-desk",
            root,
            (
                "ui/ReviewDesk.vue",
                "ui/gateway.ts",
                "backend/handlers.py",
                "backend/review_rules.py",
            ),
            lambda user: user == "reviewer",
        )

    def test_renamed_route_and_delegated_rule(self) -> None:
        """Trace an aliased PATCH handler to the new delegate check."""
        found = self.catalog.search("reviewer")
        self.assertEqual(
            {m["name"] for m in found["matches"]},
            {"递交报销", "复核", "撤回申请"},
        )
        key = next(m["id"] for m in found["matches"] if m["name"] == "撤回申请")
        entry = self.catalog.explain("reviewer", key, found["revision"])
        evidence = {r["kind"]: r for r in entry["rules"]}
        self.assertIn("queued", evidence["visibility"]["statement"])
        self.assertIn(
            "PATCH /v2/expense/reopen",
            evidence["request"]["statement"],
        )
        self.assertIn(
            "verify_delegate(sheet, reviewer)",
            evidence["policy_call"]["statement"],
        )
        ref = evidence["rejection"]["evidence"]
        self.assertEqual(
            (ref["file"], ref["symbol"]),
            ("backend/review_rules.py", "require_delegate"),
        )
        self.assertIn('sheet["delegate"] != reviewer["uid"]', ref["text"])
        self.assertNotIn("申请人", json.dumps(entry, ensure_ascii=False))

    def test_deployment_policy_is_unknown(self) -> None:
        """Report the missing tenant policy rather than implying approval."""
        answer = self.catalog.offline_answer("reviewer", "复核的额度是多少")
        self.assertIn("缺少可静态追踪的策略", answer)
        self.assertIn("check_tenant_policy", answer)
        self.assertIn("backend/handlers.py:", answer)
        self.assertNotIn("源码随后赋值", answer)
