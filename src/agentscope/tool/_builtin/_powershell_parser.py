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
from typing import Iterator, List, Optional, Set, Tuple

import tree_sitter_pwsh as tspwsh
from tree_sitter import Language, Node, Parser, Tree

from .._constants import (
    POWERSHELL_ALIASES,
    POWERSHELL_DANGEROUS_COMMANDS,
    POWERSHELL_INJECTION_NODE_TYPES,
    POWERSHELL_READ_ONLY_COMMANDS,
    POWERSHELL_REMOVE_ITEM_DANGEROUS_PARAMS,
    POWERSHELL_SET_ITEM_PROPERTY_PATH_PARAMS,
    POWERSHELL_STOP_PROCESS_DANGEROUS_PARAMS,
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
        self._cache_key: str | None = None
        self._cache_tree: Tree | None = None

    def _parse(self, command: str) -> Tree:
        """Parse ``command``, memoizing the tree for repeated checks."""
        if self._cache_key != command or self._cache_tree is None:
            self._cache_tree = self.parser.parse(bytes(command, "utf8"))
            self._cache_key = command
        return self._cache_tree

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

        tree = self._parse(command)
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

    def extract_canonical_command_names(self, command: str) -> List[str]:
        """Return alias-normalized cmdlet names for every command node.

        Args:
            command (`str`):
                PowerShell command text.

        Returns:
            `List[str]`:
                Canonical names in source order (may be empty on ERROR).
        """
        if not command.strip():
            return []
        tree = self._parse(command)
        if self._has_error_nodes(tree.root_node):
            return []
        names: list[str] = []
        for cmd_node in self._extract_command_nodes(tree.root_node):
            raw = self._command_name_text(command, cmd_node)
            if raw:
                names.append(self.normalize_cmdlet_name(raw))
        return names

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

        tree = self._parse(cmd)
        root = tree.root_node

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

        tree = self._parse(cmd)
        root = tree.root_node

        download_pattern = self._check_download_to_iex(cmd, root)
        if download_pattern:
            return download_pattern

        for cmd_node in self._extract_command_nodes(root):
            name = self._command_name_text(cmd, cmd_node)
            if not name:
                continue
            canonical = self.normalize_cmdlet_name(name)
            folded = canonical.casefold()
            if folded in self._dangerous_lookup:
                return self._dangerous_lookup[folded]

            resolved = self._resolved_parameters(cmd, cmd_node)

            if folded == "remove-item" and resolved & {
                p.casefold() for p in POWERSHELL_REMOVE_ITEM_DANGEROUS_PARAMS
            }:
                return "Remove-Item -Recurse/-Force"

            if folded == "stop-process" and "-force" in resolved:
                return "Stop-Process -Force"

            if folded == "set-itemproperty":
                hklm = self._set_itemproperty_hklm_status(cmd, cmd_node)
                if hklm == "hklm":
                    return "Set-ItemProperty HKLM:"

        # Textual fallback only when the AST is unusable.
        if self._has_error_nodes(root):
            normalized = " ".join(cmd.split())
            for pattern in POWERSHELL_DANGEROUS_COMMANDS:
                if re.search(
                    r"(?i)\b" + re.escape(pattern) + r"\b",
                    normalized,
                ):
                    return pattern
                for alias in self._alias_for_canonical(pattern):
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

        if self._has_encoded_command_flag(cmd):
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

        tree = self._parse(cmd)
        root = tree.root_node

        if self._has_error_nodes(root):
            return "Command parsing failed, cannot verify safety"

        for node in self._iter_nodes(root):
            if node.type in POWERSHELL_INJECTION_NODE_TYPES:
                return (
                    f"Command contains {node.type} which cannot be "
                    f"statically analyzed"
                )

        for cmd_node in self._extract_command_nodes(root):
            if self._has_child_type(cmd_node, "command_invocation_operator"):
                return (
                    "Command contains call operator (&/.) which cannot be "
                    "statically analyzed"
                )
            name = self._command_name_text(cmd, cmd_node)
            if not name:
                continue
            canonical = self.normalize_cmdlet_name(name)
            folded = canonical.casefold()
            if folded == "invoke-expression":
                return (
                    "Command contains Invoke-Expression which cannot be "
                    "statically analyzed"
                )
            if folded == "set-itemproperty":
                status = self._set_itemproperty_hklm_status(cmd, cmd_node)
                if status == "dynamic":
                    return (
                        "Command contains dynamic Set-ItemProperty -Path "
                        "which cannot be statically analyzed"
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

        tree = self._parse(command)
        root = tree.root_node

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
        if folded in self._dangerous_lookup:
            return False
        return folded in self._readonly_lookup

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

    def _command_parameter_pairs(
        self,
        source: str,
        cmd_node: Node,
    ) -> list[Tuple[str, Optional[Node]]]:
        """Return ``(raw_param, value_node)`` pairs for a command."""
        elements = None
        for child in cmd_node.children:
            if child.type == "command_elements":
                elements = child
                break
        if elements is None:
            return []

        pairs: list[Tuple[str, Optional[Node]]] = []
        kids = list(elements.children)
        i = 0
        while i < len(kids):
            node = kids[i]
            if node.type == "command_parameter":
                raw = source[node.start_byte : node.end_byte]
                value: Optional[Node] = None
                j = i + 1
                while j < len(kids) and kids[j].type == "command_argument_sep":
                    j += 1
                if j < len(kids) and kids[j].type != "command_parameter":
                    value = kids[j]
                    i = j
                pairs.append((raw, value))
            i += 1
        return pairs

    def _resolve_parameter_name(
        self,
        raw: str,
        known: frozenset[str],
    ) -> Optional[str]:
        """Resolve a possibly abbreviated parameter against ``known``."""
        folded = raw.casefold()
        exact = [p for p in known if p.casefold() == folded]
        if exact:
            return exact[0]
        matches = [p for p in known if p.casefold().startswith(folded)]
        if len(matches) == 1:
            return matches[0]
        return None

    def _resolved_parameters(
        self,
        source: str,
        cmd_node: Node,
    ) -> Set[str]:
        """Resolve abbreviated switch names for dangerous-parameter checks."""
        known = (
            POWERSHELL_REMOVE_ITEM_DANGEROUS_PARAMS
            | POWERSHELL_STOP_PROCESS_DANGEROUS_PARAMS
            | POWERSHELL_SET_ITEM_PROPERTY_PATH_PARAMS
        )
        resolved: Set[str] = set()
        for raw, _value in self._command_parameter_pairs(source, cmd_node):
            canonical = self._resolve_parameter_name(raw, known)
            if canonical is not None:
                resolved.add(canonical.casefold())
        return resolved

    def _set_itemproperty_hklm_status(
        self,
        source: str,
        cmd_node: Node,
    ) -> Optional[str]:
        """Classify Set-ItemProperty path: ``hklm``, ``dynamic``, or None."""
        for raw, value in self._command_parameter_pairs(source, cmd_node):
            resolved = self._resolve_parameter_name(
                raw,
                POWERSHELL_SET_ITEM_PROPERTY_PATH_PARAMS,
            )
            if resolved is None or value is None:
                continue
            if value.type == "variable" or self._contains_node_types(
                value,
                {"variable", "sub_expression", "expandable_string_literal"},
            ):
                return "dynamic"
            text = source[value.start_byte : value.end_byte].strip("\"'")
            if re.search(r"(?i)^HKLM:", text):
                return "hklm"
        return None

    def _check_download_to_iex(
        self,
        source: str,
        root: Node,
    ) -> Optional[str]:
        """Detect adjacent download→iex pipelines via the AST only."""
        download = {"invoke-restmethod", "invoke-webrequest"}

        for node in self._iter_nodes(root):
            if node.type != "pipeline_chain":
                continue
            commands = [c for c in node.children if c.type == "command"]
            for left, right in zip(commands, commands[1:]):
                left_name = self._command_name_text(source, left)
                right_name = self._command_name_text(source, right)
                if not left_name or not right_name:
                    continue
                if (
                    self.normalize_cmdlet_name(left_name).casefold()
                    in download
                    and self.normalize_cmdlet_name(right_name).casefold()
                    == "invoke-expression"
                ):
                    return "download-to-iex"

        # iex (irm ...) — download nested under Invoke-Expression
        for cmd_node in self._extract_command_nodes(root):
            name = self._command_name_text(source, cmd_node)
            if not name:
                continue
            if self.normalize_cmdlet_name(name).casefold() != (
                "invoke-expression"
            ):
                continue
            for nested in self._extract_command_nodes(cmd_node):
                if nested is cmd_node:
                    continue
                nested_name = self._command_name_text(source, nested)
                if (
                    nested_name
                    and self.normalize_cmdlet_name(nested_name).casefold()
                    in download
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

    def _has_encoded_command_flag(self, command: str) -> bool:
        """Detect ``-EncodedCommand`` including unique abbreviations."""
        # ``-en...`` is unique vs ``-ErrorAction`` (which needs ``-er...``).
        for match in re.finditer(
            r"(?i)(^|[\s|;])(-en[a-z]*)\b",
            command,
        ):
            flag = match.group(2).lstrip("-").casefold()
            if "encodedcommand".startswith(flag):
                return True
        # ``pwsh -e`` / ``powershell -e`` (CLI short form).
        return bool(
            re.search(
                r"(?i)\b(pwsh|powershell)(\.exe)?(\s+\S+)*\s+-e\b",
                command,
            ),
        )

    def _strip_quoted_strings(self, command: str) -> str:
        """Remove single- and double-quoted spans for heuristic scans."""
        return re.sub(
            r"'(?:[^']|'')*'|\"(?:[^\"`]|`.)*\"",
            '""',
            command,
        )

    def _has_backtick_obfuscation(self, command: str) -> bool:
        """Detect backtick-obfuscated command text."""
        unscanned = self._strip_quoted_strings(command)
        # e.g. Inv`oke-Expression or Get`-ChildItem
        if re.search(r"[A-Za-z]`+[A-Za-z]", unscanned):
            return True
        return unscanned.count("`") >= 3

    def _has_string_built_cmdlet(self, command: str) -> bool:
        """Detect string-concatenated / expandable cmdlet names."""
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
