# -*- coding: utf-8 -*-
"""Memory flush manager — extracts long-term memories into a daily ledger."""
from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from .._logging import logger
from ..message import Msg, SystemMsg, UserMsg, TextBlock

if TYPE_CHECKING:
    from ..model import ChatModelBase


DEFAULT_FLUSH_PROMPT = (
    "You are a memory extraction assistant. Analyze the conversation below "
    "and extract important facts, decisions, preferences, and contextual "
    "information that should be remembered for future conversations.\n\n"
    "Output ONLY the extracted memories as a markdown bullet list. Each item "
    "should be a concise, self-contained fact. Include dates, names, and "
    "specifics when available.\n\n"
    "If there is nothing worth remembering, respond with exactly: NO_REPLY\n\n"
    "Guidelines:\n"
    "- Extract user preferences, personal information, project decisions\n"
    "- Capture important technical decisions and their rationale\n"
    "- Note any commitments, deadlines, or action items\n"
    "- Ignore routine greetings, tool invocations, and ephemeral updates\n"
    "- You are writing to TODAY'S daily memory ledger (memory/YYYY-MM-DD.md)\n"
    "- Keep each bullet point independent and self-contained"
)


class MemoryFlushManager:
    """Extracts memories from a conversation window and appends them to a
    daily markdown ledger.

    Two-layer model:
    - ``memory/YYYY-MM-DD.md`` — append-only daily ledger (this class writes)
    - ``MEMORY.md`` — curated long-term memory (MemoryConsolidator writes)
    """

    def __init__(
        self,
        model: ChatModelBase,
        memory_dir: str = "memory",
        flush_prompt: str | None = None,
    ) -> None:
        self.model = model
        self.memory_dir = Path(memory_dir)
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.flush_prompt = flush_prompt or DEFAULT_FLUSH_PROMPT

    async def flush(self, messages: list[Msg]) -> str | None:
        """Extract memories and append to today's ledger.

        Returns:
            The extracted memory text, or ``None`` if nothing was extracted.
        """
        conversation_text = self._serialize_messages(messages)
        if not conversation_text.strip():
            return None

        memory_md = self._read_file("MEMORY.md")
        today = datetime.date.today().isoformat()
        daily_path = f"memory/{today}.md"
        daily_existing = self._read_file(daily_path)

        user_parts: list[str] = []
        if memory_md:
            user_parts.append(
                "MEMORY.md (read-only curated memory — do NOT restate):\n"
                + memory_md
            )
        if daily_existing:
            user_parts.append(
                "Today's daily ledger so far (your output will be appended):\n"
                + daily_existing
            )
        user_parts.append(
            "Extract NEW memories from this conversation (skip anything already "
            "covered above):\n\n" + conversation_text
        )

        inputs = [
            SystemMsg("system", self.flush_prompt),
            UserMsg("user", "\n\n".join(user_parts)),
        ]

        try:
            response = await self.model.generate_structured_output(
                messages=inputs,
                structured_model={
                    "type": "object",
                    "properties": {
                        "memories": {
                            "type": "string",
                            "description": "Markdown bullet list of extracted memories",
                        },
                    },
                    "required": ["memories"],
                },
            )
            memories = response.content.get("memories", "").strip()
        except Exception as e:
            logger.warning("Memory extraction LLM call failed: %s", e)
            return None

        if not memories or memories == "NO_REPLY":
            return None

        self._append_daily(today, memories)
        logger.info("[MemoryFlush] Extracted %d chars to %s", len(memories), daily_path)
        return memories

    def _serialize_messages(self, messages: list[Msg]) -> str:
        lines: list[str] = []
        for msg in messages:
            role = msg.role or "unknown"
            content = msg.content
            if isinstance(content, list):
                texts = []
                for block in content:
                    if hasattr(block, "text"):
                        texts.append(block.text)
                    elif hasattr(block, "output"):
                        texts.append(str(block.output))
                content = "\n".join(texts)
            lines.append(f"[{role}] {content}")
        return "\n\n".join(lines)

    def _read_file(self, rel_path: str) -> str:
        path = self.memory_dir / rel_path
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _append_daily(self, date_str: str, text: str) -> None:
        daily_file = self.memory_dir / f"{date_str}.md"
        header = f"\n## Flush at {datetime.datetime.now().isoformat()}\n\n"
        with daily_file.open("a", encoding="utf-8") as f:
            f.write(header + text + "\n")
