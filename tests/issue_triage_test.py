# -*- coding: utf-8 -*-
# pylint: disable=protected-access,wrong-import-position,wrong-import-order
"""Tests for the issue triage scripts.

What matters here is everything that turns model output — derived from an
arbitrary stranger's issue text — into something posted under the
repository's own identity, plus the guards that decide a verdict is not
trustworthy.
"""
import os
import subprocess
import sys
import tempfile
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

from agentscope.message import Msg, TextBlock
from agentscope.permission import PermissionBehavior

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts"),
)

import claim_intent  # noqa: E402
import issue_triage  # noqa: E402


class CommentRenderingTest(IsolatedAsyncioTestCase):
    """Model text becomes a comment that cannot act on the reader."""

    async def test_mentions_cannot_summon_anyone(self) -> None:
        """A quoted mention must not notify the person it names."""
        self.assertEqual(
            issue_triage._defuse("ping @DavdGao and @qbc2016, not a@b.com"),
            "ping `@DavdGao` and `@qbc2016`, not a@b.com",
        )

    async def test_a_quote_survives_a_newline(self) -> None:
        """A bare newline ends a block quote, so every line carries it."""
        self.assertEqual(
            issue_triage._as_quote("first\nsecond"),
            "> first\n> second",
        )

    async def test_a_quote_survives_markdown_structure(self) -> None:
        """Leading Markdown would be read as structure, not as text."""
        self.assertEqual(
            issue_triage._as_quote("# shout\n- item\n1. numbered"),
            "> \\# shout\n> \\- item\n> \\1. numbered",
        )

    async def test_a_quote_survives_html(self) -> None:
        """A tracking pixel must arrive as characters, not as an image."""
        self.assertEqual(
            issue_triage._as_quote('<img src="x">'),
            '> &lt;img src="x"&gt;',
        )

    async def test_a_fence_outgrows_its_content(self) -> None:
        """Backticks in the text would otherwise close the fence early."""
        self.assertEqual(
            issue_triage._as_code("a ``` b ````"),
            "`````\na ``` b ````\n`````",
        )

    async def test_a_fence_stays_three_when_it_can(self) -> None:
        """Ordinary text gets the ordinary fence."""
        self.assertEqual(
            issue_triage._as_code("plain"),
            "```\nplain\n```",
        )

    async def test_overlong_text_is_cut(self) -> None:
        """A model that rambles cannot flood the thread."""
        self.assertEqual(
            len(issue_triage._defuse("A" * 99999)),
            4000,
        )


class CommentChoiceTest(IsolatedAsyncioTestCase):
    """Which verdicts speak, and which let the label speak for them."""

    async def test_a_confirmed_bug_gets_no_comment(self) -> None:
        """The label says it; a comment would only be noise."""
        self.assertIsNone(issue_triage._comment("confirmed", "s", "e"))

    async def test_every_other_verdict_explains_itself(self) -> None:
        """A verdict that asks something of the reporter has to say why."""
        for verdict in ("invalid", "cannot_reproduce", "needs_info"):
            with self.subTest(verdict=verdict):
                body = issue_triage._comment(verdict, "summary", "evidence")
                self.assertIn("Automated preliminary check", body)
                self.assertIn("> summary", body)
                self.assertIn("evidence", body)

    async def test_an_unknown_verdict_says_nothing(self) -> None:
        """An off-schema verdict must not produce a comment at all."""
        self.assertIsNone(issue_triage._comment("nonsense", "s", "e"))


