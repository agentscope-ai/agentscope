# -*- coding: utf-8 -*-
"""Constants for tool permission system."""

DEFAULT_DANGEROUS_FILES = [
    ".gitconfig",
    ".gitmodules",
    ".bashrc",
    ".bash_profile",
    ".zshrc",
    ".zprofile",
    ".profile",
    ".ssh/config",
    ".ssh/authorized_keys",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".env",
    ".envrc",
    ".env.local",
    ".env.development",
    ".env.development.local",
    ".env.test",
    ".env.test.local",
    ".env.staging",
    ".env.production",
    ".env.production.local",
]
# Built-in list of dangerous files that should be protected from auto-editing.
#
# These files can be used for code execution, credential storage, or data
# exfiltration:
# - Shell configuration files: .bashrc, .zshrc, .profile, etc.
# - Git configuration: .gitconfig, .gitmodules
# - SSH configuration: .ssh/config, .ssh/authorized_keys
# - Credential files: .netrc, .npmrc, .pypirc
# - Environment / secret files: .env and common variants (.envrc for direnv,
#   .env.local / .env.production / etc. for framework-specific overrides)


DEFAULT_DANGEROUS_DIRECTORIES = [
    ".git",
    ".vscode",
    ".idea",
    ".ssh",
]
# Built-in list of dangerous directories that should be protected from
# auto-editing.
#
# These directories contain sensitive configuration or executable files:
# - .git: Git repository metadata
# - .vscode: VS Code configuration
# - .idea: JetBrains IDE configuration
# - .ssh: SSH keys and configuration


DANGEROUS_COMMANDS = [
    "rm -rf",
    "sudo rm",
    "dd",
    "mkfs",
    "fdisk",
    "format",
    "chmod 777",
    "chmod -R 777",
    "chown -R",
    "kill -9",
    "> /dev/",
]
# Built-in list of dangerous command patterns that require explicit
# user approval.
#
# These commands can cause data loss, system damage, or security issues:
# - rm -rf: Recursive force deletion
# - sudo rm: Deletion with elevated privileges
# - dd: Direct disk operations
# - mkfs: Format filesystem
# - fdisk: Disk partitioning
# - format: Format disk
# - chmod 777: Overly permissive file permissions
# - chown -R: Recursive ownership changes
# - kill -9: Force kill processes
# - > /dev/: Writing to device files


DANGEROUS_NODE_TYPES = {
    "command_substitution",  # $(...)  or `...`
    "process_substitution",  # <(...)
    "expansion",  # ${VAR:-default} complex expansion
    "subshell",  # (...)
    "for_statement",  # for loops
    "while_statement",  # while loops
    "until_statement",  # until loops
    "if_statement",  # if conditionals
    "case_statement",  # case statements
    "function_definition",  # function definitions
    "test_command",  # [[ ... ]] test commands
}
# Node types that indicate commands cannot be statically analyzed.
# These either execute arbitrary code or expand to values we can't
# determine statically.
#
# When these are detected, the command requires user review because:
# - Command substitution $(cmd) executes arbitrary commands
# - Process substitution <(cmd) creates dynamic file descriptors
# - Complex expansions ${VAR:-default} have runtime-dependent values
# - Control flow (if/while/for) has conditional execution paths
# - Subshells (...) create new execution contexts
#
# Note: simple_expansion ($VAR) is handled separately with allowlist
# for known-safe environment variables ($HOME, $PWD, etc.)

MCP_CALL_META_KEY = "__tool_call_meta__"
# Reserved key under `Msg.metadata` for per-request meta forwarded to MCP.
#
# Flow:
# 1. Caller stores a dict at `UserMsg.metadata[MCP_CALL_META_KEY]`.
# 2. `Toolkit.call_tool` walks `state.context` in reverse, picks the most
#    recent user message, and injects that dict as `kwargs["_meta"]`
#    (only when the target tool is an MCP tool).
# 3. `MCPTool.__call__` pops `_meta` from kwargs and passes it as the
#    `meta` kwarg of `session.call_tool(...)`.
# 4. The MCP server reads it from `CallToolRequestParams.meta`, surfaced
#    to tool handlers via `ctx.request_context.meta`.
#
# The dict stays out of LLM sight (the tool schema is unchanged) and
# out of tool args (`_meta` is popped before `session.call_tool`
# receives `arguments=kwargs`).