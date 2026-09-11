# -*- coding: utf-8 -*-
"""Shared wildcard permission-rule matching for shell tools."""

from __future__ import annotations

import re


def match_wildcard_pattern(rule_content: str, command: str) -> bool:
    """Match a command against Bash/PowerShell wildcard rule grammar.

    Callers are responsible for any tool-specific preprocessing (alias
    normalization, casefolding, pipeline splitting). This helper covers:

    - ``:*`` prefix patterns (``git:*``)
    - ``*`` wildcards with ``\\*`` / ``\\\\`` escapes
    - substring match when the pattern has no unescaped ``*``

    Args:
        rule_content (`str`):
            Preprocessed rule pattern (not ``None``).
        command (`str`):
            Preprocessed command text to match.

    Returns:
        `bool`:
            ``True`` when ``command`` matches ``rule_content``.
    """
    if rule_content.endswith(":*"):
        prefix = rule_content[:-2].strip()
        return command.startswith(prefix + " ") or command == prefix

    def has_wildcards(pattern: str) -> bool:
        """Return whether ``pattern`` contains an unescaped ``*``."""
        i = 0
        while i < len(pattern):
            if pattern[i] == "\\":
                i += 2
            elif pattern[i] == "*":
                return True
            else:
                i += 1
        return False

    if not has_wildcards(rule_content):
        pattern = rule_content
        pattern = pattern.replace("\\\\", "\x00BACKSLASH\x00")
        pattern = pattern.replace("\\*", "*")
        pattern = pattern.replace("\x00BACKSLASH\x00", "\\")
        return pattern in command

    escaped_star = "\x00ESCAPED_STAR\x00"
    escaped_backslash = "\x00ESCAPED_BACKSLASH\x00"

    pattern = rule_content
    pattern = pattern.replace("\\\\", escaped_backslash)
    pattern = pattern.replace("\\*", escaped_star)

    special_chars = r".^$+?{}[]|()"
    for char in special_chars:
        pattern = pattern.replace(char, "\\" + char)

    pattern = pattern.replace("*", ".*")
    pattern = pattern.replace(escaped_star, r"\*")
    pattern = pattern.replace(escaped_backslash, r"\\")

    if pattern.endswith(".*"):
        base_pattern = pattern[:-2].rstrip()
        if re.fullmatch(base_pattern, command):
            return True

    try:
        return bool(re.fullmatch(pattern, command))
    except re.error:
        return rule_content.replace("*", "") in command
