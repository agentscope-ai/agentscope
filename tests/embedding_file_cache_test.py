# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for :class:`FileEmbeddingCache` eviction limits."""
import os
import tempfile
from unittest import IsolatedAsyncioTestCase

from agentscope.embedding import FileEmbeddingCache

WIDTH = 1536


def _vectors(count: int) -> list:
    """Build an embeddings list of the given number of vectors.

    Args:
        count (`int`):
            The number of vectors to build.

    Returns:
        `list`:
            A list of float vectors, about ``count * 12`` KB on disk.
    """
    return [[float(row)] * WIDTH for row in range(count)]


class FileEmbeddingCacheEvictionTest(IsolatedAsyncioTestCase):
    """The size limit must never cost more entries than it stores."""

    async def test_oversized_entry_is_not_cached_and_keeps_the_cache(
        self,
    ) -> None:
        """An entry too large to ever fit is skipped, not cached by force."""
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = FileEmbeddingCache(cache_dir=cache_dir, max_cache_size=1)
            await cache.store(_vectors(10), "seed")

            await cache.store(_vectors(200), "oversized")

            self.assertEqual(
                os.listdir(cache_dir),
                [FileEmbeddingCache._get_filename("seed")],
            )
            self.assertEqual(await cache.retrieve("seed"), _vectors(10))
            self.assertIsNone(await cache.retrieve("oversized"))

    async def test_later_entries_are_still_cached_after_an_oversized_one(
        self,
    ) -> None:
        """A skipped oversized entry leaves the cache usable."""
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = FileEmbeddingCache(cache_dir=cache_dir, max_cache_size=1)
            await cache.store(_vectors(200), "oversized")
            await cache.store(_vectors(10), "after")

            self.assertEqual(await cache.retrieve("after"), _vectors(10))

    async def test_size_limit_still_evicts_the_oldest_entries(self) -> None:
        """Fitting a new entry keeps evicting oldest-first, newest intact."""
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = FileEmbeddingCache(cache_dir=cache_dir, max_cache_size=1)
            identifiers = [f"entry-{index}" for index in range(12)]
            for index, identifier in enumerate(identifiers):
                await cache.store(_vectors(10), identifier)
                # Pin the modification times so the eviction order does not
                # depend on how coarse the filesystem timestamp is.
                path = os.path.join(
                    cache_dir,
                    FileEmbeddingCache._get_filename(identifier),
                )
                os.utime(path, (1000 + index, 1000 + index))

            present = [
                await cache.retrieve(identifier) is not None
                for identifier in identifiers
            ]

            # Eviction only ever takes from the oldest end, so the surviving
            # entries form a suffix of the insertion order.
            removed = present.count(False)
            self.assertEqual(
                present,
                [False] * removed + [True] * (len(present) - removed),
            )
            self.assertGreater(removed, 0)
            self.assertTrue(present[-1])
            self.assertEqual(
                await cache.retrieve("entry-11"),
                _vectors(10),
            )
            self.assertLessEqual(cache._get_cache_size(), 1)
