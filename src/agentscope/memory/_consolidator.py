# -*- coding: utf-8 -*-
"""Memory consolidator — merges daily ledgers into curated MEMORY.md."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import TYPE_CHECKING

from .._logging import logger
from ..message import Msg, SystemMsg, UserMsg

if TYPE_CHECKING:
    from ..model import ChatModelBase


DEFAULT_CONSOLIDATION_PROMPT = (
    "You are a memory consolidation assistant. You own the curated long-term "
    "memory file MEMORY.md. Your job is to merge new daily ledger entries "
    "into MEMORY.md while keeping it concise, deduplicated, and high-signal.\n\n"
    "You are given two inputs:\n"
    "1. The current MEMORY.md content (the existing curated long-term memory).\n"
    "2. New daily ledger entries that have been appended since the last consolidation.\n\n"
    "Rules:\n"
    "- MEMORY.md is the single source of truth for cross-day, cross-session knowledge.\n"
    "- Daily ledger entries are stream-of-consciousness flush logs — they may be noisy, "
    "redundant with MEMORY.md, or redundant with each other. Promote only what is durable.\n"
    "- Deduplicate: if a new entry restates something MEMORY.md already covers, skip it.\n"
    "- Merge related facts: combine entries about the same topic into cohesive paragraphs.\n"
    "- Update or remove stale information when new entries supersede it.\n"
    "- Keep total output within %d tokens (approximately %d characters).\n"
    "- Output the COMPLETE new MEMORY.md content (not just a diff). Use markdown."
)


class MemoryConsolidator:
    """Periodically reads daily ledger files and merges them into MEMORY.md."""

    STATE_FILE = ".consolidation_state"

    def __init__(
        self,
        model: ChatModelBase,
        memory_dir: str = "memory",
        consolidation_prompt: str | None = None,
        max_memory_tokens: int = 4000,
    ) -> None:
        self.model = model
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.consolidation_prompt = (
            consolidation_prompt or DEFAULT_CONSOLIDATION_PROMPT
        )
        self.max_memory_tokens = max_memory_tokens

    async def consolidate(self) -> bool:
        """Run consolidation if fresh daily entries exist.

        Returns:
            ``True`` if consolidation was performed.
        """
        watermark = self._read_watermark()
        run_start = time.time()

        current_memory = self._read_file("MEMORY.md")
        daily_entries = self._read_daily_entries(watermark)

        if not daily_entries.strip():
            logger.debug("No fresh daily entries since watermark — skipping consolidation")
            return False

        max_chars = self.max_memory_tokens * 4
        system_prompt = self.consolidation_prompt % (
            self.max_memory_tokens,
            max_chars,
        )

        user_content = (
            "Current MEMORY.md:\n"
            + (current_memory or "(empty)")
            + "\n\nNew daily ledger entries:\n"
            + daily_entries
        )

        inputs = [
            SystemMsg("system", system_prompt),
            UserMsg("user", user_content),
        ]

        try:
            response = await self.model.generate_structured_output(
                messages=inputs,
                structured_model={
                    "type": "object",
                    "properties": {
                        "memory_md": {
                            "type": "string",
                            "description": "The complete new MEMORY.md content in markdown",
                        },
                    },
                    "required": ["memory_md"],
                },
            )
            new_memory = response.content.get("memory_md", "").strip()
        except Exception as e:
            logger.warning("Consolidation LLM call failed: %s", e)
            return False

        if not new_memory:
            return False

        self._write_file("MEMORY.md", new_memory)
        self._write_watermark(run_start)
        logger.info(
            "[MemoryConsolidator] Consolidated into MEMORY.md (%d chars)",
            len(new_memory),
        )
        return True

    def _read_file(self, rel_path: str) -> str:
        path = self.memory_dir / rel_path
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _write_file(self, rel_path: str, content: str) -> None:
        path = self.memory_dir / rel_path
        path.write_text(content, encoding="utf-8")

    def _read_daily_entries(self, since_timestamp: float) -> str:
        parts: list[str] = []
        for path in sorted(self.memory_dir.glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            if path.stat().st_mtime <= since_timestamp:
                continue
            parts.append(f"--- {path.name} ---\n" + path.read_text(encoding="utf-8"))
        return "\n\n".join(parts)

    def _read_watermark(self) -> float:
        state_file = self.memory_dir / self.STATE_FILE
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text(encoding="utf-8"))
                return data.get("last_consolidation", 0.0)
            except Exception:
                pass
        return 0.0

    def _write_watermark(self, timestamp: float) -> None:
        state_file = self.memory_dir / self.STATE_FILE
        state_file.write_text(
            json.dumps({"last_consolidation": timestamp}, indent=2),
            encoding="utf-8",
        )
