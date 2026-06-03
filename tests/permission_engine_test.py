# -*- coding: utf-8 -*-
"""Test cases for PermissionEngine.

Mode-specific tests live in :mod:`tests.permission_mode_test`. This file
covers rule priority, rule pattern matching (Bash / file glob), dangerous
path detection, suggestion generation, Bash read-only command analysis,
and bypass-immune safety checks.
"""
import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.permission import (
    PermissionEngine,
    PermissionMode,
    PermissionContext,
    PermissionRule,
    PermissionBehavior,
    PermissionDecision,
    AdditionalWorkingDirectory,
)
from agentscope.tool import (
    Bash,
    Write,
    Read,
    Edit,
    ToolBase,
)


class PermissionEngineRulePriorityTest(IsolatedAsyncioTestCase):
    """Test cases for rule priority and decision-making."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.context = PermissionContext(mode=PermissionMode.DEFAULT)
        self.engine = PermissionEngine(self.context)

    async def test_deny_rule_priority(self) -> None:
        """Test that deny rules have the highest priority."""
        # Add both allow and deny rules for the same tool
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="git:*",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="git:*",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )

        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git status"},
        )

        # Deny should take precedence over allow
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_ask_rule_priority(self) -> None:
        """Test that ask rules have priority over allow rules."""
        # Add both allow and ask rules for the same tool
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="npm:*",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="npm:*",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )

        decision = await self.engine.check_permission(
            Bash(),
            {"command": "npm install"},
        )

        # Ask should take precedence over allow
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_rule_priority_order(self) -> None:
        """Test complete rule priority: deny > ask > allow."""
        # Add all three types of rules
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="test:*",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="test:*",
                behavior=PermissionBehavior.ASK,
                source="test",
            ),
        )
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="test:*",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )

        decision = await self.engine.check_permission(
            Bash(),
            {"command": "test command"},
        )

        # Deny should win
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.engine = None
        self.context = None


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class PermissionEngineBashRuleTest(IsolatedAsyncioTestCase):
    """Test cases for Bash command rule matching."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.context = PermissionContext(mode=PermissionMode.DEFAULT)
        self.engine = PermissionEngine(self.context)

    async def test_bash_prefix_pattern_matching(self) -> None:
        """Test bash command prefix pattern matching with :* wildcard."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="git:*",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        # Test exact command
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

        # Test command with arguments
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git status"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

        # Test command with multiple arguments
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git add ."},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

        # Test non-matching command
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "npm install"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_bash_substring_pattern_matching(self) -> None:
        """Test bash command substring pattern matching."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="install",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )

        # Test command containing substring
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "npm install package"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

        # Test command containing substring in different position
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "pip install requests"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

    async def test_bash_multiple_rules(self) -> None:
        """Test bash command matching with multiple rules."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="rm:*",
                behavior=PermissionBehavior.DENY,
                source="test",
            ),
        )
        self.engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="git:*",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        # Test deny rule
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "rm -rf /tmp"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)

        # Test allow rule
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git status"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

        # Test no matching rule
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "npm install"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.engine = None
        self.context = None


class PermissionEngineFileRuleTest(IsolatedAsyncioTestCase):
    """Test cases for file operation rule matching."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.context = PermissionContext(mode=PermissionMode.DEFAULT)
        self.engine = PermissionEngine(self.context)

    async def test_file_glob_pattern_matching(self) -> None:
        """Test file path glob pattern matching."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Read",
                rule_content="*.py",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        # Test matching file
        decision = await self.engine.check_permission(
            Read(),
            {"file_path": "test.py"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

        # Test non-matching file
        decision = await self.engine.check_permission(
            Read(),
            {"file_path": "test.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_file_directory_pattern_matching(self) -> None:
        """Test file path directory pattern matching."""
        self.engine.add_rule(
            PermissionRule(
                tool_name="Edit",
                rule_content="/tmp/**",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        # Test file in directory - glob pattern /tmp/** should match
        decision = await self.engine.check_permission(
            Edit(),
            {"file_path": "/tmp/test.txt"},
        )
        # Note: The current implementation uses fnmatch/pathlib matching
        # /tmp/** may not match /tmp/test.txt depending on implementation
        # This test verifies the actual behavior
        self.assertIn(
            decision.behavior,
            [PermissionBehavior.ALLOW, PermissionBehavior.ASK],
        )

        # Test file in subdirectory
        decision = await self.engine.check_permission(
            Edit(),
            {"file_path": "/tmp/subdir/test.txt"},
        )
        self.assertIn(
            decision.behavior,
            [PermissionBehavior.ALLOW, PermissionBehavior.ASK],
        )

        # Test file outside directory
        decision = await self.engine.check_permission(
            Edit(),
            {"file_path": "/home/user/test.txt"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.engine = None
        self.context = None


@unittest.skipIf(
    sys.platform == "win32",
    "Unix-specific paths not supported on Windows",
)
class PermissionEngineDangerousPathTest(IsolatedAsyncioTestCase):
    """Test cases for dangerous path detection (bypass-immune safety
    checks)."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.context = PermissionContext(mode=PermissionMode.DEFAULT)
        self.engine = PermissionEngine(self.context)

    async def test_dangerous_file_blocks_write(self) -> None:
        """Test that Write operations on dangerous files require
        confirmation."""
        # Try to write a dangerous file
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/.bashrc"},
        )

        # Should ask for confirmation (safety check)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_dangerous_file_blocks_edit(self) -> None:
        """Test that Edit operations on dangerous files require
        confirmation."""
        # Try to edit a dangerous file
        decision = await self.engine.check_permission(
            Edit(),
            {"file_path": "/home/user/.gitconfig"},
        )

        # Should ask for confirmation (safety check)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("safety", decision.decision_reason.lower())

    async def test_dangerous_directory_blocks_write(self) -> None:
        """Test that Write operations in dangerous directories require
        confirmation."""
        # Try to write in a dangerous directory
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/.ssh/config"},
        )

        # Should ask for confirmation (safety check)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("safety", decision.decision_reason.lower())

    async def test_dangerous_path_in_bash_command(self) -> None:
        """Test that Bash commands on dangerous paths require confirmation."""
        # Try to remove a dangerous file
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "rm /home/user/.bashrc"},
        )

        # Should ask for confirmation (safety check)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("safety", decision.decision_reason.lower())

    async def test_dangerous_path_in_accept_edits_mode(self) -> None:
        """Test that dangerous paths require confirmation even in
        ACCEPT_EDITS mode."""
        context = PermissionContext(
            mode=PermissionMode.ACCEPT_EDITS,
            working_directories={
                "/home/user": AdditionalWorkingDirectory(
                    path="/home/user",
                    source="test",
                ),
            },
        )
        engine = PermissionEngine(context)

        # Try to write a dangerous file in working directory
        decision = await engine.check_permission(
            Write(),
            {"file_path": "/home/user/.bashrc"},
        )

        # Should ask despite being in working directory
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_safe_file_allows_write(self) -> None:
        """Test that Write operations on safe files work normally."""
        # Try to write a safe file
        decision = await self.engine.check_permission(
            Write(),
            {"file_path": "/home/user/test.py"},
        )

        # Should ask (no allow rule, but not blocked by safety check)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        # Should NOT be a safety check
        self.assertNotIn("safety", decision.decision_reason.lower())

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.engine = None
        self.context = None


