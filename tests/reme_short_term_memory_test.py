# -*- coding: utf-8 -*-
"""Tests for the ReMe short-term memory example."""
import runpy
import sys
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agentscope.message import Msg
from agentscope.model import DashScopeChatModel


class ReMeShortTermMemoryTest(IsolatedAsyncioTestCase):
    """Exercise the memory protocol without a ReMe service."""

    async def test_agent_memory_reads_preserve_marks(self) -> None:
        """Managed prompt reads must leave the stored messages addressable."""
        backend = AsyncMock()
        backend.async_execute.return_value = {
            "answer": [{"role": "user", "content": "shortened"}],
        }
        reme = MagicMock()
        reme.ReMeApp.return_value = backend
        model = DashScopeChatModel("test-model", api_key="test-key")
        example = (
            Path(__file__).resolve().parents[1]
            / "examples/functionality/short_term_memory/reme"
            / "reme_short_term_memory.py"
        )
        memory_cls = runpy.run_path(str(example))["ReMeShortTermMemory"]
        with patch.dict(sys.modules, {"reme_ai": reme}):
            memory = memory_cls(model=model)
        old = Msg("user", "old message", "user")
        recent = Msg("user", "recent message", "user")
        await memory.add(old, marks="compressed")
        await memory.add(recent)
        prompt = await memory.get_memory(exclude_mark=None)
        self.assertEqual(
            [msg.get_text_content() for msg in prompt],
            ["shortened"],
        )
        await memory.get_memory()
        backend.async_execute.assert_awaited_once()
        await memory.add(Msg("user", "next turn", "user"))
        await memory.get_memory()
        supplied = backend.async_execute.await_args.kwargs["messages"]
        self.assertEqual(
            [message["content"] for message in supplied],
            ["shortened", "next turn"],
        )
        await memory.update_compressed_summary("prior summary")
        remaining = await memory.get_memory(exclude_mark="compressed")
        self.assertEqual(remaining[0].get_text_content(), "prior summary")
        self.assertEqual(remaining[1].id, recent.id)
        marked = await memory.get_memory(
            mark="compressed",
            prepend_summary=False,
        )
        self.assertEqual([msg.id for msg in marked], [old.id])
        self.assertEqual(backend.async_execute.await_count, 2)
        await memory.delete([old.id])
        await memory.get_memory(prepend_summary=False)
        supplied = backend.async_execute.await_args.kwargs["messages"]
        self.assertEqual(
            [message["content"] for message in supplied],
            ["recent message", "next turn"],
        )
