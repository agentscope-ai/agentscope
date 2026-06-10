# -*- coding: utf-8 -*-
"""Dynamic subagent middleware.

Scans a local ``subagents/`` directory at runtime and injects discovered
agent definitions into the system prompt before each reasoning step.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import AsyncGenerator, Callable, TYPE_CHECKING

from ._base import MiddlewareBase
from .._logging import logger

if TYPE_CHECKING:
    from ..agent import Agent


class SubagentSpec:
    """Lightweight descriptor for a dynamically-discovered subagent."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        description: str = "",
        model: str | None = None,
    ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.description = description
        self.model = model

    def render(self) -> str:
        lines = [f"### {self.name}"]
        if self.description:
            lines.append(self.description)
        if self.model:
            lines.append(f"Model: {self.model}")
        lines.append(f"System prompt: {self.system_prompt[:200]}")
        return "\n".join(lines)


class DynamicSubagentsMiddleware(MiddlewareBase):
    """Injects dynamically-discovered subagent specs into the system prompt.

    Usage::

        mw = DynamicSubagentsMiddleware(search_dir="subagents")
        agent = Agent(..., middlewares=[mw])
    """

    def __init__(
        self,
        search_dir: str = "subagents",
        scan_interval_seconds: float = 30.0,
    ) -> None:
        self.search_dir = Path(search_dir)
        self.scan_interval_seconds = scan_interval_seconds
        self._last_scan: float = 0.0
        self._specs: list[SubagentSpec] = []

    async def on_system_prompt(
        self,
        agent: "Agent",
        current_prompt: str,
    ) -> str:
        import time

        now = time.time()
        if now - self._last_scan > self.scan_interval_seconds:
            self._specs = self._load_specs()
            self._last_scan = now

        if not self._specs:
            return current_prompt

        section = (
            "\n<system-reminder>\n"
            "Available sub-agents you can spawn:\n"
            + "\n\n".join(s.render() for s in self._specs)
            + "\n</system-reminder>"
        )
        return current_prompt + section

    def _load_specs(self) -> list[SubagentSpec]:
        specs: list[SubagentSpec] = []
        if not self.search_dir.exists():
            return specs

        for path in sorted(self.search_dir.iterdir()):
            if path.suffix.lower() in (".md", ".txt"):
                spec = self._parse_markdown(path)
                if spec:
                    specs.append(spec)
            elif path.suffix.lower() in (".json", ".yaml", ".yml"):
                spec = self._parse_structured(path)
                if spec:
                    specs.append(spec)

        logger.debug(
            "DynamicSubagentsMiddleware loaded %d specs from %s",
            len(specs),
            self.search_dir,
        )
        return specs

    def _parse_markdown(self, path: Path) -> SubagentSpec | None:
        text = path.read_text(encoding="utf-8")
        # First h1 / h2 as name, rest as system prompt
        lines = text.splitlines()
        name = path.stem
        body_lines: list[str] = []
        for line in lines:
            if line.startswith("# ") and name == path.stem:
                name = line[2:].strip()
            else:
                body_lines.append(line)
        return SubagentSpec(
            name=name,
            system_prompt="\n".join(body_lines).strip(),
        )

    def _parse_structured(self, path: Path) -> SubagentSpec | None:
        text = path.read_text(encoding="utf-8")
        try:
            if path.suffix.lower() == ".json":
                import json
                data = json.loads(text)
            else:
                import yaml
                data = yaml.safe_load(text)
        except Exception:
            return None

        if not isinstance(data, dict):
            return None
        return SubagentSpec(
            name=data.get("name", path.stem),
            system_prompt=data.get("system_prompt", ""),
            description=data.get("description", ""),
            model=data.get("model"),
        )