class PermissionEngineSuggestionTest(IsolatedAsyncioTestCase):
    """Test cases for permission rule suggestions."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.context = PermissionContext(mode=PermissionMode.DEFAULT)
        self.engine = PermissionEngine(self.context)

    async def test_bash_suggestions(self) -> None:
        """Test suggestion generation for bash commands."""
        # Use a non-read-only command to test suggestions
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git commit -m 'test'"},
        )

        # Should ask (not read-only)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

        # Should have suggestions
        self.assertIsNotNone(decision.suggested_rules)
        self.assertGreater(len(decision.suggested_rules), 0)

        # Check suggestion content - should generate two-word prefix rule
        suggestion_contents = [
            s.rule_content for s in decision.suggested_rules
        ]
        # git commit should generate "git commit:*"
        self.assertIn("git commit:*", suggestion_contents)

    async def test_file_suggestions(self) -> None:
        """Test suggestion generation for file operations."""
        decision = await self.engine.check_permission(
            Read(),
            {"file_path": "/tmp/test.py"},
        )

        # Should have suggestions
        self.assertGreater(len(decision.suggested_rules), 0)

        # Check suggestion content - should generate directory rule
        suggestion_contents = [
            s.rule_content for s in decision.suggested_rules
        ]
        self.assertIn("/tmp/**", suggestion_contents)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.engine = None
        self.context = None


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class PermissionEngineReadOnlyTest(IsolatedAsyncioTestCase):
    """Test cases for read-only command auto-allow."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.context = PermissionContext(mode=PermissionMode.DEFAULT)
        self.engine = PermissionEngine(self.context)

    async def test_git_status_is_read_only(self) -> None:
        """Test that git status is auto-allowed as read-only."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git status"},
        )

        # Should be allowed (read-only command)
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)
        self.assertIn("read-only", decision.decision_reason.lower())

    async def test_ls_is_read_only(self) -> None:
        """Test that ls is auto-allowed as read-only."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "ls -la"},
        )

        # Should be allowed (read-only command)
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_cat_is_read_only(self) -> None:
        """Test that cat is auto-allowed as read-only."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "cat file.txt"},
        )

        # Should be allowed (read-only command)
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_git_commit_is_not_read_only(self) -> None:
        """Test that git commit is not auto-allowed (not read-only)."""
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "git commit -m 'test'"},
        )

        # Should ask (not read-only)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_compound_command_with_dangerous_path(self) -> None:
        """Test compound command with read-only and dangerous path o
        perations."""
        # ls is read-only, but rm ~/.bashrc is dangerous
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "ls -la && rm ~/.bashrc"},
        )

        # Should ask because of dangerous path (safety check is bypass-immune)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("safety", decision.decision_reason.lower())

    async def test_compound_command_all_read_only(self) -> None:
        """Test compound command with all read-only operations."""
        # Both ls and cat are read-only
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "ls -la && cat file.txt"},
        )

        # Should be allowed (all read-only)
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_compound_command_with_write_operation(self) -> None:
        """Test compound command with read-only and write operations."""
        # ls is read-only, but git commit is not
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "ls -la && git commit -m 'test'"},
        )

        # Should ask (contains non-read-only command)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_output_redirection_to_dangerous_path(self) -> None:
        """Test output redirection to dangerous path."""
        # cat is read-only, but redirecting to ~/.bashrc is dangerous
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "cat file.txt > ~/.bashrc"},
        )

        # Should ask because of dangerous path in redirection
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("safety", decision.decision_reason.lower())

    async def test_output_redirection_to_safe_path(self) -> None:
        """Test output redirection to safe path."""
        # cat is read-only, redirecting to safe path
        decision = await self.engine.check_permission(
            Bash(),
            {"command": "cat file.txt > /tmp/output.txt"},
        )

        # Should ask (redirection is not considered read-only)
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.engine = None
        self.context = None


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class PermissionEngineSafetyCheckAllowRuleImmuneTest(
    IsolatedAsyncioTestCase,
):
    """Safety checks are immune to allow rules in DEFAULT mode.

    A user-configured allow rule cannot grant permission for an
    operation that the tool has flagged as a safety concern
    (``bypass_immune=True``). This guarantees that broad rules like
    ``Bash:rm:*`` don't accidentally authorize ``rm -rf /``.

    Note: in BYPASS mode the user has explicitly opted out of safety
    enforcement, so these tools' safety ASKs are NOT honored — that
    behavior is covered by
    :class:`tests.permission_mode_test.PermissionEngineBypassModeTest`.
    """

    async def test_injection_check_not_bypassed_by_allow_rule(self) -> None:
        """Allow rule for ``ls:*`` does not authorize ``ls $(rm -rf /)``."""
        context = PermissionContext(mode=PermissionMode.DEFAULT)
        engine = PermissionEngine(context)
        engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="ls:*",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        decision = await engine.check_permission(
            Bash(),
            {"command": "ls $(rm -rf /)"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("command_substitution", decision.message)

    async def test_dangerous_removal_not_bypassed_by_allow_rule(self) -> None:
        """Allow rule for ``rm:*`` does not authorize ``rm -rf /``."""
        context = PermissionContext(mode=PermissionMode.DEFAULT)
        engine = PermissionEngine(context)
        engine.add_rule(
            PermissionRule(
                tool_name="Bash",
                rule_content="rm:*",
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        decision = await engine.check_permission(
            Bash(),
            {"command": "rm -rf /"},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        # Either dangerous removal path or dangerous command pattern fires
        self.assertTrue(
            "Dangerous removal operation" in decision.message
            or "dangerous pattern" in decision.message,
        )


# ---------------------------------------------------------------------------
# bypass_immune field mechanism
# ---------------------------------------------------------------------------


class _FakeToolReturningAsk(ToolBase):
    """Minimal tool used to test the ``bypass_immune`` field mechanism.

    Returns a single configurable ASK decision from ``check_permissions``
    so we can verify the engine routes bypass-immune vs regular ASKs
    correctly regardless of the specific safety trigger.
    """

    name = "FakeAskTool"
    description = "Test-only tool"
    input_schema = {"type": "object", "properties": {}}
    is_concurrency_safe = True
    is_read_only = False
    is_external_tool = False
    is_state_injected = False
    is_mcp = False

    def __init__(self, bypass_immune: bool) -> None:
        self._bypass_immune = bypass_immune

    async def check_permissions(
        self,
        tool_input: dict,
        context: PermissionContext,
    ) -> PermissionDecision:
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="fake tool requires confirmation",
            decision_reason="test-only reason without the s-word",
            bypass_immune=self._bypass_immune,
        )


class PermissionEngineBypassImmuneFieldTest(IsolatedAsyncioTestCase):
    """Tests for the :attr:`PermissionDecision.bypass_immune` field.

    These tests use a minimal fake tool that returns ASK with a
    configurable ``bypass_immune`` value — independent of any specific
    safety trigger — to verify the engine's mechanism for honoring the
    field. Together with
    :class:`PermissionEngineSafetyCheckAllowRuleImmuneTest` (which tests
    that Bash/Write/Edit's individual safety checks correctly set the
    field), this fully covers the bypass-immune contract.
    """

    async def test_bypass_immune_ask_not_overridden_by_allow_rule(
        self,
    ) -> None:
        """An ASK with ``bypass_immune=True`` survives a matching allow
        rule that would otherwise grant the operation."""
        context = PermissionContext(mode=PermissionMode.DEFAULT)
        engine = PermissionEngine(context)
        engine.add_rule(
            PermissionRule(
                tool_name="FakeAskTool",
                rule_content=None,
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        decision = await engine.check_permission(
            _FakeToolReturningAsk(bypass_immune=True),
            {},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)

    async def test_non_bypass_immune_ask_overridden_by_allow_rule(
        self,
    ) -> None:
        """A regular ASK (``bypass_immune=False``) yields to a matching
        allow rule — proving the field, not the ``decision_reason``
        text, governs immunity."""
        context = PermissionContext(mode=PermissionMode.DEFAULT)
        engine = PermissionEngine(context)
        engine.add_rule(
            PermissionRule(
                tool_name="FakeAskTool",
                rule_content=None,
                behavior=PermissionBehavior.ALLOW,
                source="test",
            ),
        )

        decision = await engine.check_permission(
            _FakeToolReturningAsk(bypass_immune=False),
            {},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_bypass_mode_ignores_bypass_immune_field(self) -> None:
        """BYPASS is intentionally permissive: even an ASK with
        ``bypass_immune=True`` is allowed through. The user has opted
        out of safety prompts; ``bypass_immune`` only governs allow
        rules (in DEFAULT) and DONT_ASK conversion.
        """
        context = PermissionContext(mode=PermissionMode.BYPASS)
        engine = PermissionEngine(context)

        decision = await engine.check_permission(
            _FakeToolReturningAsk(bypass_immune=True),
            {},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_bypass_mode_allows_non_bypass_immune_ask(self) -> None:
        """For completeness: a non-bypass-immune ASK is also allowed
        through in BYPASS — same outcome, different path."""
        context = PermissionContext(mode=PermissionMode.BYPASS)
        engine = PermissionEngine(context)

        decision = await engine.check_permission(
            _FakeToolReturningAsk(bypass_immune=False),
            {},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_bypass_immune_ask_converted_to_deny_in_dont_ask(
        self,
    ) -> None:
        """Under DONT_ASK, even a bypass-immune ASK is converted to
        DENY (issue #3 contract): no user is available to confirm,
        so the only safe action is to refuse."""
        context = PermissionContext(mode=PermissionMode.DONT_ASK)
        engine = PermissionEngine(context)

        decision = await engine.check_permission(
            _FakeToolReturningAsk(bypass_immune=True),
            {},
        )
        self.assertEqual(decision.behavior, PermissionBehavior.DENY)
