# -*- coding: utf-8 -*-
"""Per-mode test cases for ``PermissionEngine``.

Each :class:`PermissionMode` has its own test class so the policy of
that mode can be verified in isolation. Tests cover:

- the three rule layers (deny / ask / allow) for that mode
- the ``tool.check_permissions`` return paths (ALLOW / DENY / safety-ASK
  / PASSTHROUGH)
- mode-specific behavior (e.g. EXPLORE's read-only resolution,
  ACCEPT_EDITS's working-directory auto-allow, BYPASS's safety-ASK
  immunity, DONT_ASK's default DENY)
- Bash dynamic read-only / non-read-only / dangerous commands where
  relevant

Specific safety-check triggers (injection, dangerous removal, sed
constraints, dangerous config paths) are tested separately in
``permission_engine_test.py::PermissionEngineSafetyCheckAllowRuleImmuneTest``.
"""
import os
import sys
import tempfile
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.permission import (
    PermissionEngine,
    PermissionMode,
    PermissionContext,
    PermissionRule,
    PermissionBehavior,
    AdditionalWorkingDirectory,
)
from agentscope.tool import (
    Bash,
    Write,
    Read,
    Edit,
)


# ---------------------------------------------------------------------------
# DEFAULT mode
# ---------------------------------------------------------------------------


