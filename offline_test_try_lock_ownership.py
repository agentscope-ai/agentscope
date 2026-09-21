"""Offline tests for MessageBus try_lock ownership (#2728).

Imports the in-memory bus module directly to avoid app package deps
(apscheduler etc.) that are unrelated to the lock path.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"


def _load_bus_modules():
    # Minimal package stubs so relative imports resolve without the full app.
    for name in [
        "agentscope",
        "agentscope.app",
        "agentscope.app.message_bus",
    ]:
        if name not in sys.modules:
            pkg = types.ModuleType(name)
            pkg.__path__ = []  # type: ignore[attr-defined]
            sys.modules[name] = pkg

    def load(mod_name: str, rel: str):
        path = SRC / rel
        spec = importlib.util.spec_from_file_location(mod_name, path)
        assert spec and spec.loader
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
        return mod

    # Dependency order for relative imports inside _base.py
    load("agentscope.app.message_bus._keys", "agentscope/app/message_bus/_keys.py")
    base = load("agentscope.app.message_bus._base", "agentscope/app/message_bus/_base.py")
    mem = load(
        "agentscope.app.message_bus._in_memory_message_bus",
        "agentscope/app/message_bus/_in_memory_message_bus.py",
    )
    return base, mem


_base, mem = _load_bus_modules()
InMemoryMessageBus = mem.InMemoryMessageBus


class TestTryLockOwnership(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.bus = InMemoryMessageBus()
        await self.bus.__aenter__()

    async def asyncTearDown(self) -> None:
        await self.bus.aclose()

    async def test_exclusive_until_unlocked(self) -> None:
        self.assertTrue(await self.bus.try_lock("k", ttl_secs=10))
        self.assertFalse(await self.bus.try_lock("k", ttl_secs=10))
        self.assertTrue(await self.bus.is_locked("k"))
        await self.bus.unlock("k")
        self.assertFalse(await self.bus.is_locked("k"))
        self.assertTrue(await self.bus.try_lock("k", ttl_secs=10))

    async def test_stale_unlock_does_not_release_successor(self) -> None:
        with patch.object(mem.time, "monotonic") as monotonic:
            monotonic.return_value = 1_000.0
            self.assertTrue(await self.bus.try_lock("k", ttl_secs=10))
            token_a = (mem._try_lock_tokens.get() or {})["k"]

            monotonic.return_value = 1_010.0
            self.assertFalse(await self.bus.is_locked("k"))
            self.assertTrue(await self.bus.try_lock("k", ttl_secs=600))
            token_b = (mem._try_lock_tokens.get() or {})["k"]
            self.assertNotEqual(token_a, token_b)

            mem._try_lock_tokens.set({"k": token_a})
            await self.bus.unlock("k")
            self.assertTrue(await self.bus.is_locked("k"))
            self.assertFalse(await self.bus.try_lock("k", ttl_secs=600))

            await self.bus.unlock("k", token=token_b)
            self.assertFalse(await self.bus.is_locked("k"))
            self.assertTrue(await self.bus.try_lock("k", ttl_secs=600))

    async def test_unlock_without_token_keeps_foreign_lease(self) -> None:
        self.assertTrue(await self.bus.try_lock("k", ttl_secs=600))
        mem._try_lock_tokens.set({})
        await self.bus.unlock("k")
        self.assertTrue(await self.bus.is_locked("k"))

    async def test_lease_expiry_allows_reacquire(self) -> None:
        with patch.object(mem.time, "monotonic") as monotonic:
            monotonic.return_value = 1_000.0
            self.assertTrue(await self.bus.try_lock("k", ttl_secs=600))
            self.assertFalse(await self.bus.try_lock("k", ttl_secs=600))
            monotonic.return_value = 1_000.0 + 600
            self.assertFalse(await self.bus.is_locked("k"))
            self.assertTrue(await self.bus.try_lock("k", ttl_secs=600))

    async def test_explicit_token_unlock_successor(self) -> None:
        self.assertTrue(await self.bus.try_lock("k", ttl_secs=10))
        token = (mem._try_lock_tokens.get() or {})["k"]
        await self.bus.unlock("k", token=token)
        self.assertFalse(await self.bus.is_locked("k"))


class TestRedisUnlockScriptShape(unittest.TestCase):
    def test_compare_and_delete_lua_present(self) -> None:
        redis_mod_path = SRC / "agentscope/app/message_bus/_redis_message_bus.py"
        src = redis_mod_path.read_text(encoding="utf-8")
        self.assertIn("_TRY_LOCK_UNLOCK_LUA", src)
        self.assertIn("GET", src)
        self.assertIn("DEL", src)
        self.assertIn("secrets.token_hex", src)
        self.assertIn("nx=True", src)
        # unlock path must use compare-and-delete Lua, not raw DEL
        unlock_idx = src.find("async def unlock")
        self.assertGreater(unlock_idx, 0)
        unlock_src = src[unlock_idx:]
        self.assertIn("_TRY_LOCK_UNLOCK_LUA", unlock_src)
        self.assertNotIn("await self._client.delete(key)", unlock_src.split("async def ")[0])

    def test_base_unlock_signature_documents_ownership(self) -> None:
        base_path = SRC / "agentscope/app/message_bus/_base.py"
        src = base_path.read_text(encoding="utf-8")
        self.assertIn("token: str | None = None", src)
        self.assertIn("opaque ownership token", src)


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result.wasSuccessful():
        print(f"offline_try_lock_ownership: {result.testsRun} passed")
        sys.exit(0)
    sys.exit(1)
