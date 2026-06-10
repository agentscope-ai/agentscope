# -*- coding: utf-8 -*-
"""Integration tests for migrated features using a live LLM.

Requires a model server at MODEL_URL (defaults to local DeepSeek v4 flash).
These tests are skipped if the server is unreachable.
"""
# pylint: disable=protected-access
import os
import shutil
import tempfile
from pathlib import Path
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.agent import Agent
from agentscope.state import AgentState
from agentscope.memory import MemoryFlushManager, MemoryConsolidator
from agentscope.message import UserMsg, AssistantMsg, SystemMsg
from agentscope.credential import OpenAICredential
from agentscope.model import OpenAIChatModel
from agentscope.skill import SkillCurator, TrustLevel, SkillState
from agentscope.tool import Toolkit


class _ModelMixin:
    """Helper to build a live model client for integration tests."""

    @classmethod
    def _get_model(cls):
        url = os.getenv("MODEL_URL", "http://127.0.0.1:8080/v1")
        api_key = os.getenv("MODEL_API_KEY", "yuanjun")
        try:
            cred = OpenAICredential(base_url=url, api_key=api_key)
            return OpenAIChatModel(
                credential=cred,
                model="deepseek-v4-flash",
                stream=False,
            )
        except Exception:
            return None


class TestMemoryFlushManager(IsolatedAsyncioTestCase, _ModelMixin):
    """Live-model test for memory extraction into daily ledger."""

    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.model = self._get_model()
        if self.model is None:
            self.skipTest("Live model unavailable")
        self.flush_mgr = MemoryFlushManager(
            model=self.model,
            memory_dir=self.tmpdir,
        )

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_flush_extracts_memories(self) -> None:
        messages = [
            SystemMsg("system", "You are a helpful assistant."),
            UserMsg("user", "My name is Alice and I work on Project Alpha."),
            AssistantMsg(
                "assistant",
                "Nice to meet you, Alice! I'll remember that you're working on Project Alpha.",
            ),
            UserMsg("user", "Please use Python for all code examples."),
            AssistantMsg("assistant", "Got it — I'll use Python going forward."),
        ]
        memories = await self.flush_mgr.flush(messages)
        self.assertIsNotNone(memories)
        self.assertGreater(len(memories), 10)
        # Daily file should exist
        daily_files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(len(daily_files), 1)
        content = daily_files[0].read_text(encoding="utf-8")
        self.assertIn(memories, content)

    async def test_flush_no_memories_for_trivial_chat(self) -> None:
        messages = [
            UserMsg("user", "Hi"),
            AssistantMsg("assistant", "Hello!"),
        ]
        memories = await self.flush_mgr.flush(messages)
        self.assertIsNone(memories)


class TestMemoryConsolidator(IsolatedAsyncioTestCase, _ModelMixin):
    """Live-model test for memory consolidation into MEMORY.md."""

    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.model = self._get_model()
        if self.model is None:
            self.skipTest("Live model unavailable")
        self.consolidator = MemoryConsolidator(
            model=self.model,
            memory_dir=self.tmpdir,
            max_memory_tokens=500,
        )

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_consolidation_produces_memory_md(self) -> None:
        # Seed a daily ledger
        from datetime import date

        daily_path = Path(self.tmpdir) / f"{date.today().isoformat()}.md"
        daily_path.write_text(
            "- Alice works on Project Alpha\n- Prefers Python for code\n",
            encoding="utf-8",
        )

        ok = await self.consolidator.consolidate()
        self.assertTrue(ok)

        memory_md = Path(self.tmpdir) / "MEMORY.md"
        self.assertTrue(memory_md.exists())
        content = memory_md.read_text(encoding="utf-8")
        self.assertGreater(len(content), 10)
        # Should mention Alice or Python
        self.assertTrue(
            "Alice" in content or "Python" in content or "Project Alpha" in content,
            f"Consolidated memory seems off-topic: {content[:200]}",
        )

    async def test_consolidation_skips_when_no_new_entries(self) -> None:
        ok = await self.consolidator.consolidate()
        self.assertFalse(ok)


