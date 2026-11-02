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

    async def test_file_count_limits(self) -> None:
        """Only None is unlimited; zero and positive limits are enforced."""
        for limit in (None, 0, 1, 2):
            with self.subTest(limit=limit):
                with tempfile.TemporaryDirectory() as cache_dir:
                    cache = FileEmbeddingCache(
                        cache_dir=cache_dir,
                        max_file_number=limit,
                    )
                    unrelated = os.path.join(cache_dir, "notes.txt")
                    with open(unrelated, "w", encoding="utf-8") as file:
                        file.write("not a cache entry")
                    identifiers = ["first", "second", "third"]
                    for index, identifier in enumerate(identifiers):
                        await cache.store(_vectors(1), identifier)
                        path = os.path.join(
                            cache_dir,
                            cache._get_filename(identifier),
                        )
                        if os.path.exists(path):
                            os.utime(path, (1000 + index, 1000 + index))
                        count = len(
                            [
                                name
                                for name in os.listdir(cache_dir)
                                if name.endswith(".npy")
                            ],
                        )
                        self.assertEqual(
                            count,
                            index + 1
                            if limit is None
                            else min(index + 1, limit),
                        )
                    kept = [
                        await cache.retrieve(identifier) is not None
                        for identifier in identifiers
                    ]
                    self.assertEqual(
                        kept,
                        [True] * 3
                        if limit is None
                        else [False] * (3 - limit) + [True] * limit,
                    )
                    with open(unrelated, encoding="utf-8") as file:
                        self.assertEqual(file.read(), "not a cache entry")

    async def test_zero_limit_evicts_existing_entries_on_overwrite(
        self,
    ) -> None:
        """A zero limit also applies to a directory populated earlier."""
        with tempfile.TemporaryDirectory() as cache_dir:
            cache = FileEmbeddingCache(cache_dir=cache_dir)
            await cache.store(_vectors(1), "first")
            await cache.store(_vectors(1), "second")
            cache.max_file_number = 0

            await cache.store(_vectors(2), "second", overwrite=True)

            self.assertEqual(os.listdir(cache_dir), [])
            self.assertIsNone(await cache.retrieve("first"))
            self.assertIsNone(await cache.retrieve("second"))

    async def test_rejected_write_still_enforces_file_count(self) -> None:
        """Rejected writes must still enforce the independent count cap."""
        cases: list[tuple[int | None, list[str]]] = [
            (0, []),
            (1, ["second"]),
            (None, ["first", "second"]),
        ]
        for limit, retained in cases:
            with self.subTest(limit=limit):
                with tempfile.TemporaryDirectory() as cache_dir:
                    cache = FileEmbeddingCache(cache_dir=cache_dir)
                    for index, identifier in enumerate(("first", "second")):
                        await cache.store(_vectors(1), identifier)
                        path = os.path.join(
                            cache_dir,
                            cache._get_filename(identifier),
                        )
                        os.utime(path, (1000 + index, 1000 + index))
                    unrelated = os.path.join(cache_dir, "notes.txt")
                    with open(unrelated, "w", encoding="utf-8") as file:
                        file.write("keep me")

                    cache.max_file_number = limit
                    cache.max_cache_size = 0
                    await cache.store(_vectors(1), "rejected")

                    self.assertIsNone(await cache.retrieve("rejected"))
                    for identifier in ("first", "second"):
                        self.assertEqual(
                            await cache.retrieve(identifier),
                            _vectors(1) if identifier in retained else None,
                        )
                    self.assertEqual(
                        sorted(os.listdir(cache_dir)),
                        sorted(
                            [cache._get_filename(key) for key in retained]
                            + ["notes.txt"],
                        ),
                    )
                    with open(unrelated, encoding="utf-8") as file:
                        self.assertEqual(file.read(), "keep me")

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
