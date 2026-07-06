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
    PermissionDecision,
    PermissionResolution,
    AdditionalWorkingDirectory,
)
from agentscope.tool import (
    Bash,
    Write,
    Read,
    Edit,
    ToolBase,
)


class _AlwaysDenyTool(ToolBase):
    """A tool whose ``check_permissions`` always returns DENY.

    Used to exercise the BYPASS path where a tool-emitted DENY is
    returned as-is (not suppressed like an ASK).
    """

    name: str = "always_deny"
    description: str = "Always deny."
    input_schema: dict = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.DENY,
            message="tool-emitted DENY",
        )

    async def __call__(self) -> None:
        return None


class _AlwaysAskTool(ToolBase):
    """Tool that requests confirmation for a sensitive transfer."""

    name: str = "always_ask"
    description: str = "Request confirmation before transferring data."
    input_schema: dict = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    is_read_only: bool = False

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="Confirm outbound transfer",
            decision_reason="Tool detected a sensitive transfer",
        )

    async def __call__(self, value: str) -> str:
        return value


class _PassthroughTool(ToolBase):
    """Tool that deliberately expresses no permission opinion."""

    name: str = "passthrough"
    description: str = "Delegate permission policy to the engine."
    input_schema: dict = {
        "type": "object",
        "properties": {"value": {"type": "string"}},
        "required": ["value"],
    }
    is_read_only: bool = False

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.PASSTHROUGH,
            message="No tool-level permission opinion",
        )

    async def __call__(self, value: str) -> str:
        return value


