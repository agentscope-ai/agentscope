# -*- coding: utf-8 -*-
"""Tool result eviction middleware.

Replaces oversized tool results in the agent's context with compact
placeholders that reference an offloaded file, preventing a single large
tool output from bursting the context window.
"""
from __future__ import annotations

import json
from typing import AsyncGenerator, Callable, TYPE_CHECKING

from ._base import MiddlewareBase
from .._logging import logger
from ..message import Msg, TextBlock, ToolResultBlock

if TYPE_CHECKING:
    from ..agent import Agent


class ToolResultEvictionMiddleware(MiddlewareBase):
    """Evict large tool results from context after each reply.

    This hooks into ``on_reply`` so it can inspect the *delta* of messages
    added during the reply (typically tool-result messages) and offload any
    that exceed the configured size threshold.

    Usage::

        agent = Agent(
            ...,
            middlewares=[
                ToolResultEvictionMiddleware(
                    max_result_chars=80_000,
                    preview_chars=2_000,
                ),
            ],
        )

    Args:
        max_result_chars (`int`):
            Maximum character length of a tool result before eviction.
        preview_chars (`int`):
            Number of characters to keep in the head/tail preview.
        excluded_tools (`set[str]`):
            Tool names that are never evicted (e.g. read_file).
        eviction_path (`str`):
            Directory path prefix for offloaded files.
    """

    DEFAULT_EXCLUDED_TOOLS: set[str] = {
        "read_file",
        "write_file",
        "edit_file",
        "grep_files",
        "glob_files",
        "list_files",
        "memory_search",
        "memory_get",
        "session_search",
    }

    def __init__(
        self,
        max_result_chars: int = 80_000,
        preview_chars: int = 2_000,
        excluded_tools: set[str] | None = None,
        eviction_path: str = "/large_tool_results",
    ) -> None:
        self.max_result_chars = max_result_chars
        self.preview_chars = preview_chars
        self.excluded_tools = excluded_tools or self.DEFAULT_EXCLUDED_TOOLS
        self.eviction_path = eviction_path.rstrip("/")

    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Track context size before reply, evict large results after."""
        context_before = len(agent.state.context)
        async for item in next_handler():
            yield item
        await self._evict_added_results(agent, context_before)

    async def _evict_added_results(
        self,
        agent: "Agent",
        context_before: int,
    ) -> None:
        """Scan messages added since *context_before* and evict large ones."""
        for i in range(context_before, len(agent.state.context)):
            msg = agent.state.context[i]
            if not isinstance(msg, Msg):
                continue
            rebuilt = self._maybe_evict_message(msg, agent)
            if rebuilt is not msg:
                agent.state.context[i] = rebuilt

    def _maybe_evict_message(self, msg: Msg, agent: "Agent") -> Msg:
        """Evict large tool results inside a single message."""
        blocks = msg.get_content_blocks()
        changed = False
        new_blocks: list = []
        for block in blocks:
            if isinstance(block, ToolResultBlock):
                evicted = self._maybe_evict_block(block, agent)
                if evicted is not block:
                    changed = True
                    new_blocks.append(evicted)
                    continue
            new_blocks.append(block)
        if not changed:
            return msg
        # Rebuild the message with evicted blocks
        return Msg(
            name=msg.name,
            role=msg.role,
            content=new_blocks,
        )

    def _maybe_evict_block(
        self,
        block: ToolResultBlock,
        agent: "Agent",
    ) -> ToolResultBlock:
        """Evict a single ToolResultBlock if it exceeds the threshold."""
        if block.name in self.excluded_tools:
            return block

        full_text = self._extract_text(block)
        if len(full_text) <= self.max_result_chars:
            return block

        # Build a safe path for the offloaded content
        safe_agent = "".join(
            c if c.isalnum() or c in "_-" else "_" for c in agent.name
        )
        safe_id = "".join(
            c if c.isalnum() or c in "_-" else "_" for c in (block.id or "")
        )
        path = f"{self.eviction_path}/{safe_agent}/{safe_id}.txt"

        # Try to write via offloader if available, else skip eviction
        if agent.offloader is not None:
            try:
                import asyncio
                asyncio.get_running_loop().run_in_executor(
                    None,
                    lambda: self._write_via_offloader(agent, path, full_text),
                )
            except Exception as e:
                logger.warning(
                    "[ToolResultEviction] Failed to offload %s: %s",
                    path,
                    e,
                )
                return block
        else:
            # No offloader available — skip eviction
            return block

        placeholder = self._build_placeholder(full_text, path)
        logger.info(
            "[ToolResultEviction] Evicted large result for tool=%s id=%s "
            "(%d chars -> %s)",
            block.name,
            block.id,
            len(full_text),
            path,
        )
        return ToolResultBlock(
            id=block.id,
            name=block.name,
            output=[TextBlock(text=placeholder)],
            state=block.state,
        )

    def _write_via_offloader(
        self,
        agent: "Agent",
        path: str,
        content: str,
    ) -> None:
        """Best-effort write via the agent's offloader."""
        try:
            import asyncio
            # Fire-and-forget offload; ignore result
            asyncio.create_task(
                agent.offloader.offload_tool_result(
                    agent.state.session_id,
                    ToolResultBlock(id="", name="", output=content),
                    path=path,
                ),
            )
        except Exception:
            pass

    def _extract_text(self, block: ToolResultBlock) -> str:
        """Extract plain text from a ToolResultBlock."""
        if isinstance(block.output, str):
            return block.output
        parts: list[str] = []
        for item in block.output if isinstance(block.output, list) else []:
            if isinstance(item, TextBlock):
                parts.append(item.text)
        return "".join(parts)

    def _build_placeholder(self, full_text: str, path: str) -> str:
        """Build a compact placeholder with head+tail preview."""
        length = len(full_text)
        preview = min(self.preview_chars, length // 2)

        lines = [
            f"Tool output was too large ({length:,} chars) and has been "
            f"saved to `{path}`.",
            f"To read the full output, use `read_file` with path `{path}`.",
            "",
        ]
        if preview > 0:
            lines.append(f"Preview (first {preview:,} chars):")
            lines.append(full_text[:preview])
            lines.append("")
            lines.append(f"... and last {preview:,} chars:")
            lines.append(full_text[-preview:])

        return "\n".join(lines)
