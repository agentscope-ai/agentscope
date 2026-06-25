# -*- coding: utf-8 -*-
"""Local filesystem implementation of :class:`BlobStoreBase`.

The backend reserves a single root directory and treats keys as
relative paths beneath it.  URIs are formatted as ``local://{key}`` —
the scheme acts as a discriminator so a mixed-deployment app can route
``local://`` and ``s3://`` URIs to different backends without
re-parsing the rest of the string.
"""
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import IO, AsyncIterator

import aiofiles
import aiofiles.os
import aiofiles.ospath

from ._base import AsyncReadable, BlobStoreBase


_SCHEME = "local://"
_CHUNK_SIZE = 1 << 20  # 1 MiB — keep peak memory bounded, match S3 part size.


class LocalBlobStore(BlobStoreBase):
    """Store blobs as files beneath a configurable root directory.

    Intended for single-node deployments and development.  The lifecycle
    of a blob is bound to the document that owns it, not to the request
    that uploaded it — this is what distinguishes the store from
    FastAPI's per-request ``SpooledTemporaryFile`` and is the reason
    the abstraction exists at all.
    """

    def __init__(self, root_dir: str | os.PathLike) -> None:
        """Initialize the local backend.

        Args:
            root_dir (`str | PathLike`):
                Directory that holds all blobs for this app.  Created
                on :meth:`__aenter__` if it does not exist.
        """
        self._root = Path(root_dir).resolve()

    async def __aenter__(self) -> "LocalBlobStore":
        """Ensure the root directory exists."""
        await aiofiles.os.makedirs(self._root, exist_ok=True)
        return self

    def _path_for(self, key: str) -> Path:
        """Resolve a backend-relative key to an absolute filesystem path.

        Rejects keys that try to escape :attr:`_root` via ``..`` or
        absolute paths.  Callers pick keys server-side so this is
        defensive rather than a primary trust boundary, but we still
        refuse to write outside the root.

        Args:
            key (`str`):
                Backend-relative key.

        Returns:
            `Path`:
                The absolute filesystem path within :attr:`_root`.
        """
        if not key or key.startswith("/") or ".." in Path(key).parts:
            raise ValueError(f"Invalid blob key: {key!r}")
        path = (self._root / key).resolve()
        if self._root not in path.parents and path != self._root:
            raise ValueError(f"Blob key {key!r} escapes the root directory.")
        return path

    def _key_from_uri(self, uri: str) -> str:
        """Extract the backend-relative key from a ``local://`` URI."""
        if not uri.startswith(_SCHEME):
            raise ValueError(f"Not a local blob URI: {uri!r}")
        return uri[len(_SCHEME) :]

    async def write_stream(self, key: str, stream: IO[bytes]) -> str:
        """Copy ``stream`` into the blob at ``key`` in 1 MiB chunks.

        Creates intermediate directories as needed.  Existing blobs at
        the same key are overwritten — keys are generated server-side
        from document ids, so collisions only happen on intentional
        re-uploads.

        Args:
            key (`str`):
                Backend-relative key.
            stream (`IO[bytes]`):
                Synchronous binary source.

        Returns:
            `str`:
                The ``local://{key}`` URI.
        """
        path = self._path_for(key)
        await aiofiles.os.makedirs(path.parent, exist_ok=True)
        async with aiofiles.open(path, "wb") as out:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                await out.write(chunk)
        return f"{_SCHEME}{key}"

    @asynccontextmanager
    async def open(  # type: ignore[override]
        self,
        uri: str,
    ) -> AsyncIterator[AsyncReadable]:
        """Open the blob at ``uri`` for streaming reads."""
        path = self._path_for(self._key_from_uri(uri))
        async with aiofiles.open(path, "rb") as fp:
            yield fp

    async def delete(self, uri: str) -> None:
        """Remove the blob at ``uri`` if present (idempotent)."""
        path = self._path_for(self._key_from_uri(uri))
        try:
            await aiofiles.os.remove(path)
        except FileNotFoundError:
            return
        # Best-effort cleanup of empty parent directories up to the root.
        parent = path.parent
        while parent != self._root and parent.is_relative_to(self._root):
            try:
                await aiofiles.os.rmdir(parent)
            except OSError:
                break
            parent = parent.parent

    async def exists(self, uri: str) -> bool:
        """Return whether the blob at ``uri`` is present."""
        path = self._path_for(self._key_from_uri(uri))
        return await aiofiles.ospath.isfile(path)

    # Synchronous helper used by tests / cleanup scripts; not part of
    # the public abstraction.
    def _wipe(self) -> None:
        """Delete the entire root directory.  Test-only."""
        if self._root.exists():
            shutil.rmtree(self._root)
