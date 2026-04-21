# -*- coding: utf-8 -*-
"""Bash command parser using tree-sitter for precise syntax analysis.

This module provides utilities to parse Bash commands and extract meaningful
information for permission rule generation, including:
- Splitting compound commands (&&, ||, ;, |)
- Extracting command prefixes (e.g., "npm run" from "npm run build")
- Extracting file paths from commands for dangerous path detection
- Extracting output redirections
- Checking if commands are read-only
"""

from typing import List, Optional, Set, Tuple

import re
import shlex

import tree_sitter_bash as tsbash
from tree_sitter import Language, Parser, Node

from .._constants import DANGEROUS_NODE_TYPES, DANGEROUS_COMMANDS


# Commands that are considered safe and don't require permission rules
SAFE_COMMANDS: Set[str] = {
    "echo",
    "cat",
    "ls",
    "pwd",
    "cd",
    "true",
    "false",
    "printf",
    "grep",
    "tee",
}

# Safe environment variables that can be skipped when extracting command prefix
SAFE_ENV_VARS = {
    "NODE_ENV",
    "PYTHONUNBUFFERED",
    "RUST_LOG",
    "LANG",
    "TERM",
    "NO_COLOR",
    "FORCE_COLOR",
    "DEBUG",
    "VERBOSE",
    "CI",
    "PATH",
    "HOME",
    "USER",
    "SHELL",
    "EDITOR",
    "PAGER",
    "TZ",
    "LC_ALL",
    "LC_CTYPE",
    "COLUMNS",
    "LINES",
    "CLICOLOR",
    "CLICOLOR_FORCE",
}

# Read-only git commands
GIT_READ_ONLY_COMMANDS = {
    "git status",
    "git log",
    "git diff",
    "git show",
    "git branch",
    "git tag",
    "git remote",
    "git ls-files",
    "git ls-tree",
    "git cat-file",
    "git rev-parse",
    "git rev-list",
    "git describe",
    "git shortlog",
    "git blame",
    "git grep",
    "git reflog",
    "git config --get",
    "git config --list",
}

# Read-only commands for various tools
READ_ONLY_COMMANDS = {
    # Basic file operations
    "ls",
    "cat",
    "head",
    "tail",
    "less",
    "more",
    "file",
    "stat",
    "wc",
    "grep",
    "rg",
    "ag",
    "ack",
    "find",
    "tree",
    "pwd",
    "which",
    "whereis",
    "type",
    # Git commands
    *GIT_READ_ONLY_COMMANDS,
    # Docker read-only
    "docker ps",
    "docker images",
    "docker inspect",
    "docker logs",
    "docker version",
    "docker info",
    # GitHub CLI read-only
    "gh repo view",
    "gh issue list",
    "gh pr list",
    "gh status",
    # Python/Node tools
    "python --version",
    "python -V",
    "node --version",
    "node -v",
    "npm list",
    "npm ls",
    "pip list",
    "pip show",
}


