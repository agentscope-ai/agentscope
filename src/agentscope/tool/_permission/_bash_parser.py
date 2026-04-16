# -*- coding: utf-8 -*-
"""Bash command parser using tree-sitter for precise syntax analysis.

This module provides utilities to parse Bash commands and extract meaningful
information for permission rule generation, including:
- Splitting compound commands (&&, ||, ;, |)
- Extracting command prefixes (e.g., "npm run" from "npm run build")
- Identifying redirections and environment variables
"""

from typing import List, Optional, Tuple

try:
    import tree_sitter_bash as tsbash
    from tree_sitter import Language, Parser, Node

    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    # Fallback types for when tree-sitter is not available
    Node = None  # type: ignore


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


class BashCommandParser:
    """Parse Bash commands using tree-sitter for accurate syntax analysis."""

    def __init__(self) -> None:
        """Initialize the parser with tree-sitter-bash language."""
        if not TREE_SITTER_AVAILABLE:
            raise ImportError(
                "tree-sitter-bash is not installed. "
                "Install it with: pip install tree-sitter tree-sitter-bash",
            )

        self.parser = Parser()
        self.parser.set_language(Language(tsbash.language()))

    def parse(self, command: str) -> Node:
        """Parse command and return AST root node.

        Args:
            command: The bash command string to parse

        Returns:
            The root node of the parsed AST
        """
        tree = self.parser.parse(bytes(command, "utf8"))
        return tree.root_node

    def split_compound_command(self, command: str) -> List[str]:
        """Split compound commands using tree-sitter for precise parsing.

        Recognizes:
        - && (and)
        - || (or)
        - ; (sequence)
        - | (pipe)

        Args:
            command: The bash command string (may be compound)

        Returns:
            List of individual subcommands

        Examples:
            >>> parser.split_compound_command("git add . && git commit")
            ['git add .', 'git commit']
            >>> parser.split_compound_command("npm run build")
            ['npm run build']
        """
        root = self.parse(command)
        subcommands = []

        def extract_commands(node: Node) -> None:
            """Recursively extract commands from AST."""
            if node.type in ["command", "simple_command"]:
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

    def extract_command_prefix(self, command: str) -> Optional[str]:
        """Extract command prefix (first two words) for rule generation.

        Logic:
        1. Skip safe environment variable assignments
        2. Extract command name and first subcommand
        3. Verify the second word looks like a subcommand (not a flag)

        Args:
            command: The bash command string

        Returns:
            Command prefix (e.g., "npm run") or None if cannot extract

        Examples:
            >>> parser.extract_command_prefix('git commit -m "fix"')
            'git commit'
            >>> parser.extract_command_prefix('npm run build')
            'npm run'
            >>> parser.extract_command_prefix('NODE_ENV=prod npm run build')
            'npm run'
            >>> parser.extract_command_prefix('ls -la')
            None  # Second word is a flag, not a subcommand
        """
        root = self.parse(command)

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
                var_name = command[child.start_byte : child.end_byte].split(
                    "=",
                )[0]
                env_vars.append(var_name)
            elif child.type == "command_name":
                # Command name
                parts.append(command[child.start_byte : child.end_byte])
            elif child.type == "word" and len(parts) == 1:
                # First argument (might be a subcommand)
                word = command[child.start_byte : child.end_byte]
                # Check if it looks like a subcommand (not a flag)
                if not word.startswith("-"):
                    parts.append(word)
                break

        # Check if environment variables are safe
        if env_vars and not all(v in SAFE_ENV_VARS for v in env_vars):
            return None

        # Return first two words
        if len(parts) >= 2:
            return " ".join(parts[:2])

        return None

    def _find_first_simple_command(self, node: Node) -> Optional[Node]:
        """Recursively find the first simple_command node in AST.

        Args:
            node: The AST node to search from

        Returns:
            The first simple_command node found, or None
        """
        if node.type == "simple_command":
            return node

        for child in node.children:
            result = self._find_first_simple_command(child)
            if result:
                return result

        return None

    def extract_redirections(self, command: str) -> List[Tuple[str, str]]:
        """Extract output redirections from command.

        Args:
            command: The bash command string

        Returns:
            List of (operator, target) tuples

        Examples:
            >>> parser.extract_redirections('echo hello > /tmp/file.txt')
            [('>', '/tmp/file.txt')]
            >>> parser.extract_redirections('cat file.txt 2>&1')
            [('2>&1', '')]
        """
        root = self.parse(command)
        redirections = []

        def find_redirects(node: Node) -> None:
            """Recursively find redirect nodes."""
            if node.type in ["file_redirect", "heredoc_redirect"]:
                operator = None
                target = None

                for child in node.children:
                    if child.type in [">", ">>", "<", "<<", "&>", "&>>"]:
                        operator = command[child.start_byte : child.end_byte]
                    elif child.type in ["word", "string", "heredoc_start"]:
                        target = command[child.start_byte : child.end_byte]

                if operator and target:
                    redirections.append((operator, target))

            for child in node.children:
                find_redirects(child)

        find_redirects(root)
        return redirections


# Fallback implementation when tree-sitter is not available
class SimpleBashCommandParser:
    """Simplified bash parser using regex (fallback when tree-sitter
    unavailable)."""

    def split_compound_command(self, command: str) -> List[str]:
        """Simple split by && and || operators."""
        import re

        parts = re.split(r"\s*(?:&&|\|\|)\s*", command)
        return [p.strip() for p in parts if p.strip()]

    def extract_command_prefix(self, command: str) -> Optional[str]:
        """Simple prefix extraction using string split."""
        tokens = command.strip().split()
        if not tokens:
            return None

        # Skip environment variables
        i = 0
        while i < len(tokens) and "=" in tokens[i]:
            var_name = tokens[i].split("=")[0]
            if var_name not in SAFE_ENV_VARS:
                return None
            i += 1

        remaining = tokens[i:]
        if len(remaining) < 2:
            return None

        # Check if second word looks like a subcommand
        subcmd = remaining[1]
        if not subcmd.startswith("-") and subcmd.isalnum():
            return " ".join(remaining[:2])

        return None

    def extract_redirections(self, command: str) -> List[Tuple[str, str]]:
        """Simple redirection extraction using regex."""
        import re

        redirections = []
        # Match > or >> followed by a filename
        pattern = r"(>>?)\s+([^\s;|&]+)"
        for match in re.finditer(pattern, command):
            redirections.append((match.group(1), match.group(2)))
        return redirections


def get_bash_parser() -> BashCommandParser:
    """Get a bash parser instance (tree-sitter or fallback).

    Returns:
        BashCommandParser if tree-sitter is available, otherwise
        SimpleBashCommandParser
    """
    if TREE_SITTER_AVAILABLE:
        return BashCommandParser()
    else:
        return SimpleBashCommandParser()  # type: ignore
