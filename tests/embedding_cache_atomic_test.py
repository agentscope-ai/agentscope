# -*- coding: utf-8 -*-
"""Failed cache writes must not publish partial embeddings."""
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

import numpy as np

from agentscope.embedding import FileEmbeddingCache


class FileEmbeddingCacheAtomicTest(IsolatedAsyncioTestCase):
    """Exercise publication failures and readers during replacement."""

    async def test_serialization_failure_preserves_cache(self) -> None:
        """Invalid vectors must leave either the old entry or a cache miss."""
        for existing in (False, True):
            with self.subTest(existing=existing):
                with tempfile.TemporaryDirectory() as directory:
                    cache = FileEmbeddingCache(directory)
                    old = [[1.0, 2.0]]
                    if existing:
                        await cache.store(old, "key")
                    before = sorted(os.listdir(directory))
                    with self.assertRaises(ValueError):
                        await cache.store(
                            [[3.0], [4.0, 5.0]],
                            "key",
                            overwrite=True,
                        )
                    self.assertEqual(
                        await cache.retrieve("key"), old if existing else None
                    )
                    self.assertEqual(sorted(os.listdir(directory)), before)

    async def test_partial_write_failure_preserves_cache(self) -> None:
        """A disk error after partial output cannot truncate an old entry."""

        def fail_save(file: Any, _vectors: Any) -> None:
            """Simulate a failed write after emitting a partial NPY header."""
            if hasattr(file, "write"):
                file.write(b"partial")
            else:
                Path(file).write_bytes(b"partial")
            raise OSError("simulated disk write failure")

        for existing in (False, True):
            with self.subTest(existing=existing):
                with tempfile.TemporaryDirectory() as directory:
                    cache = FileEmbeddingCache(directory)
                    old = [[1.0, 2.0]]
                    if existing:
                        await cache.store(old, "key")
                    before = sorted(os.listdir(directory))
                    with patch(
                        "agentscope.embedding._file_cache.np.save",
                        side_effect=fail_save,
                    ):
                        with self.assertRaisesRegex(OSError, "simulated disk"):
                            await cache.store(
                                [[3.0, 4.0]], "key", overwrite=True
                            )
                    self.assertEqual(
                        await cache.retrieve("key"), old if existing else None
                    )
                    self.assertEqual(sorted(os.listdir(directory)), before)

    async def test_failed_publication_cleans_temporary_file(self) -> None:
        """A rename failure preserves the old entry and leaves no temp file."""
        with tempfile.TemporaryDirectory() as directory:
            cache = FileEmbeddingCache(directory)
            await cache.store([[1.0]], "key")
            before = sorted(os.listdir(directory))
            with patch(
                "agentscope.embedding._file_cache.os.replace",
                side_effect=PermissionError("publication denied"),
            ):
                with self.assertRaisesRegex(
                    PermissionError, "publication denied"
                ):
                    await cache.store([[2.0]], "key", overwrite=True)
            self.assertEqual(await cache.retrieve("key"), [[1.0]])
            self.assertEqual(sorted(os.listdir(directory)), before)

    async def test_reader_sees_old_entry_until_replacement_is_complete(
        self,
    ) -> None:
        """Observe the published path while replacement bytes are written."""
        with tempfile.TemporaryDirectory() as directory:
            cache = FileEmbeddingCache(directory)
            await cache.store([[1.0, 2.0]], "key")
            original = next(Path(directory).glob("*.npy"))
            save = np.save

            def observe_save(file: Any, vectors: Any) -> None:
                """Read the current entry on both sides of serialization."""
                self.assertEqual(np.load(original).tolist(), [[1.0, 2.0]])
                save(file, vectors)
                self.assertEqual(np.load(original).tolist(), [[1.0, 2.0]])

            with patch(
                "agentscope.embedding._file_cache.np.save",
                side_effect=observe_save,
            ):
                await cache.store([[3.0, 4.0]], "key", overwrite=True)
            self.assertEqual(await cache.retrieve("key"), [[3.0, 4.0]])
            self.assertEqual(os.listdir(directory), [original.name])

    async def test_no_overwrite_does_not_serialize(self) -> None:
        """An existing entry still wins when overwrite is disabled."""
        with tempfile.TemporaryDirectory() as directory:
            cache = FileEmbeddingCache(directory)
            await cache.store([[1.0]], "key")
            with patch("agentscope.embedding._file_cache.np.save") as save:
                await cache.store([[2.0]], "key")
                save.assert_not_called()
            self.assertEqual(await cache.retrieve("key"), [[1.0]])
