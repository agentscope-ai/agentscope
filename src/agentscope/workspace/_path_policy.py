# -*- coding: utf-8 -*-
"""PathPolicy — immutable allow-list of directories for absolute-path access.

Ported from Java ``io.agentscope.harness.agent.workspace.PathPolicy``.
"""
from __future__ import annotations

from pathlib import Path


class PathPolicy:
    """Immutable allow-list of host directories that agent file tools may reach.

    Usage::

        policy = PathPolicy.of(project="/home/user/my-project", workspace="/tmp/agent")
        policy.is_allowed(Path("/home/user/my-project/src/main.py"))  # True
        policy.is_allowed(Path("/etc/passwd"))  # False
    """

    _EMPTY: "PathPolicy | None" = None

    def __init__(self, roots: list[Path]) -> None:
        self._roots = [r for r in roots]

    @classmethod
    def empty(cls) -> "PathPolicy":
        if cls._EMPTY is None:
            cls._EMPTY = cls([])
        return cls._EMPTY

    @classmethod
    def of(cls, *roots: str | Path) -> "PathPolicy":
        """Build a policy from one or more root paths."""
        normalized: list[Path] = []
        for r in roots:
            if r is None:
                continue
            p = Path(r).resolve()
            normalized.append(p)
        return cls.empty() if not normalized else cls(normalized)

    @property
    def roots(self) -> list[Path]:
        return list(self._roots)

    def is_empty(self) -> bool:
        return not self._roots

    def is_allowed(self, candidate: str | Path) -> bool:
        """Return True when *candidate* is equal to or below one of the roots."""
        if candidate is None:
            return False
        p = Path(candidate)
        if not p.is_absolute():
            return False
        normalized = p.resolve()
        for root in self._roots:
            try:
                normalized.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def __repr__(self) -> str:
        return f"PathPolicy({self._roots})"
