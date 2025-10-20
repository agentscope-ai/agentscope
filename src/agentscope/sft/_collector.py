# -*- coding: utf-8 -*-
"""SFT data collection utilities.

This module provides a simple JSONL writer to persist model I/O for
supervised fine-tuning (SFT). Each record corresponds to a single model
invocation and contains the input messages and available tools.

Design goals:
- Minimal dependencies and simple file IO
- Async-friendly API (but uses sync file writes for portability)
- Safe to call frequently; append-only JSONL
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class SFTRecord:
    """A single SFT data record to be written as JSONL.

    Notes
    -----
    - "messages" and "tools" are stored as JSON-encoded strings to make the
      downstream ingestion consistent with common SFT pipelines that expect
      string fields in tabular formats.
    """

    messages: list[dict]
    tools: list[dict] | None
    metadata: dict[str, Any] | None = None

    def to_jsonl(self) -> str:
        payload = {
            "messages": json.dumps(self.messages, ensure_ascii=False),
            "tools": json.dumps(self.tools or [], ensure_ascii=False),
        }
        if self.metadata:
            payload["metadata"] = self.metadata
        return json.dumps(payload, ensure_ascii=False)


class SFTDataCollector:
    """Append-only JSONL data collector for SFT records.

    Parameters
    ----------
    output_path:
        The file path to write JSONL lines to. Parent directories will be
        created if not present.
    enable_collection:
        Global switch to enable/disable writing.
    """

    def __init__(
        self,
        output_path: str,
        enable_collection: bool = True,
    ) -> None:
        self.output_path = output_path
        self.enable_collection = enable_collection

        # Ensure directory exists early to fail fast on permission issues
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)

    async def collect(
        self,
        messages: list[dict],
        tools: list[dict] | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist a single model call as one JSONL line.

        This function is async for ergonomic symmetry, but uses synchronous
        file IO for portability and simplicity (atomic append behavior on POSIX
        systems).
        """
        if not self.enable_collection:
            return

        record = SFTRecord(
            messages=messages,
            tools=tools or [],
            metadata={
                **(metadata or {}),
                "collected_at": datetime.utcnow().isoformat() + "Z",
            },
        )

        line = record.to_jsonl()
        with open(self.output_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def build_collector_from_env(default_path: str | None = None) -> SFTDataCollector | None:
    """Create a collector from environment variables.

    Controlled by:
    - ENABLE_SFT_COLLECTION: "true"/"false" (case-insensitive)
    - SFT_OUTPUT_PATH: JSONL file path
    """
    enable = os.getenv("ENABLE_SFT_COLLECTION", "false").lower() == "true"
    if not enable:
        return None

    path = os.getenv("SFT_OUTPUT_PATH", default_path or "sft_data.jsonl")
    return SFTDataCollector(output_path=path, enable_collection=True)