def test_allow_rule_override_resolution_is_public() -> None:
    """ALLOW-rule ASK overrides have a stable machine-readable label."""
    assert (
        PermissionResolution.ASK_OVERRIDDEN_BY_ALLOW_RULE.value
        == "ask_overridden_by_allow_rule"
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


class PermissionEvaluationDefaultModeTest(IsolatedAsyncioTestCase):
    """evaluate_permission returns structured evaluations for DEFAULT mode."""

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(mode=PermissionMode.DEFAULT)
        self.engine = PermissionEngine(self.context)

    async def test_deny_rule_is_direct(self) -> None:
        """A DEFAULT deny-rule result is a direct evaluation."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="rm *",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "rm foo"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )
        assert evaluation.mode == PermissionMode.DEFAULT

    async def test_safety_ask_is_direct(self) -> None:
        """A DEFAULT safety ASK remains a direct evaluation."""
        # rm -rf / triggers bypass-immune safety ASK; in DEFAULT it is
        # honored (returned as-is, not suppressible by allow rules).
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "rm -rf /"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert evaluation.effective_decision.behavior == PermissionBehavior.ASK
        assert evaluation.effective_decision.bypass_immune is True

    async def test_read_only_command_is_direct_allow(self) -> None:
        """A DEFAULT read-only ALLOW remains direct."""
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "ls"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )


class PermissionEvaluationBypassModeTest(IsolatedAsyncioTestCase):
    """evaluate_permission exposes ASKs suppressed by BYPASS."""

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(mode=PermissionMode.BYPASS)
        self.engine = PermissionEngine(self.context)

    async def test_tool_allow_is_direct(self) -> None:
        """A tool-emitted ALLOW remains direct in BYPASS."""
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "ls"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )

    async def test_safety_ask_suppressed_to_allow(self) -> None:
        """Preserve a safety ASK suppressed by BYPASS fallback.

        Scenario:
            A BYPASS session invokes ``rm -rf /``; Bash flags the command
            with a bypass-immune ASK, while the mode permits execution.

        Expected evaluation:
            candidate=ASK(bypass_immune=True), effective=ALLOW, and
            resolution=BYPASS_ASK_SUPPRESSED.

        Audit significance:
            The ALLOW is attributable to an intentional mode override rather
            than being mistaken for a command Bash considered safe.
        """
        # rm -rf / -> bypass-immune safety ASK, suppressed to ALLOW.
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "rm -rf /"},
        )
        assert (
            evaluation.resolution == PermissionResolution.BYPASS_ASK_SUPPRESSED
        )
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )
        assert evaluation.candidate_decision is not None
        assert evaluation.candidate_decision.behavior == PermissionBehavior.ASK
        assert evaluation.candidate_decision.bypass_immune is True

    async def test_allow_rule_after_ask_is_suppressed(self) -> None:
        """Preserve a safety ASK suppressed through a BYPASS allow rule.

        Scenario:
            Bash flags a destructive command with a safety ASK and a
            matching user-configured allow rule permits it in BYPASS.

        Expected evaluation:
            candidate=ASK(bypass_immune=True), effective=ALLOW, and
            resolution=BYPASS_ASK_SUPPRESSED.

        Audit significance:
            The record retains Bash's warning even when explicit policy and
            BYPASS jointly authorize the command.
        """
        # An allow rule that converts a suppressed ASK into ALLOW still
        # records the suppressed ASK as the candidate.
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="rm *",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "rm -rf /"},
        )
        assert (
            evaluation.resolution == PermissionResolution.BYPASS_ASK_SUPPRESSED
        )
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )
        assert evaluation.candidate_decision is not None
        assert evaluation.candidate_decision.behavior == PermissionBehavior.ASK
        assert evaluation.candidate_decision.bypass_immune is True

    async def test_deny_rule_is_direct(self) -> None:
        """A BYPASS deny-rule result remains direct."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="rm *",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "rm foo"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )

    async def test_tool_emitted_deny_is_direct(self) -> None:
        """A tool-emitted DENY remains direct in BYPASS."""
        # BYPASS returns a tool-emitted DENY as-is (only ASKs are
        # suppressed); no candidate is preserved.
        evaluation = await self.engine.evaluate_permission(
            _AlwaysDenyTool(),
            {},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )


class PermissionEvaluationDontAskModeTest(IsolatedAsyncioTestCase):
    """evaluate_permission exposes ASKs converted to DENY by DONT_ASK."""

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(mode=PermissionMode.DONT_ASK)
        self.engine = PermissionEngine(self.context)

    async def test_safety_ask_converted_to_deny(self) -> None:
        """Preserve a safety ASK converted into DENY by DONT_ASK.

        Scenario:
            An unattended task invokes ``rm -rf /`` and cannot present
            Bash's safety confirmation request to a user.

        Expected evaluation:
            candidate=ASK(bypass_immune=True), effective=DENY, and
            resolution=ASK_CONVERTED_TO_DENY.

        Audit significance:
            The DENY is identifiable as unavailable confirmation rather than
            an explicit deny rule or invalid input.
        """
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "rm -rf /"},
        )
        assert (
            evaluation.resolution == PermissionResolution.ASK_CONVERTED_TO_DENY
        )
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )
        assert evaluation.candidate_decision is not None
        assert evaluation.candidate_decision.behavior == PermissionBehavior.ASK
        assert evaluation.candidate_decision.bypass_immune is True

    async def test_ask_rule_converted_to_deny(self) -> None:
        """Preserve an ask-rule decision converted by DONT_ASK.

        Scenario:
            An unattended task matches a user-configured ask rule, but no
            user is available to answer the requested confirmation.

        Expected evaluation:
            candidate=ASK, effective=DENY, and
            resolution=ASK_CONVERTED_TO_DENY.

        Audit significance:
            Operators can see that policy requested confirmation instead of
            interpreting the result as a direct prohibition.
        """
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="rm *",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "rm foo"},
        )
        assert (
            evaluation.resolution == PermissionResolution.ASK_CONVERTED_TO_DENY
        )
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )
        assert evaluation.candidate_decision is not None
        assert evaluation.candidate_decision.behavior == PermissionBehavior.ASK

    async def test_read_only_is_direct_allow(self) -> None:
        """A DONT_ASK read-only ALLOW remains direct."""
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "ls"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )

    @unittest.skipIf(
        sys.platform == "win32",
        "Bash tool is not supported on Windows",
    )
    async def test_default_is_direct_deny(self) -> None:
        """The DONT_ASK fallback DENY is direct."""
        # Non-read-only, no rule, no safety ASK -> default DENY (no user).
        evaluation = await self.engine.evaluate_permission(
            Bash(),
            {"command": "npm install"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )


class PermissionEvaluationExploreModeTest(IsolatedAsyncioTestCase):
    """evaluate_permission for EXPLORE mode (all DIRECT — no transformation).

    EXPLORE never transforms a candidate — read-only verdicts are final.
    """

    async def asyncSetUp(self) -> None:
        self.context = PermissionContext(mode=PermissionMode.EXPLORE)
        self.engine = PermissionEngine(self.context)

    async def test_read_tool_is_direct_allow(self) -> None:
        """EXPLORE reports a read-only ALLOW directly."""
        evaluation = await self.engine.evaluate_permission(
            Read(),
            {"file_path": "/tmp/file.txt"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )

    async def test_write_tool_is_direct_deny(self) -> None:
        """EXPLORE reports a write DENY directly."""
        evaluation = await self.engine.evaluate_permission(
            Write(),
            {"file_path": "/tmp/file.txt"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )


class PermissionEvaluationAcceptEditsModeTest(IsolatedAsyncioTestCase):
    """Direct ACCEPT_EDITS evaluation paths."""

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

    async def test_deny_rule_is_direct(self) -> None:
        """ACCEPT_EDITS reports a deny-rule result directly."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Write",
                rule_content="*.env",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        evaluation = await self.engine.evaluate_permission(
            Write(),
            {"file_path": "/tmp/secret.env"},
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )

    async def test_allowed_edit_within_working_directory_is_direct(
        self,
    ) -> None:
        """ACCEPT_EDITS reports an allowed workspace edit directly."""
        # Write/Edit within a configured working directory is auto-allowed
        # by the tool's own check_permissions — ACCEPT_EDITS surfaces it
        # directly (no mode transformation).
        for tool in (Write(), Edit()):
            evaluation = await self.engine.evaluate_permission(
                tool,
                {"file_path": "/tmp/project/file.txt"},
            )
            assert evaluation.resolution == PermissionResolution.DIRECT
            assert evaluation.candidate_decision is None
            assert (
                evaluation.effective_decision.behavior
                == PermissionBehavior.ALLOW
            )


class PermissionEvaluationAskTransitionTest(IsolatedAsyncioTestCase):
    """Behavior-changing ordinary ASK transitions retain their candidate."""

    @staticmethod
    def _allow_all_rule(tool_name: str) -> PermissionRule:
        """Build a tool-level ALLOW rule for a deterministic test tool."""
        return PermissionRule(
            tool_name=tool_name,
            rule_content=None,
            behavior=PermissionBehavior.ALLOW,
            source="test",
        )

    async def test_default_allow_rule_preserves_overridden_ask(self) -> None:
        """Preserve a tool ASK overridden by a DEFAULT allow rule.

        Scenario:
            A credential-transfer tool requests confirmation, while a
            matching user-configured allow rule permits the operation.

        Expected evaluation:
            candidate=ASK, effective=ALLOW, and
            resolution=ASK_OVERRIDDEN_BY_ALLOW_RULE.

        Audit significance:
            The candidate distinguishes an explicit policy override from a
            tool decision that considered the transfer safe.
        """
        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.DEFAULT),
        )
        engine.add_rule(self._allow_all_rule(_AlwaysAskTool.name))

        evaluation = await engine.evaluate_permission(
            _AlwaysAskTool(),
            {"value": "credential payload"},
        )

        assert evaluation.candidate_decision is not None
        assert evaluation.candidate_decision.behavior == PermissionBehavior.ASK
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )
        assert (
            evaluation.resolution
            == PermissionResolution.ASK_OVERRIDDEN_BY_ALLOW_RULE
        )

    async def test_accept_edits_allow_rule_preserves_overridden_ask(
        self,
    ) -> None:
        """Preserve a tool ASK overridden in ACCEPT_EDITS.

        Scenario:
            An edit-capable session invokes a sensitive transfer tool whose
            ordinary ASK is covered by a user-configured allow rule.

        Expected evaluation:
            candidate=ASK, effective=ALLOW, and
            resolution=ASK_OVERRIDDEN_BY_ALLOW_RULE.

        Audit significance:
            The record shows that ACCEPT_EDITS did not independently consider
            the transfer safe; an explicit allow rule overrode the tool ASK.
        """
        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.ACCEPT_EDITS),
        )
        engine.add_rule(self._allow_all_rule(_AlwaysAskTool.name))

        evaluation = await engine.evaluate_permission(
            _AlwaysAskTool(),
            {"value": "credential payload"},
        )

        assert evaluation.candidate_decision is not None
        assert evaluation.candidate_decision.behavior == PermissionBehavior.ASK
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )
        assert (
            evaluation.resolution
            == PermissionResolution.ASK_OVERRIDDEN_BY_ALLOW_RULE
        )

    async def test_dont_ask_allow_rule_preserves_overridden_ask(self) -> None:
        """Preserve a tool ASK overridden by a DONT_ASK allow rule.

        Scenario:
            An unattended task reaches a sensitive transfer that requests
            confirmation, but a preconfigured allow rule authorizes it.

        Expected evaluation:
            candidate=ASK, effective=ALLOW, and
            resolution=ASK_OVERRIDDEN_BY_ALLOW_RULE.

        Audit significance:
            Operators can distinguish preauthorization from a tool-level
            safety approval during unattended execution.
        """
        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.DONT_ASK),
        )
        engine.add_rule(self._allow_all_rule(_AlwaysAskTool.name))

        evaluation = await engine.evaluate_permission(
            _AlwaysAskTool(),
            {"value": "credential payload"},
        )

        assert evaluation.candidate_decision is not None
        assert evaluation.candidate_decision.behavior == PermissionBehavior.ASK
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )
        assert (
            evaluation.resolution
            == PermissionResolution.ASK_OVERRIDDEN_BY_ALLOW_RULE
        )

    async def test_dont_ask_fallback_preserves_converted_ask(self) -> None:
        """Preserve a tool ASK converted by the DONT_ASK fallback.

        Scenario:
            An unattended task requests a sensitive transfer and has no
            matching allow rule or available user to confirm it.

        Expected evaluation:
            candidate=ASK, effective=DENY, and
            resolution=ASK_CONVERTED_TO_DENY.

        Audit significance:
            The DENY can be diagnosed as unavailable human confirmation
            instead of being mistaken for an explicit deny rule.
        """
        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.DONT_ASK),
        )

        evaluation = await engine.evaluate_permission(
            _AlwaysAskTool(),
            {"value": "credential payload"},
        )

        assert evaluation.candidate_decision is not None
        assert evaluation.candidate_decision.behavior == PermissionBehavior.ASK
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.DENY
        )
        assert (
            evaluation.resolution == PermissionResolution.ASK_CONVERTED_TO_DENY
        )

    async def test_passthrough_allow_rule_remains_direct(self) -> None:
        """Keep PASSTHROUGH outside the security-candidate contract.

        Scenario:
            A tool delegates permission policy without expressing an ASK,
            and a tool-level allow rule authorizes the call.

        Expected evaluation:
            candidate=None, effective=ALLOW, and resolution=DIRECT.

        Audit significance:
            Candidate records remain reserved for actual security judgments,
            avoiding noisy traces for tools that expressed no opinion.
        """
        engine = PermissionEngine(
            PermissionContext(mode=PermissionMode.DEFAULT),
        )
        engine.add_rule(self._allow_all_rule(_PassthroughTool.name))

        evaluation = await engine.evaluate_permission(
            _PassthroughTool(),
            {"value": "ordinary payload"},
        )

        assert evaluation.candidate_decision is None
        assert (
            evaluation.effective_decision.behavior == PermissionBehavior.ALLOW
        )
        assert evaluation.resolution == PermissionResolution.DIRECT
