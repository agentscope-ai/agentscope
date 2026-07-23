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


POWERSHELL_ALIASES: dict[str, str] = {
    "ls": "Get-ChildItem",
    "dir": "Get-ChildItem",
    "gci": "Get-ChildItem",
    "cat": "Get-Content",
    "gc": "Get-Content",
    "type": "Get-Content",
    "sls": "Select-String",
    "select": "Select-Object",
    "where": "Where-Object",
    "?": "Where-Object",
    "sort": "Sort-Object",
    "measure": "Measure-Object",
    "echo": "Write-Output",
    "write": "Write-Output",
    "cd": "Set-Location",
    "chdir": "Set-Location",
    "sl": "Set-Location",
    "pwd": "Get-Location",
    "gl": "Get-Location",
    "rm": "Remove-Item",
    "del": "Remove-Item",
    "ri": "Remove-Item",
    "rmdir": "Remove-Item",
    "rd": "Remove-Item",
    "ni": "New-Item",
    "mi": "Move-Item",
    "move": "Move-Item",
    "cpi": "Copy-Item",
    "copy": "Copy-Item",
    "cp": "Copy-Item",
    "ii": "Invoke-Item",
    "iex": "Invoke-Expression",
    "irm": "Invoke-RestMethod",
    "iwr": "Invoke-WebRequest",
    "curl": "Invoke-WebRequest",
    "wget": "Invoke-WebRequest",
    "kill": "Stop-Process",
    "spps": "Stop-Process",
    "gps": "Get-Process",
    "ps": "Get-Process",
    "ft": "Format-Table",
    "fl": "Format-List",
    "foreach": "ForEach-Object",
    "%": "ForEach-Object",
}
# Built-in PowerShell alias map (lowercase key → canonical cmdlet name).
# Used for read-only classification, dangerous-command checks, and
# case-insensitive permission rule matching.


POWERSHELL_READ_ONLY_COMMANDS: set[str] = {
    "Get-Location",
    "Get-Date",
    "Get-Host",
    "Get-Help",
    "Get-Member",
    "Get-Unique",
    "Get-Variable",
    "Get-Alias",
    "Get-Command",
    "Get-Module",
    "Get-Process",
    "Get-Service",
    "Get-ChildItem",
    "Get-Content",
    "Get-Item",
    "Get-ItemProperty",
    "Get-Acl",
    "Get-FileHash",
    "Test-Path",
    "Resolve-Path",
    "Select-Object",
    "Select-String",
    "Where-Object",
    "Sort-Object",
    "Measure-Object",
    "Format-Table",
    "Format-List",
    "Format-Wide",
    "Format-Custom",
    "ConvertTo-Json",
    "ConvertFrom-Json",
    "ConvertTo-Csv",
    "ConvertFrom-Csv",
    "ConvertTo-Xml",
    "ConvertFrom-StringData",
    "Out-String",
    "Out-Null",
    "Write-Output",
    "Write-Host",
    "Write-Verbose",
    "Write-Debug",
    "Write-Information",
}
# Cmdlets treated as read-only for auto-ALLOW / EXPLORE mode.
# Verb prefixes such as Get- / Select- / Format- / ConvertTo- /
# ConvertFrom- are handled separately in the PowerShell parser.


POWERSHELL_READ_ONLY_VERB_PREFIXES: tuple[str, ...] = (
    "Get-",
    "Select-",
    "Format-",
    "ConvertTo-",
    "ConvertFrom-",
)
# Verb prefixes that are generally read-only when the cmdlet has no
# script block, call operator, or redirection.


POWERSHELL_DANGEROUS_COMMANDS: list[str] = [
    "Clear-Content",
    "Clear-Item",
    "Format-Volume",
    "Invoke-Expression",
    "Start-Process",
    "Add-Type",
    "Set-ExecutionPolicy",
    "Set-MpPreference",
    "Stop-Computer",
    "Restart-Computer",
    "Register-ScheduledTask",
]
# Dangerous PowerShell cmdlets that always require a bypass-immune ASK.
# Parameter-sensitive patterns (Remove-Item -Recurse/-Force,
# Stop-Process -Force, HKLM registry writes, download-to-iex) are
# handled in the parser.


POWERSHELL_INJECTION_NODE_TYPES: set[str] = {
    "sub_expression",
    "if_statement",
    "while_statement",
    "for_statement",
    "foreach_statement",
    "switch_statement",
    "function_definition",
    "filter_definition",
    "trap_statement",
}
# AST node types that prevent static analysis of PowerShell commands.
# Detecting these forces a non-read-only classification and a
# bypass-immune safety ASK.