class PermissionEngineDefaultModeTest(IsolatedAsyncioTestCase):
    """Tests for :attr:`PermissionMode.DEFAULT`.

    DEFAULT is the most restrictive non-DONT_ASK mode: every operation
    requires explicit permission unless an allow rule matches or the
    tool itself returns ALLOW (e.g. Bash auto-allows known read-only
    commands).
    """

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(mode=PermissionMode.DEFAULT)
        self.engine = PermissionEngine(self.context)

    async def test_default_write_with_no_rules_returns_ask(self) -> None:
        """Write to a safe path with no matching rules falls to the
        engine's default ASK."""
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_default_deny_rule_returns_deny(self) -> None:
        """Deny rule has the highest priority."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="*.env",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/secret.env"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_default_ask_rule_returns_ask_with_suggestions(
        self,
    ) -> None:
        """Ask rule short-circuits before the tool's own check and
        attaches ``suggested_rules``."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="*.py",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/main.py"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIsNotNone(decision.suggested_rules)
        self.assertGreater(len(decision.suggested_rules), 0)

    async def test_default_allow_rule_returns_allow(self) -> None:
        """Allow rule grants permission when no deny/ask rule and no
        safety ASK applies."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="/tmp/**",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_default_bash_read_only_command_auto_allows(self) -> None:
        """Bash returns ALLOW for known read-only commands, which the
        engine surfaces directly in DEFAULT mode."""
        for command in ("ls -a", "pwd", "git status", "cat README.md"):
            decision = await self.engine.check_permission(
                Bash(),
                {"command": command},
            )
            self.assertEqual(
                decision.behavior,
                PermissionBehavior.ALLOW,
                f"Expected ALLOW for read-only bash command: {command}",
            )

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_default_bash_modification_command_returns_ask(
        self,
    ) -> None:
        """Bash modification commands (no matching rule) fall through to
        the engine's default ASK."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "npm install"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_default_dangerous_path_safety_ask(self) -> None:
        """Write to a dangerous path produces a safety ASK from the tool
        itself (bypass-immune)."""
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/.bashrc"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("safety", (decision.decision_reason or "").lower())

    async def test_default_safety_ask_not_overridden_by_allow_rule(
        self,
    ) -> None:
        """A user-configured allow rule must not override a tool's
        safety ASK (bypass-immune)."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="**",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/.bashrc"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)


# ---------------------------------------------------------------------------
# EXPLORE mode
# ---------------------------------------------------------------------------


class PermissionEngineExploreModeTest(IsolatedAsyncioTestCase):
    """Tests for :attr:`PermissionMode.EXPLORE`.

    EXPLORE is the read-only mode: an invocation is ALLOWed iff
    :meth:`ToolBase.check_read_only` returns True; everything else is
    DENIed outright. ``tool.check_permissions`` is intentionally not
    consulted (the read-only verdict is final), and allow rules cannot
    grant write access — EXPLORE's read-only invariant is non-negotiable.
    """

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(mode=PermissionMode.EXPLORE)
        self.engine = PermissionEngine(self.context)

    async def test_explore_read_tool_allows(self) -> None:
        """Statically read-only tools (Read/Glob/Grep) → ALLOW."""
        decision = await self.engine.check_permission(
            Read(),
            {"file_path": "/tmp/file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_explore_write_tool_denies(self) -> None:
        """Statically non-read-only tools (Write/Edit) → DENY."""
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_explore_deny_rule_returns_deny(self) -> None:
        """Deny rule still has top priority in EXPLORE."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Read",
                rule_content="/secret/**",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Read(),
            {"file_path": "/secret/key.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_explore_ask_rule_on_read_only_tool_returns_ask(
        self,
    ) -> None:
        """Ask rules apply before the read-only fast-path, so a read-only
        tool with a matching ask rule still surfaces as ASK.

        Documents current behavior — this is the design point tracked
        as issue #7.
        """
        self.engine.add_rule(
            PermissionRule(
                tool_name="Read",
                rule_content="**/*.env",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Read(),
            {"file_path": "/tmp/secret.env"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_explore_allow_rule_does_not_override_deny(self) -> None:
        """EXPLORE's deny on non-read-only tools is the invariant; an
        allow rule must not be able to grant write access."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="**",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_explore_dangerous_path_returns_deny_not_safety_ask(
        self,
    ) -> None:
        """EXPLORE never invokes ``tool.check_permissions``, so a dangerous
        path on a write tool surfaces as DENY (the read-only verdict)
        rather than a safety ASK. DENY is strictly stronger than ASK, so
        this is consistent with EXPLORE's "no writes" guarantee.
        """
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/.bashrc"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_explore_bash_read_only_command_allows(self) -> None:
        """EXPLORE allows read-only bash commands via
        :meth:`Bash.check_read_only` (regression for issue #1)."""
        for command in ("ls -a", "pwd", "git status", "cat README.md"):
            decision = await self.engine.check_permission(
                Bash(),
                {"command": command},
            )
            self.assertEqual(
                decision.behavior,
                PermissionBehavior.ALLOW,
                f"Expected ALLOW for read-only bash command: {command}",
            )

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_explore_bash_modification_command_denies(self) -> None:
        """EXPLORE denies bash commands that are not statically
        recognized as read-only."""
        for command in ("cp a b", "mv a b", "touch /tmp/x"):
            decision = await self.engine.check_permission(
                Bash(),
                {"command": command},
            )
            self.assertEqual(
                decision.behavior,
                PermissionBehavior.DENY,
                f"Expected DENY for non-read-only bash command: {command}",
            )

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_explore_bash_dangerous_command_denies(self) -> None:
        """EXPLORE denies dangerous commands directly — in DEFAULT mode
        the same command would surface as a safety ASK, but EXPLORE is
        stricter."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "rm -rf /"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)


# ---------------------------------------------------------------------------
# ACCEPT_EDITS mode
# ---------------------------------------------------------------------------


class PermissionEngineAcceptEditsModeTest(IsolatedAsyncioTestCase):
    """Tests for :attr:`PermissionMode.ACCEPT_EDITS`.

    ACCEPT_EDITS auto-allows file edits within configured working
    directories; reads are auto-allowed unconditionally; other
    operations follow the normal DEFAULT-like flow.
    """

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(
            mode=PermissionMode.ACCEPT_EDITS,
            working_directories={
                "/tmp/project": AdditionalWorkingDirectory(
                    path="/tmp/project",
                    source="test",
                ),
            },
        )
        self.engine = PermissionEngine(self.context)

    async def test_accept_edits_within_working_directory(self) -> None:
        """Write / Read / Edit within a working directory → ALLOW."""
        for tool in (Write(), Read(), Edit()):
            decision = await self.engine.check_permission(
                tool,
                {"file_path": "/tmp/project/file.txt"},
            )
            self.assertEqual(
                decision.behavior,
                PermissionBehavior.ALLOW,
                f"Expected ALLOW for {tool.name} in working directory",
            )

    @unittest.skipIf(
        os.name == "nt",
        "os.symlink typically requires admin privileges on Windows",
    )
    async def test_accept_edits_resolves_symlinked_working_directory(
        self,
    ) -> None:
        """Working directory comparison must use ``realpath`` so a path
        reached through a symlink (e.g. macOS's ``/tmp`` ->
        ``/private/tmp``) is recognized. Regression test for the
        ``abspath`` → ``realpath`` fix in
        :meth:`_path_in_allowed_working_path`.
        """
        parent = tempfile.mkdtemp()
        try:
            real_dir = os.path.join(parent, "real")
            os.makedirs(real_dir)
            link_dir = os.path.join(parent, "link")
            os.symlink(real_dir, link_dir)

            # Case 1: working_dir given as real path, file via link
            context = PermissionContext(
                mode=PermissionMode.ACCEPT_EDITS,
                working_directories={
                    real_dir: AdditionalWorkingDirectory(
                        path=real_dir,
                        source="test",
                    ),
                },
            )
            engine = PermissionEngine(context)
            decision = await engine.check_permission(
                Write(),
                {"file_path": os.path.join(link_dir, "file.txt")},
            )
            self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

            # Case 2: working_dir given as link, file via real path
            context = PermissionContext(
                mode=PermissionMode.ACCEPT_EDITS,
                working_directories={
                    link_dir: AdditionalWorkingDirectory(
                        path=link_dir,
                        source="test",
                    ),
                },
            )
            engine = PermissionEngine(context)
            decision = await engine.check_permission(
                Edit(),
                {"file_path": os.path.join(real_dir, "file.txt")},
            )
            self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)
        finally:
            import shutil

            shutil.rmtree(parent, ignore_errors=True)

    async def test_accept_edits_outside_working_directory(self) -> None:
        """Edit outside a working directory falls to the default ASK."""
        decision = await self.engine.check_permission(
            Edit(),
            {"file_path": "/home/user/file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_accept_edits_read_operation_auto_allowed(self) -> None:
        """Read tool is auto-allowed regardless of path (read-only fast
        path)."""
        decision = await self.engine.check_permission(
            Read(),
            {"file_path": "/anywhere/file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_accept_edits_deny_rule_returns_deny(self) -> None:
        """Deny rule overrides ACCEPT_EDITS's working-directory
        auto-allow."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="**/*.lock",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/project/poetry.lock"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_accept_edits_ask_rule_returns_ask(self) -> None:
        """Ask rule short-circuits before the working-directory check."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Edit",
                rule_content="**/*.py",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Edit(),
            {"file_path": "/tmp/project/main.py"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_accept_edits_dangerous_path_safety_ask(self) -> None:
        """Safety ASK from a dangerous path is bypass-immune even when
        the path is inside a working directory.
        """
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/.bashrc"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("safety", (decision.decision_reason or "").lower())

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_accept_edits_bash_read_only_command_allows(self) -> None:
        """Read-only bash commands ALLOW via the read-only fast path."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "ls -a"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_accept_edits_bash_filesystem_command_inside_working_dir(
        self,
    ) -> None:
        """ACCEPT_EDITS auto-allows recognized filesystem commands
        (``mkdir``, ``touch``, ``rm``, ``cp``, ``mv``, ``sed``,
        ``rmdir``) when **all** target paths are inside the configured
        working directories."""
        cases = [
            "touch /tmp/project/new.txt",
            "mkdir /tmp/project/newdir",
            "rm /tmp/project/old.txt",
            "rmdir /tmp/project/olddir",
            "cp /tmp/project/a /tmp/project/b",
            "mv /tmp/project/a /tmp/project/b",
            "sed -i 's/x/y/g' /tmp/project/foo.txt",
        ]
        for command in cases:
            decision = await self.engine.check_permission(
                Bash(),
                {"command": command},
            )
            self.assertEqual(
                decision.behavior,
                PermissionBehavior.ALLOW,
                f"Expected ALLOW for in-working-dir command: {command}",
            )

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_accept_edits_bash_filesystem_command_outside_working_dir(
        self,
    ) -> None:
        """Regression for issue #4: filesystem commands whose targets
        escape the working directory must NOT be auto-allowed.

        Without this guard ``cp /etc/hosts /Users/me/other-project/x``
        would silently succeed in ACCEPT_EDITS even though the
        equivalent ``Write`` call is correctly denied. The asserted
        behavior is ASK (the PASSTHROUGH from Bash + the engine's
        default ASK in ACCEPT_EDITS).
        """
        cases = [
            # Target entirely outside the working directory
            "rm /Users/someone/other/foo",
            "touch /Users/someone/other/foo",
            "mkdir /Users/someone/other/newdir",
            # cp / mv: at least one of (src, dst) outside
            "cp /tmp/project/a /Users/someone/other/b",
            "cp /Users/someone/other/a /tmp/project/b",
            "mv /tmp/project/a /Users/someone/other/b",
            # sed in-place modifying a file outside
            "sed -i 's/x/y/g' /Users/someone/other/foo.txt",
        ]
        for command in cases:
            decision = await self.engine.check_permission(
                Bash(),
                {"command": command},
            )
            self.assertEqual(
                decision.behavior,
                PermissionBehavior.ASK,
                f"Expected ASK for outside-working-dir command: {command}",
            )

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_accept_edits_bash_filesystem_command_no_args_not_allowed(
        self,
    ) -> None:
        """Conservative behavior: if the parser extracts no target paths
        (e.g. a bare ``mkdir`` with no arguments), we do not auto-allow.
        The command itself will fail at execution, but it should not
        silently pass the permission check."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "mkdir"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)


# ---------------------------------------------------------------------------
# BYPASS mode
# ---------------------------------------------------------------------------


class PermissionEngineBypassModeTest(IsolatedAsyncioTestCase):
    """Tests for :attr:`PermissionMode.BYPASS`.

    BYPASS is the "fully trusted" mode: the user has opted out of all
    safety prompts. The only guardrails left are user-configured deny
    / ask rules and tool-emitted DENY. Every bypass-immune safety ASK
    (dangerous removal, dangerous paths, sed in-place on sensitive
    files, command injection, ...) is intentionally **skipped**.
    Use BYPASS only in sandboxed environments or when you fully trust
    the agent.
    """

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(mode=PermissionMode.BYPASS)
        self.engine = PermissionEngine(self.context)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_bypass_no_rules_allows(self) -> None:
        """No rules → ALLOW (BYPASS's default fallback)."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "npm install"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_bypass_deny_rule_returns_deny(self) -> None:
        """Deny rules are bypass-immune."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="rm:*",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "rm -rf /tmp/foo"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_bypass_ask_rule_returns_ask(self) -> None:
        """A user-configured ask rule represents explicit intent to be
        prompted; BYPASS must not override it."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="git push:*",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git push origin main"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_bypass_skips_dangerous_path_safety(self) -> None:
        """BYPASS skips the Write tool's dangerous-path safety check —
        writing to ``~/.bashrc`` is allowed through."""
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/.bashrc"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_bypass_skips_dangerous_removal(self) -> None:
        """BYPASS skips Bash's dangerous-removal safety check —
        ``rm -rf /`` is allowed through."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "rm -rf /"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_bypass_skips_command_injection(self) -> None:
        """BYPASS skips Bash's command-injection safety check —
        dynamic expansion is allowed through."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "ls $(date +%s)"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_bypass_skips_sed_dangerous_file(self) -> None:
        """BYPASS skips Bash's sed-on-dangerous-file safety check."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "sed 's/old/new/e' file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_bypass_skips_dangerous_config_path_in_bash(self) -> None:
        """BYPASS skips Bash's dangerous-config-path safety check —
        operating on ``~/.bashrc`` via bash is allowed through."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "rm ~/.bashrc"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_bypass_tool_allow_returns_allow(self) -> None:
        """Tool's own ALLOW (e.g. Bash read-only command) is returned
        as-is in BYPASS as in any mode."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "ls -a"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)


# ---------------------------------------------------------------------------
# DONT_ASK mode
# ---------------------------------------------------------------------------


class PermissionEngineDontAskModeTest(IsolatedAsyncioTestCase):
    """Tests for :attr:`PermissionMode.DONT_ASK`.

    DONT_ASK is used when no user is available to answer prompts
    (scheduled tasks, background runs). The invariant is "never return
    ASK" — every ASK-producing code path (default, ASK rule, safety
    ASK) is converted to DENY.
    """

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(mode=PermissionMode.DONT_ASK)
        self.engine = PermissionEngine(self.context)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_dont_ask_no_rules_returns_deny(self) -> None:
        """No rules → DENY (DONT_ASK's default)."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "npm install"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_dont_ask_deny_rule_returns_deny(self) -> None:
        """Deny rules apply normally."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="rm:*",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "rm -rf /tmp/foo"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_dont_ask_allow_rule_returns_allow(self) -> None:
        """An explicit allow rule still grants permission in DONT_ASK."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="/tmp/**",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/file.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_dont_ask_bash_read_only_command_allows(self) -> None:
        """Tool's own ALLOW (Bash read-only command) is still ALLOW
        under DONT_ASK — no user prompt is needed."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "ls -a"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_dont_ask_ask_rule_returns_deny(self) -> None:
        """An ASK rule hit is converted to DENY (issue #3): the user is
        not available to answer the prompt, so the operation cannot
        proceed."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="**/*.py",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/main.py"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_dont_ask_ask_rule_conversion_preserves_suggestions(
        self,
    ) -> None:
        """Converted DENY decisions keep the original ASK's
        ``suggested_rules`` so callers can surface them to the user
        out-of-band (e.g. in a scheduled-task failure report)."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="**/*.py",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/tmp/main.py"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)
        self.assertIsNotNone(decision.suggested_rules)
        self.assertGreater(len(decision.suggested_rules), 0)

    async def test_dont_ask_safety_ask_returns_deny(self) -> None:
        """A safety ASK from ``tool.check_permissions`` (e.g. Write to a
        dangerous path) is converted to DENY (issue #3) — DONT_ASK
        respects the safety verdict but cannot ask the user, so the
        only safe action is to refuse."""
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/.bashrc"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)
        # Conversion records both the original safety reason and the
        # DONT_ASK conversion in the decision_reason.
        reason = (decision.decision_reason or "").lower()
        self.assertIn("dont_ask", reason)
        self.assertIn("safety", reason)

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_dont_ask_bash_dangerous_command_returns_deny(
        self,
    ) -> None:
        """``rm -rf /`` triggers a safety ASK from Bash, which DONT_ASK
        converts to DENY (issue #3)."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "rm -rf /"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)
