# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Real skill copies must finish before cancellation releases their lock."""
import asyncio
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.workspace import LocalWorkspace


class TestSkillCopyCancellation(IsolatedAsyncioTestCase):
    """Control copy completion without replacing its filesystem behavior."""

    async def test_cancelled_copy_cannot_write_after_reset(self) -> None:
        """Keep the lock through copy and rollback despite repeated cancel."""
        for copy_fails in (False, True):
            with self.subTest(copy_fails=copy_fails):
                await self._check_cancelled_copy(copy_fails)

    async def _check_cancelled_copy(self, copy_fails: bool) -> None:
        """Run cancellation and reset against a gated executor thread."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            (source / "SKILL.md").write_text(
                "---\nname: copy-audit\ndescription: Test skill\n---\nBody\n",
                encoding="utf-8",
            )
            (source / "scripts").mkdir()
            (source / "scripts" / "run.py").write_text(
                "print('copied')\n",
                encoding="utf-8",
            )
            workspace = LocalWorkspace(workdir=str(root / "workspace"))
            await workspace.initialize()
            entered = threading.Event()
            release = threading.Event()
            finished = threading.Event()
            original = shutil.copytree
            destinations: list[Path] = []
            cleanup_entered = asyncio.Event()
            cleanup_release = asyncio.Event()
            original_delete = workspace._backend.delete_path

            async def gated_delete(path: str) -> None:
                """Expose cancellation while rollback owns the destination."""
                if destinations and Path(path) == destinations[0]:
                    cleanup_entered.set()
                    await cleanup_release.wait()
                await original_delete(path)

            def gated_copy(
                src: str,
                dst: str,
                *args: Any,
                **kwargs: Any,
            ) -> str:
                """Pause the real copy and optionally fail after it writes."""
                if Path(src).resolve() != source.resolve():
                    return original(src, dst, *args, **kwargs)
                destinations.append(Path(dst))
                entered.set()
                try:
                    if not release.wait(10):
                        raise TimeoutError("copy gate was not released")
                    result = original(src, dst, *args, **kwargs)
                    if copy_fails:
                        raise OSError("copy failed after writing files")
                    return result
                finally:
                    finished.set()

            reset = None
            with patch("shutil.copytree", gated_copy), patch.object(
                workspace._backend,
                "delete_path",
                side_effect=gated_delete,
            ):
                install = asyncio.create_task(workspace.add_skill(str(source)))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                    install.cancel()
                    await asyncio.sleep(0)
                    install.cancel()
                    await asyncio.sleep(0)
                    self.assertTrue(workspace._skill_lock.locked())
                    self.assertFalse(install.done())
                    reset = asyncio.create_task(workspace.reset())
                    await asyncio.sleep(0)
                    self.assertFalse(reset.done())
                    release.set()
                    await asyncio.wait_for(cleanup_entered.wait(), 5)
                    install.cancel()
                    await asyncio.sleep(0)
                    self.assertTrue(workspace._skill_lock.locked())
                    self.assertFalse(reset.done())
                    cleanup_release.set()
                    with self.assertRaises(asyncio.CancelledError):
                        await asyncio.wait_for(install, 5)
                    await asyncio.wait_for(reset, 5)
                    self.assertTrue(finished.is_set())
                    self.assertFalse(destinations[0].exists())
                    self.assertFalse(workspace._skill_lock.locked())
                finally:
                    release.set()
                    cleanup_release.set()
                    await asyncio.gather(install, return_exceptions=True)
                    if reset is not None:
                        await asyncio.gather(reset, return_exceptions=True)
                    self.assertTrue(await asyncio.to_thread(finished.wait, 5))
                    await workspace.close()