class TestSkillCuratorIntegration(IsolatedAsyncioTestCase):
    """End-to-end skill lifecycle with real filesystem and scanner."""

    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.curator = SkillCurator(state_dir=self.tmpdir)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_lifecycle_safe_and_dangerous(self) -> None:
        safe = self.curator.register("safe", "A benign skill.")
        self.assertEqual(safe.state, SkillState.ACTIVE)

        dangerous = self.curator.register(
            "dangerous",
            "```bash\nrm -rf / \n```",
            trust=TrustLevel.AGENT_CREATED,
        )
        self.assertEqual(dangerous.state, SkillState.ARCHIVED)
        self.assertEqual(dangerous.scan_verdict.value, "DANGEROUS")

    def test_reactivation_after_fix(self) -> None:
        self.curator.register(
            "fixable",
            "```bash\nrm -rf /\n```",
            trust=TrustLevel.TRUSTED,
        )
        self.assertEqual(self.curator.get("fixable").state, SkillState.ARCHIVED)

        # Fix the skill and re-register
        self.curator.register(
            "fixable",
            "A safe skill after fix.",
            trust=TrustLevel.TRUSTED,
        )
        self.assertEqual(self.curator.get("fixable").state, SkillState.ACTIVE)



# ------------------------------------------------------------------
# Middleware integration tests
# ------------------------------------------------------------------
class TestMemoryFlushMiddlewareIntegration(IsolatedAsyncioTestCase, _ModelMixin):
    """Live-model test for automatic memory flush middleware."""

    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.model = self._get_model()
        if self.model is None:
            self.skipTest("Live model unavailable")
        self.reply_model = self._get_model()

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_middleware_extracts_memories_after_reply(self) -> None:
        from agentscope.middleware import MemoryFlushMiddleware, FlushTrigger

        mw = MemoryFlushMiddleware(
            model=self.model,
            memory_dir=self.tmpdir,
            flush_trigger=FlushTrigger.always(),
        )
        agent = Agent(
            name="test",
            system_prompt="You are a helpful assistant.",
            model=self.reply_model,
            middlewares=[mw],
            state=AgentState(
                context=[
                    UserMsg("user", "My name is Bob and I prefer TypeScript."),
                ]
            ),
            toolkit=Toolkit(),
        )
        await agent.reply(UserMsg("user", "Hello!"))

        daily_files = list(Path(self.tmpdir).glob("*.md"))
        self.assertEqual(len(daily_files), 1)
        content = daily_files[0].read_text()
        self.assertTrue(
            "Bob" in content or "TypeScript" in content,
            f"Expected memory about Bob/TypeScript, got: {content[:200]}",
        )


class TestMemoryMaintenanceMiddlewareIntegration(IsolatedAsyncioTestCase, _ModelMixin):
    """Live-model test for maintenance middleware with real consolidation."""

    async def asyncSetUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.model = self._get_model()
        if self.model is None:
            self.skipTest("Live model unavailable")
        self.memory_dir = Path(self.tmpdir) / "memory"
        self.memory_dir.mkdir()
        # Seed a daily ledger
        from datetime import date
        daily_path = self.memory_dir / f"{date.today().isoformat()}.md"
        daily_path.write_text(
            "- Charlie works on Project Beta\n- Prefers Rust for systems code\n",
            encoding="utf-8",
        )
        self.consolidator = MemoryConsolidator(
            model=self.model,
            memory_dir=str(self.memory_dir),
            max_memory_tokens=500,
        )

    async def asyncTearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    async def test_maintenance_consolidates_and_archives(self) -> None:
        from agentscope.middleware import MemoryMaintenanceMiddleware

        mw = MemoryMaintenanceMiddleware(
            consolidator=self.consolidator,
            memory_dir=str(self.memory_dir),
            min_gap_seconds=0,
        )
        # Run maintenance directly
        await mw._run_maintenance()

        memory_md = self.memory_dir / "MEMORY.md"
        self.assertTrue(memory_md.exists())
        content = memory_md.read_text(encoding="utf-8")
        self.assertTrue(
            "Charlie" in content or "Rust" in content or "Project Beta" in content,
            f"Consolidated memory seems off-topic: {content[:200]}",
        )
