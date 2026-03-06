# -*- coding: utf-8 -*-
"""Unit tests for PowerMemLongTermMemory."""
from __future__ import annotations

from typing import Awaitable
from unittest import IsolatedAsyncioTestCase

from agentscope.message import Msg
from agentscope.memory import PowerMemLongTermMemory

try:
    import powermem  # noqa: F401
except ImportError:
    powermem = None


class AsyncMemoryStub:
    """Async memory stub for unit tests."""

    def __init__(self) -> None:
        self.add_calls: list[dict] = []
        self.search_calls: list[dict] = []
        self.initialized = False

    async def initialize(self) -> None:
        """Initialize the stub backend."""
        self.initialized = True

    async def add(self, **kwargs: object) -> dict:
        """Collect add calls for assertions."""
        self.add_calls.append(kwargs)
        return {"results": []}

    async def search(self, **kwargs: object) -> dict:
        """Return deterministic search results."""
        self.search_calls.append(kwargs)
        return {
            "results": [{"memory": "prefers tea"}],
            "relations": [
                {
                    "source": "user",
                    "relationship": "likes",
                    "destination": "longjing",
                },
            ],
        }


class SyncAddCoroutineStub:
    """Sync add stub that returns a coroutine for await testing."""

    def __init__(self) -> None:
        self.coro_awaited = False

    def initialize(self) -> None:
        """Initialize the stub backend."""
        return None

    def add(self, **_kwargs: object) -> Awaitable[dict]:
        """Return a coroutine to validate await handling."""

        async def _inner() -> dict:
            self.coro_awaited = True
            return {"results": []}

        return _inner()


class PowerMemLongTermMemoryTest(IsolatedAsyncioTestCase):
    """Test cases for PowerMemLongTermMemory."""

    async def asyncSetUp(self) -> None:
        """Skip tests when powermem is unavailable."""
        if powermem is None:
            self.skipTest("powermem is not installed")

    async def test_record_to_memory_overrides_infer(self) -> None:
        """Ensure infer override is honored for record_to_memory."""
        memory = AsyncMemoryStub()
        long_term_memory = PowerMemLongTermMemory(
            memory=memory,
            agent_name="agent",
            user_name="user",
            run_name="run",
        )

        await long_term_memory.record_to_memory(
            thinking="capture travel preferences",
            content=["prefers tea"],
            infer=False,
        )

        self.assertTrue(memory.initialized)
        self.assertEqual(memory.add_calls[0]["infer"], False)

    async def test_retrieve_from_memory_formats_results(self) -> None:
        """Ensure retrieve_from_memory formats results and relations."""
        memory = AsyncMemoryStub()
        long_term_memory = PowerMemLongTermMemory(
            memory=memory,
            agent_name="agent",
            user_name="user",
            run_name="run",
        )

        result = await long_term_memory.retrieve_from_memory(
            keywords=["tea"],
        )
        text = " ".join(
            block.get("text", "")
            for block in result.content
            if block.get("type") == "text"
        )

        self.assertIn("prefers tea", text)
        self.assertIn("user -- likes -- longjing", text)

    async def test_record_awaits_sync_add_coroutine(self) -> None:
        """Ensure sync add returning coroutine is awaited."""
        memory = SyncAddCoroutineStub()
        long_term_memory = PowerMemLongTermMemory(
            memory=memory,
            agent_name="agent",
            user_name="user",
            run_name="run",
        )

        await long_term_memory.record(
            msgs=[
                Msg(
                    role="user",
                    content="hello",
                    name="user",
                ),
            ],
        )

        self.assertTrue(memory.coro_awaited)

    async def test_default_memory_type_is_forwarded(self) -> None:
        """Ensure default_memory_type is passed to backend add calls."""
        memory = AsyncMemoryStub()
        long_term_memory = PowerMemLongTermMemory(
            memory=memory,
            agent_name="agent",
            user_name="user",
            run_name="run",
            default_memory_type="semantic_memory",
        )

        await long_term_memory.record_to_memory(
            thinking="capture preference",
            content=["prefers tea"],
        )

        self.assertEqual(
            memory.add_calls[0]["memory_type"],
            "semantic_memory",
        )

    async def test_record_to_memory_overrides_memory_type(self) -> None:
        """Ensure record_to_memory memory_type overrides defaults."""
        memory = AsyncMemoryStub()
        long_term_memory = PowerMemLongTermMemory(
            memory=memory,
            agent_name="agent",
            user_name="user",
            run_name="run",
            default_memory_type="semantic_memory",
        )

        await long_term_memory.record_to_memory(
            thinking="capture preference",
            content=["prefers tea"],
            memory_type="procedural_memory",
        )

        self.assertEqual(
            memory.add_calls[0]["memory_type"],
            "procedural_memory",
        )
