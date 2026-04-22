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
]
# Built-in list of dangerous files that should be protected from auto-editing.
#
# These files can be used for code execution, credential storage, or data
# exfiltration:
# - Shell configuration files: .bashrc, .zshrc, .profile, etc.
# - Git configuration: .gitconfig, .gitmodules
# - SSH configuration: .ssh/config, .ssh/authorized_keys
# - Credential files: .netrc, .npmrc, .pypirc


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