class DenyRuleTest(IsolatedAsyncioTestCase):
    """The rules handed to the verifier's permission engine."""

    async def test_the_rules_are_what_they_claim(self) -> None:
        """Written out in full, so a silent narrowing is visible."""
        rules = issue_triage._deny_rules()
        self.assertListEqual(sorted(rules), ["Bash", "Edit", "Write"])
        self.assertListEqual(
            [
                (r.tool_name, r.rule_content, r.behavior, r.source)
                for name in sorted(rules)
                for r in rules[name]
            ],
            [
                ("Bash", "git push", PermissionBehavior.DENY, "issueTriage"),
                ("Bash", "git config", PermissionBehavior.DENY, "issueTriage"),
                ("Bash", "git commit", PermissionBehavior.DENY, "issueTriage"),
                ("Edit", "src/**", PermissionBehavior.DENY, "issueTriage"),
                ("Edit", "tests/**", PermissionBehavior.DENY, "issueTriage"),
                (
                    "Edit",
                    "examples/**",
                    PermissionBehavior.DENY,
                    "issueTriage",
                ),
                ("Write", "src/**", PermissionBehavior.DENY, "issueTriage"),
                ("Write", "tests/**", PermissionBehavior.DENY, "issueTriage"),
                (
                    "Write",
                    "examples/**",
                    PermissionBehavior.DENY,
                    "issueTriage",
                ),
            ],
        )


class PristineCheckoutTest(IsolatedAsyncioTestCase):
    """A verdict must describe the code as it was, not as the agent left it."""

    def _repo(self, tmp: str) -> str:
        """A one-commit repository to run the check against."""
        subprocess.run(["git", "init", "-q", tmp], check=True)
        for key, value in (("user.email", "t@t"), ("user.name", "t")):
            subprocess.run(
                ["git", "-C", tmp, "config", key, value],
                check=True,
            )
        with open(os.path.join(tmp, "a.py"), "w", encoding="utf-8") as f:
            f.write("original\n")
        subprocess.run(["git", "-C", tmp, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", tmp, "commit", "-qm", "init"],
            check=True,
        )
        return tmp

    async def test_an_untouched_checkout_passes(self) -> None:
        """Nothing to complain about when nothing changed."""
        # Cleanup is best-effort: Windows refuses to delete the read-only
        # files git leaves under .git/objects.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            issue_triage._assert_pristine(self._repo(tmp))

    async def test_a_shell_write_is_caught(self) -> None:
        """The deny rules cover Write and Edit; a shell can still reach it."""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            repo = self._repo(tmp)
            with open(os.path.join(repo, "a.py"), "w", encoding="utf-8") as f:
                f.write("instrumented\n")
            with self.assertRaises(RuntimeError) as ctx:
                issue_triage._assert_pristine(repo)
            self.assertIn("does not describe main", str(ctx.exception))


class MissingOutputTest(IsolatedAsyncioTestCase):
    """What happens when the model does not fill the schema."""

    async def test_triage_refuses_a_reply_without_structure(self) -> None:
        """Better to fail the run than to label from nothing."""
        agent = AsyncMock()
        agent.name = "issue-classifier"

        async def _stream(*_args: object, **_kwargs: object) -> Any:
            yield Msg(
                name="a",
                content=[TextBlock(type="text", text="prose")],
                role="assistant",
            )

        agent.reply_stream = _stream
        with self.assertRaises(RuntimeError) as ctx:
            await issue_triage._run(
                agent,
                "<issue/>",
                issue_triage.IssueIntent,
            )
        self.assertIn("no structured output", str(ctx.exception))

    async def test_a_claim_without_structure_is_not_a_claim(self) -> None:
        """Silence hands the issue to nobody, which is the safe answer."""
        msg = Msg(
            name="a",
            content=[TextBlock(type="text", text="prose")],
            role="assistant",
        )
        # A directory rather than NamedTemporaryFile: on Windows the script
        # cannot open a temporary file that the test still holds open.
        with tempfile.TemporaryDirectory() as tmp:
            comment = os.path.join(tmp, "comment.md")
            with open(comment, "w", encoding="utf-8") as f:
                f.write("I would like to work on this")

            with (
                patch.dict(os.environ, {"DASHSCOPE_API_KEY": "test"}),
                patch.object(claim_intent, "DashScopeChatModel"),
                patch.object(claim_intent, "Agent") as agent_cls,
                patch.object(
                    sys,
                    "argv",
                    ["x", "--comment-file", comment, "--model", "m"],
                ),
            ):
                agent_cls.return_value.reply = AsyncMock(return_value=msg)
                with patch("builtins.print") as printed:
                    await claim_intent.main()
        self.assertIn(
            '"wants_to_claim": false',
            printed.call_args_list[0].args[0],
        )
