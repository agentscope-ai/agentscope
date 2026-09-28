# -*- coding: utf-8 -*-
"""Tests for MCP configuration validation."""

import importlib.util
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

# Load _config.py directly to avoid importing the full mcp package
# which has version-incompatible dependencies.
_CONFIG_PATH = Path(__file__).resolve().parents[1] / "src" / "agentscope" / "mcp" / "_config.py"
spec = importlib.util.spec_from_file_location("_config_direct", _CONFIG_PATH)
assert spec is not None
_config_module = importlib.util.module_from_spec(spec)
sys.modules["_config_direct"] = _config_module
spec.loader.exec_module(_config_module)  # type: ignore[union-attr]

StdioMCPConfig = _config_module.StdioMCPConfig


class TestStdioMCPConfigCommandValidation:
    """Tests for StdioMCPConfig.command validation."""

    def test_allowed_command_without_args(self) -> None:
        """A simple allowed command should validate."""
        config = StdioMCPConfig(command="npx")
        assert config.command == "npx"

    def test_allowed_command_with_args(self) -> None:
        """An allowed command with trailing args should validate."""
        config = StdioMCPConfig(command="python -m http.server")
        assert config.command == "python -m http.server"

    def test_disallowed_command_rejected(self) -> None:
        """A command not in the allowlist should be rejected."""
        with pytest.raises(ValidationError, match="not in the allowed set"):
            StdioMCPConfig(command="curl")

    def test_shell_metacharacter_semicolon_rejected(self) -> None:
        """Command chaining with ; should be rejected."""
        with pytest.raises(ValidationError, match="shell metacharacters"):
            StdioMCPConfig(command="python; rm -rf /")

    def test_shell_metacharacter_pipe_rejected(self) -> None:
        """Command piping with | should be rejected."""
        with pytest.raises(ValidationError, match="shell metacharacters"):
            StdioMCPConfig(command="python | cat")

    def test_shell_metacharacter_ampersand_rejected(self) -> None:
        """Background execution with & should be rejected."""
        with pytest.raises(ValidationError, match="shell metacharacters"):
            StdioMCPConfig(command="python &")

    def test_shell_metacharacter_dollar_rejected(self) -> None:
        """Variable expansion with $ should be rejected."""
        with pytest.raises(ValidationError, match="shell metacharacters"):
            StdioMCPConfig(command="python $HOME")

    def test_shell_metacharacter_backtick_rejected(self) -> None:
        """Command substitution with ` should be rejected."""
        with pytest.raises(ValidationError, match="shell metacharacters"):
            StdioMCPConfig(command="python `whoami`")

    def test_empty_command_rejected(self) -> None:
        """An empty command should be rejected."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            StdioMCPConfig(command="")

    def test_whitespace_only_command_rejected(self) -> None:
        """A whitespace-only command should be rejected."""
        with pytest.raises(ValidationError, match="cannot be empty"):
            StdioMCPConfig(command="   ")

    def test_command_with_absolute_path_allowed(self) -> None:
        """An allowed command given as an absolute path should validate."""
        config = StdioMCPConfig(command="/usr/bin/python")
        assert config.command == "/usr/bin/python"

    def test_command_with_absolute_path_disallowed(self) -> None:
        """A disallowed command given as an absolute path should be rejected."""
        with pytest.raises(ValidationError, match="not in the allowed set"):
            StdioMCPConfig(command="/usr/bin/curl")

    def test_allowed_commands_override(self) -> None:
        """Custom allowed_commands should permit otherwise-blocked commands."""
        config = StdioMCPConfig(
            command="custom-mcp-server",
            allowed_commands={"custom-mcp-server"},
        )
        assert config.command == "custom-mcp-server"
