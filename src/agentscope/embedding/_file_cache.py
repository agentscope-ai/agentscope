# -*- coding: utf-8 -*-
"""A file embedding cache implementation for storing and retrieving
embeddings in binary files."""
import hashlib
import json
import os
from typing import Any, List

import numpy as np

from ._cache_base import EmbeddingCacheBase
from .._logging import logger
from ..types import (
    Embedding,
    JSONSerializableObject,
)


class FileEmbeddingCache(EmbeddingCacheBase):
    """The embedding cache class that stores each embeddings vector in
    binary files."""

    def __init__(
        self,
        cache_dir: str = "./.cache/embeddings",
        max_file_number: int | None = None,
        max_cache_size: int | None = None,
    ) -> None:
        """Initialize the file embedding cache class.

        Args:
            cache_dir (`str`, defaults to `"./.cache/embeddings"`):
                The directory to store the embedding files.
            max_file_number (`int | None`, defaults to `None`):
                The maximum number of files to keep in the cache directory. If
                exceeded, the oldest files will be removed.
            max_cache_size (`int | None`, defaults to `None`):
                The maximum size of the cache directory in MB. If exceeded,
                the oldest files will be removed until the size is within the
                limit. A vector set that exceeds the limit on its own is not
                cached, so that storing it cannot empty the cache.
        """
        self._cache_dir = os.path.abspath(cache_dir)
        self.max_file_number = max_file_number
        self.max_cache_size = max_cache_size

    @property
    def cache_dir(self) -> str:
        """The cache directory where the embedding files are stored."""
        if not os.path.exists(self._cache_dir):
            os.makedirs(self._cache_dir, exist_ok=True)
        return self._cache_dir

    async def store(
        self,
        embeddings: List[Embedding],
        identifier: JSONSerializableObject,
        overwrite: bool = False,
        **kwargs: Any,
    ) -> None:
        """Store the embeddings with the given identifier.

        Args:
            embeddings (`List[Embedding]`):
                The embeddings to store.
            identifier (`JSONSerializableObject`):
                The identifier to distinguish the embeddings, which will be
                used to generate a hashable filename, so it should be
                JSON serializable (e.g. a string, number, list, dict).
            overwrite (`bool`, defaults to `False`):
                Whether to overwrite existing embeddings with the same
                identifier. If `True`, existing embeddings will be replaced.
        """
        filename = self._get_filename(identifier)
        path_file = os.path.join(self.cache_dir, filename)

        if os.path.exists(path_file):
            if not os.path.isfile(path_file):
                raise RuntimeError(
                    f"Path {path_file} exists but is not a file.",
                )

            if overwrite:
                np.save(path_file, embeddings)
                await self._maintain_cache_dir(path_file)
        else:
            np.save(path_file, embeddings)
            await self._maintain_cache_dir(path_file)

    async def retrieve(
        self,
        identifier: JSONSerializableObject,
    ) -> List[Embedding] | None:
        """Retrieve the embeddings with the given identifier. If not found,
        return `None`.

        Args:
            identifier (`JSONSerializableObject`):
                The identifier to retrieve the embeddings, which will be
                used to generate a hashable filename, so it should be
                JSON serializable (e.g. a string, number, list, dict).
        """
        filename = self._get_filename(identifier)
        path_file = os.path.join(self.cache_dir, filename)

        if os.path.exists(path_file):
            return np.load(os.path.join(self.cache_dir, filename)).tolist()
        return None

    async def remove(self, identifier: JSONSerializableObject) -> None:
        """Remove the embeddings with the given identifier.

        Args:
            identifier (`JSONSerializableObject`):
                The identifiers to remove the embeddings, which will be
                used to generate a hashable filename, so it should be
                JSON serializable (e.g. a string, number, list, dict).
        """
        filename = self._get_filename(identifier)
        path_file = os.path.join(self.cache_dir, filename)

        if os.path.exists(path_file):
            os.remove(path_file)
        else:
            raise FileNotFoundError(f"File {path_file} does not exist.")

    async def clear(self) -> None:
        """Clear the cache directory by removing all files."""
        for filename in os.listdir(self.cache_dir):
            if filename.endswith(".npy"):
                os.remove(os.path.join(self.cache_dir, filename))

    def _get_cache_size(self) -> float:
        """Get the current size of the cache directory in MB."""
        total_size = 0
        for filename in os.listdir(self.cache_dir):
            if filename.endswith(".npy"):
                path_file = os.path.join(self.cache_dir, filename)
                if os.path.isfile(path_file):
                    total_size += os.path.getsize(path_file)
        return total_size / (1024.0 * 1024.0)

    @staticmethod
    def _get_filename(identifier: JSONSerializableObject) -> str:
        """Generate a filename based on the identifier."""
        json_str = json.dumps(identifier, ensure_ascii=False)
        return hashlib.sha256(json_str.encode("utf-8")).hexdigest() + ".npy"

    async def _maintain_cache_dir(
        self,
        new_file: str | None = None,
    ) -> None:
        """Maintain the cache directory by removing old files if the number of
        files exceeds the maximum limit or if the cache size exceeds the
        maximum size.

        Args:
            new_file (`str | None`, defaults to `None`):
                The path of the file that :meth:`store` has just written.
                When that file alone is larger than ``max_cache_size`` it is
                dropped uncached and the rest of the cache is left untouched:
                evicting oldest-first towards such a limit would delete
                every other file and still not fit.
        """
        if new_file and self.max_cache_size is not None:
            new_size_mb = os.path.getsize(new_file) / (1024.0 * 1024.0)
            if new_size_mb > self.max_cache_size:
                os.remove(new_file)
                logger.warning(
                    "Do not cache %s: it holds %.2f MB, which exceeds the "
                    "%d MB cache limit on its own.",
                    os.path.basename(new_file),
                    new_size_mb,
                    self.max_cache_size,
                )
                return

        files = [
            (_.name, _.stat().st_mtime)
            for _ in os.scandir(self.cache_dir)
            if _.is_file() and _.name.endswith(".npy")
        ]
        files.sort(key=lambda x: x[1])

        if self.max_file_number and len(files) > self.max_file_number:
            for file_name, _ in files[: 0 - self.max_file_number]:
                os.remove(os.path.join(self.cache_dir, file_name))
                logger.info(
                    "Remove cached embedding file %s for limited number "
                    "of files (%d).",
                    file_name,
                    self.max_file_number,
                )
            files = files[0 - self.max_file_number :]

        if (
            self.max_cache_size is not None
            and self._get_cache_size() > self.max_cache_size
        ):
            removed_files = []
            for filename, _ in files:
                os.remove(os.path.join(self.cache_dir, filename))
                removed_files.append(filename)
                if self._get_cache_size() <= self.max_cache_size:
                    break

            if removed_files:
                logger.info(
                    "Remove %d cached embedding file(s) for limited "
                    "cache size (%d MB).",
                    len(removed_files),
                    self.max_cache_size,
                )
