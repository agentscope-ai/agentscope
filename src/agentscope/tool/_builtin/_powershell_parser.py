# -*- coding: utf-8 -*-
"""PowerShell command parser using tree-sitter for permission checks.

Mirrors :class:`BashCommandParser` method surface for PowerShell:
- Read-only command classification
- Dangerous command detection
- Injection / unanalyzable structure detection
- Command prefix extraction for allow-rule suggestions
"""

from __future__ import annotations

import re
from typing import Iterator, List, Optional, Set

import tree_sitter_pwsh as tspwsh
from tree_sitter import Language, Node, Parser

from .._constants import (
    POWERSHELL_ALIASES,
    POWERSHELL_DANGEROUS_COMMANDS,
    POWERSHELL_INJECTION_NODE_TYPES,
    POWERSHELL_READ_ONLY_COMMANDS,
    POWERSHELL_READ_ONLY_VERB_PREFIXES,
)


class PowerShellCommandParser:
    """Parse PowerShell commands using tree-sitter for safety checks."""

    def __init__(self) -> None:
        """Initialize the parser with the tree-sitter-pwsh language."""
        self.parser = Parser(Language(tspwsh.language()))
        self._alias_lookup = {
            key.casefold(): value for key, value in POWERSHELL_ALIASES.items()
        }
        self._readonly_lookup = {
            name.casefold(): name for name in POWERSHELL_READ_ONLY_COMMANDS
        }
        self._dangerous_lookup = {
            name.casefold(): name for name in POWERSHELL_DANGEROUS_COMMANDS
        }

    def normalize_cmdlet_name(self, name: str) -> str:
        """Resolve aliases to canonical cmdlet names (case-insensitive).

        Args:
            name (`str`):
                Raw command name or alias from the source text.

        Returns:
            `str`:
                Canonical cmdlet name when known, otherwise the original
                name with PowerShell-style casing preserved when possible.
        """
        stripped = name.strip()
        if not stripped:
            return stripped
        alias_target = self._alias_lookup.get(stripped.casefold())
        if alias_target is not None:
            return alias_target
        readonly = self._readonly_lookup.get(stripped.casefold())
        if readonly is not None:
            return readonly
        dangerous = self._dangerous_lookup.get(stripped.casefold())
        if dangerous is not None:
            return dangerous
        return stripped

    def normalize_command_for_match(self, command: str) -> str:
        """Alias-normalize the leading cmdlet of each pipeline segment.

        Used by permission rule matching so that ``Get-ChildItem*`` also
        matches ``ls``.

        Args:
            command (`str`):
                Raw PowerShell command text.

        Returns:
            `str`:
                Command text with leading aliases expanded where practical.
        """
        if not command.strip():
            return command

        try:
            tree = self.parser.parse(bytes(command, "utf8"))
        except Exception:
            return self._normalize_command_fallback(command)

        replacements: list[tuple[int, int, str]] = []
        for node in self._iter_nodes(tree.root_node):
            if node.type != "command":
                continue
            name_node = self._command_name_node(node)
            if name_node is None:
                continue
            raw = command[name_node.start_byte : name_node.end_byte]
            canonical = self.normalize_cmdlet_name(raw)
            if canonical != raw:
                replacements.append(
                    (name_node.start_byte, name_node.end_byte, canonical),
                )

        if not replacements:
            return command

        parts: list[str] = []
        cursor = 0
        for start, end, text in sorted(replacements):
            parts.append(command[cursor:start])
            parts.append(text)
            cursor = end
        parts.append(command[cursor:])
        return "".join(parts)

    def is_read_only_command(self, command: str) -> bool:
        """Check whether a PowerShell command is read-only.

        Pipelines and statement lists require every command segment to be
        read-only. Script blocks, call operators, redirections, and
        injection-risk structures are never treated as read-only.

        Args:
            command (`str`):
                The PowerShell command string.

        Returns:
            `bool`:
                ``True`` when the command is classified as read-only.
        """
        cmd = command.strip()
        if not cmd:
            return False

        if self.check_injection_risk(cmd):
            return False

        try:
            tree = self.parser.parse(bytes(cmd, "utf8"))
            root = tree.root_node
        except Exception:
            return False

        if self._has_error_nodes(root):
            return False

        if self._contains_node_types(
            root,
            {
                "redirection",
                "script_block_expression",
                "script_block",
                "command_invocation_operator",
            },
        ):
            return False

        commands = self._extract_command_nodes(root)
        if not commands:
            return False

        for cmd_node in commands:
            if not self._is_single_command_read_only(cmd, cmd_node):
                return False
        return True

    def check_dangerous_command(self, command: str) -> Optional[str]:
        """Detect dangerous PowerShell patterns that need an ASK.

        Args:
            command (`str`):
                The PowerShell command to inspect.

        Returns:
            `Optional[str]`:
                Matched dangerous pattern label, or ``None``.
        """
        cmd = command.strip()
        if not cmd:
            return None

        try:
            tree = self.parser.parse(bytes(cmd, "utf8"))
            root = tree.root_node
        except Exception:
            return "unparseable PowerShell command"

        # Prefer the more specific download-to-iex label when both apply.
        download_pattern = self._check_download_to_iex(cmd, root)
        if download_pattern:
            return download_pattern

        # Always-dangerous cmdlets (exact name match after alias normalize)
        for cmd_node in self._extract_command_nodes(root):
            name = self._command_name_text(cmd, cmd_node)
            if not name:
                continue
            canonical = self.normalize_cmdlet_name(name)
            folded = canonical.casefold()
            if folded in self._dangerous_lookup:
                return self._dangerous_lookup[folded]

            params = self._command_parameters(cmd, cmd_node)
            param_fold = {p.casefold() for p in params}

            if folded == "remove-item" and (
                "-recurse" in param_fold or "-force" in param_fold
            ):
                return "Remove-Item -Recurse/-Force"

            if folded == "stop-process" and "-force" in param_fold:
                return "Stop-Process -Force"

            if folded == "set-itemproperty" and self._mentions_hklm(
                cmd,
                cmd_node,
            ):
                return "Set-ItemProperty HKLM:"

        # Textual fallback for always-dangerous names that may appear
        # oddly tokenized
        normalized = " ".join(cmd.split())
        for pattern in POWERSHELL_DANGEROUS_COMMANDS:
            if re.search(
                r"(?i)\b" + re.escape(pattern) + r"\b",
                normalized,
            ):
                return pattern
            alias_hit = self._alias_for_canonical(pattern)
            for alias in alias_hit:
                if re.search(
                    r"(?i)\b" + re.escape(alias) + r"\b",
                    normalized,
                ):
                    return pattern

        return None

    def check_injection_risk(self, command: str) -> Optional[str]:
        """Detect structures that cannot be statically analyzed.

        Args:
            command (`str`):
                The PowerShell command to inspect.

        Returns:
            `Optional[str]`:
                Reason string when review is required, otherwise ``None``.
        """
        cmd = command.strip()
        if not cmd:
            return None

        # Heuristics that do not require a clean AST
        if re.search(r"(?i)(^|[\s|;])-EncodedCommand\b", cmd):
            return (
                "Command contains -EncodedCommand which cannot be "
                "statically analyzed"
            )
        if self._has_backtick_obfuscation(cmd):
            return (
                "Command contains backtick obfuscation which cannot be "
                "statically analyzed"
            )
        if self._has_string_built_cmdlet(cmd):
            return (
                "Command builds cmdlet names dynamically which cannot be "
                "statically analyzed"
            )

        try:
            tree = self.parser.parse(bytes(cmd, "utf8"))
            root = tree.root_node
        except Exception:
            return "Command parsing failed, cannot verify safety"

        if self._has_error_nodes(root):
            return "Command parsing failed, cannot verify safety"

        reason = self._walk_for_injection_nodes(root)
        if reason:
            return reason

        # Call operator / dot-sourcing on dynamic targets
        for cmd_node in self._extract_command_nodes(root):
            if self._has_child_type(cmd_node, "command_invocation_operator"):
                return (
                    "Command contains call operator (&/.) which cannot be "
                    "statically analyzed"
                )
            name = self._command_name_text(cmd, cmd_node)
            if name and self.normalize_cmdlet_name(name).casefold() == (
                "invoke-expression"
            ):
                return (
                    "Command contains Invoke-Expression which cannot be "
                    "statically analyzed"
                )

        return None

    def extract_command_prefixes(
        self,
        command: str,
        max_prefixes: int = 5,
    ) -> List[str]:
        """Extract cmdlet prefixes for allow-rule suggestions.

        Returns canonical cmdlet names (alias-normalized). Read-only
        cmdlets that auto-ALLOW are omitted, matching Bash's treatment of
        safe commands.

        Args:
            command (`str`):
                PowerShell command text (may include pipelines).
            max_prefixes (`int`):
                Maximum number of prefixes to return.

        Returns:
            `List[str]`:
                Deduplicated cmdlet prefixes such as ``["Remove-Item"]``.
        """
        if not command or not command.strip():
            return []

        try:
            tree = self.parser.parse(bytes(command, "utf8"))
            root = tree.root_node
        except Exception:
            return []

        prefixes: list[str] = []
        seen: Set[str] = set()
        for cmd_node in self._extract_command_nodes(root):
            name = self._command_name_text(command, cmd_node)
            if not name:
                continue
            canonical = self.normalize_cmdlet_name(name)
            if self._is_readonly_cmdlet_name(canonical):
                continue
            key = canonical.casefold()
            if key in seen:
                continue
            seen.add(key)
            prefixes.append(canonical)
            if len(prefixes) >= max_prefixes:
                break
        return prefixes

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _normalize_command_fallback(self, command: str) -> str:
        """Token-based alias normalization when parsing fails."""
        tokens = command.split(None, 1)
        if not tokens:
            return command
        canonical = self.normalize_cmdlet_name(tokens[0])
        if len(tokens) == 1:
            return canonical
        return f"{canonical} {tokens[1]}"

    def _is_single_command_read_only(
        self,
        source: str,
        cmd_node: Node,
    ) -> bool:
        """Classify one AST ``command`` node as read-only or not."""
        if self._has_child_type(cmd_node, "command_invocation_operator"):
            return False
        if self._contains_node_types(
            cmd_node,
            {"script_block_expression", "script_block", "redirection"},
        ):
            return False

        name = self._command_name_text(source, cmd_node)
        if not name:
            return False
        return self._is_readonly_cmdlet_name(self.normalize_cmdlet_name(name))

    def _is_readonly_cmdlet_name(self, name: str) -> bool:
        """Return whether a canonical cmdlet name is read-only."""
        folded = name.casefold()
        if folded in self._readonly_lookup:
            return True
        for prefix in POWERSHELL_READ_ONLY_VERB_PREFIXES:
            if folded.startswith(prefix.casefold()):
                return True
        return False

    def _command_name_node(self, cmd_node: Node) -> Optional[Node]:
        """Return the name node for a command AST node."""
        for child in cmd_node.children:
            if child.type in {"command_name", "command_name_expr"}:
                return child
        return None

    def _command_name_text(self, source: str, cmd_node: Node) -> Optional[str]:
        """Extract the command name text from a command node."""
        name_node = self._command_name_node(cmd_node)
        if name_node is None:
            return None
        return source[name_node.start_byte : name_node.end_byte].strip()

    def _command_parameters(self, source: str, cmd_node: Node) -> list[str]:
        """Collect ``command_parameter`` texts for a command node."""
        params: list[str] = []
        for node in self._iter_nodes(cmd_node):
            if node.type == "command_parameter":
                params.append(source[node.start_byte : node.end_byte])
        return params

    def _mentions_hklm(self, source: str, cmd_node: Node) -> bool:
        """Return whether a command node references an HKLM path."""
        text = source[cmd_node.start_byte : cmd_node.end_byte]
        return bool(re.search(r"(?i)\bHKLM:", text))

    def _check_download_to_iex(
        self,
        source: str,
        root: Node,
    ) -> Optional[str]:
        """Detect download-to-iex patterns such as ``irm ... | iex``."""
        normalized = " ".join(source.split())
        if re.search(
            r"(?i)\b(irm|iwr|Invoke-RestMethod|Invoke-WebRequest)\b.*"
            r"\|\s*(iex|Invoke-Expression)\b",
            normalized,
        ):
            return "download-to-iex"
        if re.search(
            r"(?i)\b(iex|Invoke-Expression)\b\s*\(.*\b"
            r"(irm|iwr|Invoke-RestMethod|Invoke-WebRequest)\b",
            normalized,
        ):
            return "download-to-iex"

        # Also catch adjacent pipeline commands via AST
        names = [
            self.normalize_cmdlet_name(n).casefold()
            for n in (
                self._command_name_text(source, node)
                for node in self._extract_command_nodes(root)
            )
            if n
        ]
        download = {
            "invoke-restmethod",
            "invoke-webrequest",
        }
        if any(n in download for n in names) and any(
            n == "invoke-expression" for n in names
        ):
            return "download-to-iex"
        return None

    def _alias_for_canonical(self, canonical: str) -> list[str]:
        """Return aliases that map to ``canonical``."""
        target = canonical.casefold()
        return [
            alias
            for alias, name in POWERSHELL_ALIASES.items()
            if name.casefold() == target
        ]

    def _has_backtick_obfuscation(self, command: str) -> bool:
        """Detect backtick-obfuscated command text."""
        # e.g. Inv`oke-Expression or Get`-ChildItem
        if re.search(r"[A-Za-z]`+[A-Za-z]", command):
            return True
        # Many backticks are a strong obfuscation signal
        return command.count("`") >= 3

    def _has_string_built_cmdlet(self, command: str) -> bool:
        """Detect string-concatenated / expandable cmdlet names."""
        # & ("Get-" + "ChildItem") or & "Get-$verb"
        if re.search(
            r"""(?i)&\s*[\(\"'].*(?:\+|\$)""",
            command,
        ):
            return True
        return bool(
            re.search(
                r"""(?i)\.\s*[\(\"'].*(?:\+|\$)""",
                command,
            ),
        )

    def _walk_for_injection_nodes(self, node: Node) -> Optional[str]:
        """Walk the AST for injection-related node types."""
        if node.type in POWERSHELL_INJECTION_NODE_TYPES:
            return (
                f"Command contains {node.type} which cannot be "
                f"statically analyzed"
            )
        for child in node.children:
            result = self._walk_for_injection_nodes(child)
            if result:
                return result
        return None

    def _extract_command_nodes(self, root: Node) -> list[Node]:
        """Collect all ``command`` nodes under ``root``."""
        return [
            node for node in self._iter_nodes(root) if node.type == "command"
        ]

    def _contains_node_types(self, root: Node, types: Set[str]) -> bool:
        """Return whether any descendant has a type in ``types``."""
        for node in self._iter_nodes(root):
            if node.type in types:
                return True
        return False

    def _has_child_type(self, node: Node, node_type: str) -> bool:
        """Return whether ``node`` has a direct child of ``node_type``."""
        return any(child.type == node_type for child in node.children)

    def _has_error_nodes(self, root: Node) -> bool:
        """Return whether the tree contains ERROR / missing nodes."""
        for node in self._iter_nodes(root):
            if node.type == "ERROR" or node.is_error or node.is_missing:
                return True
        return False

    def _iter_nodes(self, root: Node) -> Iterator[Node]:
        """Yield ``root`` and all descendants depth-first."""
        stack = [root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed(list(node.children)))