class BashCommandParser:
    """Parse Bash commands using tree-sitter for accurate syntax analysis."""

    def __init__(self) -> None:
        """Initialize the parser with tree-sitter-bash language."""
        self.parser = Parser(Language(tsbash.language()))

    def is_read_only_command(self, command: str) -> bool:
        """Check if a command is read-only (safe to auto-allow).

        For compound commands (&&, ||, ;, |), ALL subcommands must be
        read-only for the entire command to be considered read-only.

        Commands with output redirections (>, >>) are NOT considered read-only.

        Args:
            command (`str`):
                The bash command string

        Returns:
            `bool`:
                True if the command (and all subcommands) are read-only,
                False otherwise
        """
        # Normalize command (strip leading/trailing whitespace)
        cmd = command.strip()

        # Check for output redirections - these are NOT read-only
        if ">" in cmd:
            return False

        # Check if it's a compound command
        if any(op in cmd for op in ["&&", "||", ";", "|"]):
            # Split into subcommands and check each one
            try:
                tree = self.parser.parse(bytes(cmd, "utf8"))
                root = tree.root_node
                subcommands = self.split_compound_command(root, cmd)

                # All subcommands must be read-only
                for subcmd in subcommands:
                    if not self._is_single_command_read_only(subcmd.strip()):
                        return False
                return True
            except Exception:
                # If parsing fails, be conservative
                return False

        # Single command - check directly
        return self._is_single_command_read_only(cmd)

    def _is_single_command_read_only(self, cmd: str) -> bool:
        """Check if a single (non-compound) command is read-only.

        Args:
            cmd (`str`):
                A single command string (no &&, ||, ;, |)

        Returns:
            `bool`:
                True if the command is read-only, False otherwise
        """
        # Check exact match in read-only commands
        if cmd in READ_ONLY_COMMANDS:
            return True

        # Check if it starts with a read-only prefix
        for readonly_cmd in READ_ONLY_COMMANDS:
            if cmd == readonly_cmd or cmd.startswith(readonly_cmd + " "):
                return True

        # Check base command for simple read-only operations
        tokens = cmd.split()
        if tokens:
            base_cmd = tokens[0]
            # Skip environment variables
            i = 0
            while i < len(tokens) and "=" in tokens[i]:
                i += 1
            if i < len(tokens):
                base_cmd = tokens[i]

            # Check if base command is in safe commands
            if base_cmd in SAFE_COMMANDS:
                return True

        return False

    def extract_file_paths(
        self,
        command: str,
    ) -> List[Tuple[str, str]]:
        """Extract file paths from a bash command using tree-sitter.

        Returns paths that are arguments to file-manipulating commands
        (rm, mv, cp, chmod, chown, etc.) and output redirection targets.

        Args:
            command (`str`):
                The bash command string

        Returns:
            `List[Tuple[str, str]]`:
                List of tuples (command_name, file_path)
        """
        paths = []

        try:
            # Parse command to AST
            tree = self.parser.parse(bytes(command, "utf8"))
            root = tree.root_node

            # Extract paths from commands
            self._extract_paths_from_node(root, command, paths)

        except Exception:
            # Fallback to simple token-based extraction
            paths = self._extract_paths_fallback(command)

        return paths

    def _extract_paths_from_node(
        self,
        node: Node,
        command: str,
        paths: List[Tuple[str, str]],
    ) -> None:
        """Recursively extract file paths from AST nodes.

        Args:
            node (`Node`):
                The AST node to process
            command (`str`):
                The original command string
            paths (`List[Tuple[str, str]]`):
                List to append (command_name, path) tuples to
        """
        # Check for redirections
        if node.type == "file_redirect":
            # Extract the target file
            for child in node.children:
                if child.type == "word":
                    path = command[child.start_byte : child.end_byte]
                    paths.append(("redirect", path.strip("'\"")))
        # Check for commands
        if node.type == "command":
            # Extract command name and arguments
            cmd_name = None
            args = []

            for child in node.children:
                if child.type == "command_name":
                    cmd_name = command[child.start_byte : child.end_byte]
                elif child.type == "word" and cmd_name:
                    arg = command[child.start_byte : child.end_byte]
                    args.append(arg.strip("'\""))

            # Check if this is a file-manipulating command
            if cmd_name in [
                "rm",
                "mv",
                "cp",
                "chmod",
                "chown",
                "chgrp",
                "touch",
                "ln",
                "sed",
            ]:
                # Extract file arguments (skip flags)
                for arg in args:
                    if not arg.startswith("-"):
                        paths.append((cmd_name, arg))

        # Recursively process children
        for child in node.children:
            self._extract_paths_from_node(child, command, paths)

    def _extract_paths_fallback(
        self,
        command: str,
    ) -> List[Tuple[str, str]]:
        """Fallback path extraction using simple token parsing.

        Args:
            command (`str`):
                The bash command string

        Returns:
            `List[Tuple[str, str]]`:
                List of tuples (command_name, file_path)
        """
        paths = []
        tokens = command.split()
        i = 0

        while i < len(tokens):
            token = tokens[i]

            # Check for output redirections
            if token in [">", ">>", "2>", "&>"]:
                if i + 1 < len(tokens):
                    path = tokens[i + 1].strip("'\"")
                    paths.append(("redirect", path))
                i += 2
                continue

            # Check for file-manipulating commands
            if token in ["rm", "mv", "cp", "chmod", "chown", "sed", "touch"]:
                cmd_name = token
                # Look for file arguments after this command
                j = i + 1
                while j < len(tokens):
                    arg = tokens[j].strip("'\"")
                    # Skip flags
                    if arg.startswith("-"):
                        j += 1
                        continue
                    # This is a file argument
                    paths.append((cmd_name, arg))
                    j += 1
                break

            i += 1

        return paths

    def extract_redirections(self, command: str) -> List[str]:
        """Extract output redirection targets from a bash command.

        Args:
            command (`str`):
                The bash command string

        Returns:
            `List[str]`:
                List of file paths that are redirection targets
        """
        redirections = []

        try:
            # Parse command to AST
            tree = self.parser.parse(bytes(command, "utf8"))
            root = tree.root_node

            # Extract redirections
            self._extract_redirections_from_node(root, command, redirections)

        except Exception:
            # Fallback to simple extraction
            tokens = command.split()
            for i, token in enumerate(tokens):
                if token in [">", ">>", "2>", "&>"] and i + 1 < len(tokens):
                    path = tokens[i + 1].strip("'\"")
                    redirections.append(path)

        return redirections

    def _extract_redirections_from_node(
        self,
        node: Node,
        command: str,
        redirections: List[str],
    ) -> None:
        """Recursively extract redirections from AST nodes.

        Args:
            node (`Node`):
                The AST node to process
            command (`str`):
                The original command string
            redirections (`List[str]`):
                List to append redirection targets to
        """
        if node.type == "file_redirect":
            # Extract the target file
            for child in node.children:
                if child.type == "word":
                    path = command[child.start_byte : child.end_byte]
                    redirections.append(path.strip("'\""))

        # Recursively process children
        for child in node.children:
            self._extract_redirections_from_node(child, command, redirections)

    def extract_command_prefixes(
        self,
        command: str,
        max_prefixes: int = 5,
    ) -> List[str]:
        """Extract command prefixes from a bash command.

        Automatically handles compound commands (&&, ||, ;, |) and extracts
        prefixes from each subcommand. Returns deduplicated list of prefixes.

        Args:
            command (`str`):
                The bash command string (may be compound)
            max_prefixes (`int`):
                Maximum number of prefixes to return (default: 5)

        Returns:
            `List[str]`:
                List of command prefixes (deduplicated), e.g., ["npm run",
                "git commit"]

        Examples:
            >>> parser.extract_command_prefixes("git add . && git commit")
            ['git add', 'git commit']
            >>> parser.extract_command_prefixes("npm run build")
            ['npm run']
            >>> parser.extract_command_prefixes("ls -la")
            []
        """
        if not command or not command.strip():
            return []

        # Parse command to AST
        tree = self.parser.parse(bytes(command, "utf8"))
        root = tree.root_node

        # Split compound commands
        subcommands = self.split_compound_command(root, command)

        # Extract prefixes from each subcommand
        prefixes = []
        seen = set()

        for subcmd in subcommands[:max_prefixes]:
            prefix = self._extract_command_prefix(subcmd)
            if prefix and prefix not in seen:
                prefixes.append(prefix)
                seen.add(prefix)

            if len(prefixes) >= max_prefixes:
                break

        return prefixes

    def split_compound_command(self, root: Node, command: str) -> List[str]:
        """Split compound commands using tree-sitter for precise parsing.

        Recognizes: &&, ||, ;, |

        Args:
            root (`Node`):
                The root AST node
            command (`str`):
                The original command string

        Returns:
            `List[str]`:
                List of individual subcommands
        """
        subcommands = []

        def extract_commands(node: Node) -> None:
            """Recursively extract commands from AST."""
            if node.type == "command":
                # Extract command text
                cmd_text = command[node.start_byte : node.end_byte]
                subcommands.append(cmd_text)
            elif node.type in ["list", "pipeline", "command_list"]:
                # Recursively process compound structures
                for child in node.children:
                    if child.type not in ["&&", "||", ";", "|", "|&"]:
                        extract_commands(child)
            else:
                # Continue traversing
                for child in node.children:
                    extract_commands(child)

        extract_commands(root)
        return subcommands if subcommands else [command]

    def _extract_command_prefix(
        self,
        subcmd: str,
    ) -> Optional[str]:
        """Extract command prefix (first two words) from a subcommand.

        Logic:
        1. Skip safe environment variable assignments
        2. Extract command name and first subcommand
        3. Verify the second word looks like a subcommand (not a flag)

        Args:
            subcmd (`str`):
                The subcommand string to extract prefix from

        Returns:
            `Optional[str]`:
                Command prefix (e.g., "npm run") or None if cannot extract
        """
        # Parse the subcommand
        tree = self.parser.parse(bytes(subcmd, "utf8"))
        root = tree.root_node

        # Find the first simple_command node
        simple_cmd = self._find_first_simple_command(root)
        if not simple_cmd:
            return None

        # Extract command parts
        parts = []
        env_vars = []

        for child in simple_cmd.children:
            if child.type == "variable_assignment":
                # Environment variable assignment
                var_name = subcmd[child.start_byte : child.end_byte].split(
                    "=",
                )[0]
                env_vars.append(var_name)
            elif child.type == "command_name":
                # Command name
                parts.append(subcmd[child.start_byte : child.end_byte])
            elif child.type == "word" and len(parts) >= 1:
                # Argument (might be a flag or subcommand)
                word = subcmd[child.start_byte : child.end_byte]
                parts.append(word)
                # Stop after we have command + first argument
                if len(parts) >= 2:
                    break

        # Check if environment variables are safe
        if env_vars and not all(v in SAFE_ENV_VARS for v in env_vars):
            return None

        # Check if the command is a safe command that doesn't need permission
        if parts and parts[0].lower() in SAFE_COMMANDS:
            return None

        # Return first two words
        if len(parts) >= 2:
            return " ".join(parts[:2])

        return None

    def _find_first_simple_command(self, node: Node) -> Optional[Node]:
        """Recursively find the first command node in AST.

        Args:
            node (`Node`):
                The AST node to search from

        Returns:
            `Optional[Node]`:
                The first command node found, or None
        """
        if node.type == "command":
            return node

        for child in node.children:
            result = self._find_first_simple_command(child)
            if result:
                return result

        return None

    def check_dangerous_command(self, command: str) -> Optional[str]:
        """Check if command contains dangerous patterns.

        Uses word-boundary aware matching to avoid false positives like
        'git add' matching 'dd' pattern.

        Args:
            command (`str`):
                The bash command to check

        Returns:
            `Optional[str]`:
                The matched dangerous pattern if found, None otherwise
        """

        # Normalize command for matching
        normalized = " ".join(command.split())

        # Check each dangerous pattern
        for pattern in DANGEROUS_COMMANDS:
            # For single-word patterns like "dd", use word boundary matching
            # to avoid false positives (e.g., "git add" shouldn't match "dd")
            if " " not in pattern and len(pattern) <= 4:
                # Single word pattern - use word boundaries
                regex = r"\b" + re.escape(pattern) + r"\b"
                if re.search(regex, normalized):
                    return pattern
            else:
                # Multi-word pattern or longer pattern - use substring match
                if pattern in normalized:
                    return pattern

        return None

    # pylint: disable=too-many-return-statements, too-many-branches
    def check_sed_constraints(
        self,
        command: str,
        dangerous_files: List[str],
    ) -> str | None:
        """Check if sed command violates safety constraints.

        Implements allowlist/denylist system matching Claude Code's behavior:
        - Allowlist: Line printing (sed -n 'Np') and substitution (sed 's///')
        - Denylist: Dangerous operations (w/W/e/E), file writes, command
         execution

        Args:
            command: The bash command to check
            dangerous_files: List of dangerous file patterns

        Returns:
            Error message if dangerous sed operation found, None otherwise
        """

        if "sed" not in command:
            return None

        # Parse command using shlex
        try:
            tokens = shlex.split(command)
        except ValueError:
            return "sed command has invalid shell syntax"

        # Find sed command position
        sed_idx = None
        for i, token in enumerate(tokens):
            if token == "sed" or token.endswith("/sed"):
                sed_idx = i
                break

        if sed_idx is None:
            return None

        # Extract flags and expressions
        args = tokens[sed_idx + 1 :]
        flags = []
        expressions = []
        file_args = []
        i = 0
        found_first_expr = False

        while i < len(args):
            arg = args[i]

            # Handle flags
            if arg.startswith("-") and not arg.startswith("--"):
                # Combined flags like -nE
                flag_chars = arg[1:]
                for char in flag_chars:
                    flags.append(char)
                # -i flag may have optional backup extension argument
                # But only skip if next arg doesn't look like an expression
                if "i" in flag_chars and i + 1 < len(args):
                    next_arg = args[i + 1]
                    # Skip backup extension only if it's not an expression
                    # or file
                    if (
                        not next_arg.startswith("-")
                        and not next_arg.startswith("s")
                        and "." not in next_arg
                    ):
                        i += 1  # Skip backup extension
            elif arg == "--in-place":
                flags.append("i")
                if i + 1 < len(args):
                    next_arg = args[i + 1]
                    if (
                        not next_arg.startswith("-")
                        and not next_arg.startswith("s")
                        and "." not in next_arg
                    ):
                        i += 1
            elif arg in ["-e", "--expression"]:
                if i + 1 < len(args):
                    expressions.append(args[i + 1])
                    i += 1
            elif not arg.startswith("-"):
                # First non-flag, non-option arg is expression (if no -e used)
                if not found_first_expr:
                    expressions.append(arg)
                    found_first_expr = True
                else:
                    file_args.append(arg)

            i += 1

        # If no expressions found, command is invalid
        if not expressions:
            return "sed command missing expression"

        # Validate flags - only allow specific flags
        allowed_flags = {"n", "E", "e", "i"}
        for flag in flags:
            if flag not in allowed_flags:
                return f"sed flag -{flag} not allowed"

        # Check allowlist patterns
        has_n_flag = "n" in flags
        has_i_flag = "i" in flags

        for expr in expressions:
            # Denylist checks first - dangerous operations
            # Check for write operations (w, W) - must be at end or followed
            # by space/filename
            if (
                re.search(r"/[wW]\s+\S+", expr)
                or expr.endswith("/w")
                or expr.endswith("/W")
            ):
                return "sed write operation (w/W) not allowed"

            # Check for execute operations (e, E) - must be at end or
            # followed by space
            if (
                re.search(r"/[eE](?:\s|$)", expr)
                or expr.endswith("/e")
                or expr.endswith("/E")
            ):
                return "sed execute operation (e/E) not allowed"

            # Check for dangerous patterns
            if "{" in expr or "}" in expr:
                return "sed curly braces not allowed"
            if expr.startswith("!"):
                return "sed negation (!) not allowed"
            if "#" in expr and not expr.startswith("s#"):
                return "sed comments not allowed"

            # Pattern 1: Line printing with -n flag (sed -n 'Np' or 'N,Mp')
            if has_n_flag:
                # Match: number followed by 'p', or range 'N,Mp'
                if re.match(
                    r"^\d+p$",
                    expr,
                ) or re.match(
                    r"^\d+,\d+p$",
                    expr,
                ):
                    continue

            # Pattern 2: Substitution command
            # (sed 's/pattern/replacement/flags')
            if (
                expr.startswith("s/")
                or expr.startswith("s|")
                or expr.startswith("s#")
            ):
                delimiter = expr[1]
                parts = expr[2:].split(delimiter)
                if len(parts) >= 2:
                    # Valid substitution
                    # Check substitution flags (g, p, number, etc.)
                    if len(parts) > 2:
                        sub_flags = parts[2]
                        # Allow common substitution flags
                        if all(c in "gp0123456789" for c in sub_flags):
                            continue
                    else:
                        continue

            # If we reach here, expression doesn't match allowlist
            return f"sed expression '{expr}' not in allowlist"

        # Check -i flag with dangerous files
        if has_i_flag and file_args:
            for file_path in file_args:
                for dangerous_file in dangerous_files:
                    if dangerous_file in file_path or file_path.endswith(
                        dangerous_file,
                    ):
                        return f"sed -i modifying dangerous file: {file_path}"

        return None

    def check_injection_risk(self, command: str) -> Optional[str]:
        """Check if command contains structures that cannot be statically
        analyzed.

        This detects command substitution, process substitution, complex
        expansions, control flow, and other dynamic shell features that
        make it impossible to determine the command's behavior without
        execution.

        Args:
            command (`str`):
                The bash command to check

        Returns:
            `Optional[str]`:
                Reason string if command is too complex, None if it can be
                statically analyzed

        Examples:
            >>> parser.check_injection_risk("ls -la")
            None
            >>> parser.check_injection_risk("rm $(find . -name '*.tmp')")
            "Command contains command_substitution which cannot be statically
            analyzed"
            >>> parser.check_injection_risk("for f in *.txt; do cat $f; done")
            "Command contains for_statement which cannot be statically
            analyzed"
        """

        try:
            tree = self.parser.parse(bytes(command, "utf8"))
            return self._walk_for_dangerous_nodes(tree.root_node)
        except Exception:
            # If parsing fails, be conservative and require review
            return "Command parsing failed, cannot verify safety"

    def _walk_for_dangerous_nodes(self, node: Node) -> Optional[str]:
        """Recursively walk AST to find dangerous node types.

        Args:
            node (`Node`):
                The AST node to check

        Returns:
            `Optional[str]`:
                Reason string if dangerous node found, None otherwise
        """

        # Check if this node is a dangerous type
        if node.type in DANGEROUS_NODE_TYPES:
            return (
                f"Command contains {node.type} which cannot be "
                f"statically analyzed"
            )

        # Recursively check children
        for child in node.children:
            result = self._walk_for_dangerous_nodes(child)
            if result:
                return result

        return None
