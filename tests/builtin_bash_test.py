# -*- coding: utf-8 -*-
"""Bash tool test case."""
import sys
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.tool import ToolChunk, PermissionContext, Bash
from agentscope.message import TextBlock


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class BashToolTest(IsolatedAsyncioTestCase):
    """The bash tool test case."""

    async def asyncSetUp(self) -> None:
        """The async setup method."""
        self.bash_tool = Bash()

    async def test_tool_properties(self) -> None:
        """Test bash tool properties."""
        self.assertEqual(self.bash_tool.name, "Bash")
        self.assertIsInstance(self.bash_tool.description, str)
        self.assertIsInstance(self.bash_tool.input_schema, dict)
        self.assertFalse(self.bash_tool.is_mcp)
        self.assertFalse(self.bash_tool.is_read_only)
        self.assertFalse(self.bash_tool.is_concurrency_safe)

    async def test_check_permissions(self) -> None:
        """Test bash tool permission checking."""
        from agentscope.tool import PermissionBehavior

        context = PermissionContext()
        tool_input = {"command": "echo hello"}
        decision = await self.bash_tool.check_permissions(tool_input, context)

        self.assertEqual(decision.behavior, PermissionBehavior.ALLOW)

    async def test_simple_command(self) -> None:
        """Test executing a simple bash command."""
        chunks = []
        async for chunk in self.bash_tool(command="echo 'Hello World'"):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertIsInstance(chunks[0], ToolChunk)
        self.assertEqual(chunks[0].state, "running")
        self.assertTrue(chunks[0].is_last)
        self.assertEqual(len(chunks[0].content), 1)
        self.assertIsInstance(chunks[0].content[0], TextBlock)
        self.assertIn("Hello World", chunks[0].content[0].text)

    async def test_command_with_error(self) -> None:
        """Test executing a command that fails."""
        chunks = []
        async for chunk in self.bash_tool(command="exit 1"):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertTrue(chunks[0].is_last)

    @unittest.skipIf(
        sys.platform == "win32",
        "sleep command not available on Windows",
    )
    async def test_command_timeout(self) -> None:
        """Test command timeout."""
        chunks = []
        async for chunk in self.bash_tool(
            command="sleep 10",
            timeout=100,  # 100ms timeout
        ):
            chunks.append(chunk)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].state, "error")
        self.assertIn("timed out", chunks[0].content[0].text.lower())


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class BashToolInjectionCheckTest(IsolatedAsyncioTestCase):
    """Test injection detection in Bash tool permission checks."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bash_tool = Bash()
        self.context = PermissionContext()

    async def test_command_substitution_blocked(self) -> None:
        """Test that command substitution is blocked."""
        from agentscope.tool import PermissionBehavior

        test_cases = [
            "ls $(pwd)",
            "rm $(find . -name '*.tmp')",
            "cat `which python`",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                self.assertIn("command_substitution", decision.message)

    async def test_control_flow_blocked(self) -> None:
        """Test that control flow structures are blocked."""
        from agentscope.tool import PermissionBehavior

        test_cases = [
            "for f in *.txt; do cat $f; done",
            "while read line; do echo $line; done < file.txt",
            "if [ -f file.txt ]; then cat file.txt; fi",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                self.assertIn(
                    "cannot be statically analyzed",
                    decision.message,
                )

    async def test_subshell_blocked(self) -> None:
        """Test that subshells are blocked."""
        from agentscope.tool import PermissionBehavior

        cmd = "(cd /tmp && ls)"
        decision = await self.bash_tool.check_permissions(
            {"command": cmd},
            self.context,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("subshell", decision.message)

    async def test_injection_check_before_readonly(self) -> None:
        """Test that injection check runs before read-only check."""
        from agentscope.tool import PermissionBehavior

        # ls is read-only, but $(rm -rf /) is dangerous
        cmd = "ls $(rm -rf /)"
        decision = await self.bash_tool.check_permissions(
            {"command": cmd},
            self.context,
        )
        # Should be blocked by injection check, not allowed as read-only
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        self.assertIn("command_substitution", decision.message)

    async def test_safe_commands_pass(self) -> None:
        """Test that safe commands pass injection check."""
        from agentscope.tool import PermissionBehavior

        safe_commands = [
            "ls -la",
            "cat file.txt",
            "git status",
            "echo 'hello world'",
        ]
        for cmd in safe_commands:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                # Should pass injection check (either ALLOW or PASSTHROUGH)
                self.assertNotEqual(decision.behavior, PermissionBehavior.ASK)
                if decision.behavior == PermissionBehavior.ASK:
                    self.assertNotIn(
                        "cannot be statically analyzed",
                        decision.message,
                    )

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.bash_tool = None
        self.context = None


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class BashToolMatchRuleTest(IsolatedAsyncioTestCase):
    """Test cases for Bash tool match_rule and generate_suggestions."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bash_tool = Bash()

    async def test_match_rule_prefix_pattern(self) -> None:
        """Test match_rule with prefix patterns (e.g., git:*)."""
        # Test exact command match
        self.assertTrue(
            self.bash_tool.match_rule(
                "git:*",
                {"command": "git"},
            ),
        )

        # Test command with arguments
        self.assertTrue(
            self.bash_tool.match_rule(
                "git:*",
                {"command": "git status"},
            ),
        )

        # Test non-matching command
        self.assertFalse(
            self.bash_tool.match_rule(
                "git:*",
                {"command": "npm install"},
            ),
        )

    async def test_match_rule_wildcard_pattern(self) -> None:
        """Test match_rule with wildcard patterns."""
        # Test wildcard matching
        self.assertTrue(
            self.bash_tool.match_rule(
                "git * -m *",
                {"command": "git commit -m 'test'"},
            ),
        )

        # Test non-matching wildcard
        self.assertFalse(
            self.bash_tool.match_rule(
                "git * -m *",
                {"command": "git status"},
            ),
        )

    async def test_match_rule_substring_pattern(self) -> None:
        """Test match_rule with substring patterns."""
        # Test substring matching
        self.assertTrue(
            self.bash_tool.match_rule(
                "install",
                {"command": "npm install package"},
            ),
        )

        # Test non-matching substring
        self.assertFalse(
            self.bash_tool.match_rule(
                "install",
                {"command": "npm run build"},
            ),
        )

    async def test_match_rule_escaped_characters(self) -> None:
        """Test match_rule with escaped characters."""
        # Test escaped asterisk
        self.assertTrue(
            self.bash_tool.match_rule(
                r"echo \*",
                {"command": "echo *"},
            ),
        )

        # Test escaped backslash
        self.assertTrue(
            self.bash_tool.match_rule(
                r"echo \\",
                {"command": "echo \\"},
            ),
        )

    async def test_generate_suggestions(self) -> None:
        """Test generate_suggestions for bash commands."""
        from agentscope.tool import PermissionRule

        # Test two-word command
        suggestions = self.bash_tool.generate_suggestions(
            {"command": "git commit -m 'test'"},
        )

        self.assertIsInstance(suggestions, list)
        self.assertGreater(len(suggestions), 0)
        self.assertIsInstance(suggestions[0], PermissionRule)

        # Should suggest "git commit:*"
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn("git commit:*", suggestion_contents)

    async def test_generate_suggestions_single_word(self) -> None:
        """Test generate_suggestions for single-word commands."""
        suggestions = self.bash_tool.generate_suggestions(
            {"command": "npm install"},
        )

        self.assertGreater(len(suggestions), 0)

        # Should suggest "npm install:*"
        suggestion_contents = [s.rule_content for s in suggestions]
        self.assertIn("npm install:*", suggestion_contents)

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.bash_tool = None


