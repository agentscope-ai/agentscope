# -*- coding: utf-8 -*-
"""Abstract base class for blob storage backends.

A :class:`BlobStoreBase` is the byte-level home of files uploaded into
the application — knowledge base documents in v1, potentially other
binary payloads later.  It is created once at app startup and shared
across requests, mirroring the lifecycle of
:class:`~agentscope.app.storage.StorageBase` and
:class:`~agentscope.rag.VectorStoreBase`.

The abstraction owes its existence to a single hard requirement: bytes
must never sit in memory for the duration of indexing.  The upload
endpoint streams the request body into ``write_stream``; the indexing
worker streams the bytes back out via ``open``.  Implementations decide
where those bytes physically live (local disk, S3-compatible object
store, etc.) — neither caller cares.
"""
from abc import ABC, abstractmethod
from contextlib import AbstractAsyncContextManager
from typing import IO, Any, Protocol, Self


class AsyncReadable(Protocol):
    """Minimal async-read protocol returned by :meth:`BlobStoreBase.open`.

    Backed by ``aiofiles`` handles for the local store and by streaming
    response bodies for object-storage backends.  The narrow interface
    keeps the worker's blob-reading code identical across backends
    even though no single concrete type implements both shapes.
    """

    async def read(self, n: int = -1) -> bytes:
        """Read up to ``n`` bytes (``-1`` reads to EOF).

        Args:
            n (`int`, defaults to ``-1``):
                The maximum number of bytes to read; ``-1`` drains the
                stream.

        Returns:
            `bytes`:
                The bytes read; an empty ``bytes`` at EOF.
        """


class BlobStoreBase(ABC):
    """Abstract base class for blob storage backends.

    Lifecycle is managed via the async context manager protocol so the
    app lifespan can open / close connection pools or filesystem
    handles uniformly.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> Self:
        """Enter the async context — open connections / create dirs.

        The default implementation is a no-op.  Subclasses that need
        explicit setup should override this.

        Returns:
            `BlobStoreBase`:
                ``self``.
        """
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        """Exit the async context — close connections if needed."""

    # ------------------------------------------------------------------
    # Byte operations
    # ------------------------------------------------------------------

    @abstractmethod
    async def write_stream(self, key: str, stream: IO[bytes]) -> str:
        """Stream-write a blob and return its URI.

        Implementations MUST copy the bytes in chunks (typically ~1 MB)
        and MUST NOT call ``stream.read()`` without a size argument —
        the whole point of the abstraction is that the byte payload
        never lives in memory all at once.

        Args:
            key (`str`):
                Backend-relative key, e.g. ``"kb/{kb_id}/{doc_id}"``.
                The caller picks the key layout; the backend turns it
                into a backend-native location (filesystem path,
                ``s3://`` object name, ...).
            stream (`IO[bytes]`):
                A readable binary stream.  Synchronous read API
                (``read(n)``) — ``UploadFile.file`` from FastAPI fits
                directly, and other binary streams are wrapped via the
                same protocol.

        Returns:
            `str`:
                A scheme-qualified URI (e.g. ``"local://kb/.../uuid"``,
                ``"s3://bucket/kb/.../uuid"``) that round-trips back
                through :meth:`open` / :meth:`delete` / :meth:`exists`.
        """

    @abstractmethod
    async def open(
        self,
        uri: str,
    ) -> AbstractAsyncContextManager[AsyncReadable]:
        """Stream-read a blob by URI.

        Returns an async context manager whose ``__aenter__`` yields a
        stream with an awaitable ``read(n)`` method.  Implementations
        decide whether the stream is backed by an ``aiofiles`` handle,
        a streaming response body, or any other async source —
        callers MUST treat it as forward-only and chunked.

        Args:
            uri (`str`):
                A URI produced by :meth:`write_stream`.

        Returns:
            `AbstractAsyncContextManager[AsyncReadable]`:
                The byte stream, wrapped so the backend can release
                handles deterministically on context exit.
        """

    @abstractmethod
    async def delete(self, uri: str) -> None:
        """Delete the blob at ``uri`` if it exists.

        MUST be idempotent — a no-op when the blob is already gone.
        The caller (document deletion path) treats blob deletion as
        best-effort cleanup; partial failures are recoverable by
        re-running the sweep.

        Args:
            uri (`str`):
                A URI produced by :meth:`write_stream`.
        """

    @abstractmethod
    async def exists(self, uri: str) -> bool:
        """Return whether the blob at ``uri`` is present.

        Args:
            uri (`str`):
                A URI produced by :meth:`write_stream`.

        Returns:
            `bool`:
                ``True`` if the blob is currently retrievable.
        """
