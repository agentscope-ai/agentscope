# -*- coding: utf-8 -*-
"""The embedding cache tests in agentscope."""
import os
import shutil
import time
from unittest.async_case import IsolatedAsyncioTestCase

import numpy as np

from agentscope.embedding import FileEmbeddingCache


class EmbeddingCacheTest(IsolatedAsyncioTestCase):
    """The embedding cache tests in agentscope."""

    async def asyncSetUp(self) -> None:
        """Set up the test case."""
        self.embeddings = [[1, 2, 3, 4, 5, 6, 7, 8, 9, 10]]
        self.identifier1 = {
            "model": "text-embedding-v1",
            "text": ["This is a test text for embedding cache."],
        }
        self.identifier2 = {
            "model": "text-embedding-v2",
            "text": ["This is a test text for embedding cache."],
        }
        self.identifier3 = {
            "model": "text-embedding-v3",
            "text": ["This is a test text for embedding cache."],
        }
        self.identifier4 = {
            "model": "text-embedding-v4",
            "text": ["This is a test text for embedding cache."],
        }
        self.identifier5 = {
            "model": "text-embedding-v5",
            "text": ["This is a test text for embedding cache."],
        }
        self.ground_truth_filenames = [
            "56fe1fc64cb2830d0026559607e2ee5b9ae1d4524b256f83fdc2e2c8c23cefb7"
            ".npy",
            "fd9deb42e60e87c8358bc262c0ede4f0490385ffad1b85dddec3937ce6a24b03"
            ".npy",
            "394cb1f647fa493309abd4b97066649de9d5e5f2bbb03f43b11d56bf7d161497"
            ".npy",
            "ce97eac56670400eef1a7c8b7e366107af2129649b02479729bceefe2edd5727"
            ".npy",
            "675caa8a352219a9b74ebda4b1930e96d4c292f288f79ccb65e42293dd9de162"
            ".npy",
        ]

        self.large_embeddings = np.zeros((600, 600)).tolist()

        self.embedding_cache = FileEmbeddingCache(
            max_file_number=3,
            max_cache_size=2,
        )

    def _get_filenames(self, path_dir: str) -> list[str]:
        """Get the filenames in the cache directory."""
        filenames = [
            (_.name, _.stat().st_mtime)
            for _ in os.scandir(path_dir)
            if _.is_file() and _.name.endswith(".npy")
        ]
        filenames.sort(key=lambda x: x[1])

        return [_[0] for _ in filenames]

    async def test_embedding_cache(self) -> None:
        """Test the embedding cache."""

        await self.embedding_cache.store(self.embeddings, self.identifier1)
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            self.ground_truth_filenames[:1],
        )

        time.sleep(1)

        # when overwrite is False
        await self.embedding_cache.store([[1, 2]], self.identifier1)
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            self.ground_truth_filenames[:1],
        )
        cached_embedding = await self.embedding_cache.retrieve(
            self.identifier1,
        )
        self.assertListEqual(
            cached_embedding,
            self.embeddings,
        )

        time.sleep(1)

        # when overwrite is True
        await self.embedding_cache.store(
            [[1, 2]],
            self.identifier1,
            overwrite=True,
        )
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            self.ground_truth_filenames[:1],
        )
        cached_embedding = await self.embedding_cache.retrieve(
            self.identifier1,
        )
        self.assertListEqual(
            cached_embedding,
            [[1, 2]],
        )

        time.sleep(1)

        await self.embedding_cache.store(self.embeddings, self.identifier2)
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            self.ground_truth_filenames[:2],
        )

        time.sleep(1)

        await self.embedding_cache.store(self.embeddings, self.identifier3)
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            self.ground_truth_filenames[:3],
        )

        time.sleep(1)

        await self.embedding_cache.store(self.embeddings, self.identifier4)
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            self.ground_truth_filenames[1:4],
        )

        time.sleep(1)

        await self.embedding_cache.store(self.embeddings, self.identifier5)
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            self.ground_truth_filenames[2:5],
        )

        time.sleep(1)

        await self.embedding_cache.store(
            self.large_embeddings,
            self.identifier1,
            overwrite=True,
        )
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            [],
        )

        await self.embedding_cache.clear()
        self.assertListEqual(
            self._get_filenames(self.embedding_cache.cache_dir),
            [],
        )

    async def asyncTearDown(self) -> None:
        """Tear down the test case."""
        if os.path.exists(self.embedding_cache.cache_dir):
            shutil.rmtree(self.embedding_cache.cache_dir)