@unittest.skipIf(
    sys.platform == "win32",
    "Bash tool is not supported on Windows",
)
class BashToolDangerousRemovalTest(IsolatedAsyncioTestCase):
    """Test dangerous removal path detection in Bash tool."""

    async def asyncSetUp(self) -> None:
        """Set up test fixtures."""
        self.bash_tool = Bash()
        self.context = PermissionContext()

    async def test_rm_root_blocked(self) -> None:
        """Test that rm -rf / is blocked."""
        from agentscope.tool import PermissionBehavior

        cmd = "rm -rf /"
        decision = await self.bash_tool.check_permissions(
            {"command": cmd},
            self.context,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        # Can be blocked by either dangerous command pattern or dangerous
        # removal path check
        self.assertTrue(
            "Dangerous removal operation" in decision.message
            or "dangerous pattern" in decision.message,
        )

    async def test_rm_root_children_blocked(self) -> None:
        """Test that rm -rf /usr, /etc, etc. are blocked."""
        from agentscope.tool import PermissionBehavior

        test_cases = [
            "rm -rf /usr",
            "rm -rf /etc",
            "rm -rf /tmp",
            "rm -rf /var",
            "rm -rf /bin",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                # Can be blocked by either dangerous command pattern or
                # dangerous removal path check
                self.assertTrue(
                    "Dangerous removal operation" in decision.message
                    or "dangerous pattern" in decision.message,
                )

    async def test_rm_home_blocked(self) -> None:
        """Test that rm -rf ~ is blocked."""
        from agentscope.tool import PermissionBehavior

        cmd = "rm -rf ~"
        decision = await self.bash_tool.check_permissions(
            {"command": cmd},
            self.context,
        )
        self.assertEqual(decision.behavior, PermissionBehavior.ASK)
        # Can be blocked by either dangerous command pattern or dangerous
        # removal path check
        self.assertTrue(
            "Dangerous removal operation" in decision.message
            or "dangerous pattern" in decision.message,
        )

    async def test_rm_wildcard_blocked(self) -> None:
        """Test that rm -rf * and rm -rf /* are blocked."""
        from agentscope.tool import PermissionBehavior

        test_cases = [
            "rm -rf *",
            "rm -rf /*",
            "rm -rf /tmp/*",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                # Can be blocked by either dangerous command pattern or
                # dangerous removal path check
                self.assertTrue(
                    "Dangerous removal operation" in decision.message
                    or "dangerous pattern" in decision.message,
                )

    async def test_rmdir_dangerous_paths_blocked(self) -> None:
        """Test that rmdir on dangerous paths is blocked."""
        from agentscope.tool import PermissionBehavior

        test_cases = [
            "rmdir /",
            "rmdir /usr",
            "rmdir ~",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                self.assertIn("Dangerous removal operation", decision.message)

    async def test_safe_rm_commands_pass(self) -> None:
        """Test that safe rm commands pass dangerous removal check."""
        from agentscope.tool import PermissionBehavior

        safe_commands = [
            "rm file.txt",
            "rm -f temp.log",
            "rm -rf /tmp/my_project/build",
            "rm -rf ./node_modules",
        ]
        for cmd in safe_commands:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                # Should not be blocked by dangerous removal check
                # (may still be blocked by other checks)
                if decision.behavior == PermissionBehavior.ASK:
                    self.assertNotIn(
                        "Dangerous removal operation",
                        decision.message,
                    )

    async def test_compound_commands_with_dangerous_removal(self) -> None:
        """Test compound commands containing dangerous removal."""
        from agentscope.tool import PermissionBehavior

        test_cases = [
            "ls && rm -rf /",
            "cd /tmp && rm -rf /usr",
            "echo start; rm -rf ~; echo end",
        ]
        for cmd in test_cases:
            with self.subTest(cmd=cmd):
                decision = await self.bash_tool.check_permissions(
                    {"command": cmd},
                    self.context,
                )
                self.assertEqual(decision.behavior, PermissionBehavior.ASK)
                # Can be blocked by either dangerous command pattern or
                # dangerous removal path check
                self.assertTrue(
                    "Dangerous removal operation" in decision.message
                    or "dangerous pattern" in decision.message,
                )

    async def asyncTearDown(self) -> None:
        """Clean up test fixtures."""
        self.bash_tool = None
        self.context = None
