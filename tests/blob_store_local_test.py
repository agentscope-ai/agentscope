# -*- coding: utf-8 -*-
"""Regression tests for publishing complete local blobs."""
import asyncio
import io
import shutil
import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from agentscope.app.rag.blob_store import LocalBlobStore


class _InterruptedStream(io.BytesIO):
    """Return a chunk, then fail or cancel the task reading the source."""

    def __init__(self, cancel: bool = False) -> None:
        """Choose whether the second read fails or cancels the writer."""
        super().__init__(b"partial replacement")
        self._reads = 0
        self._cancel = cancel

    def read(self, size: int | None = -1) -> bytes:
        """Interrupt after the first chunk has been written."""
        self._reads += 1
        if self._reads > 1:
            if self._cancel:
                asyncio.current_task().cancel()
                return b"another chunk"
            raise OSError("upload source failed")
        return super().read(size)


class _ObservedStream(io.BytesIO):
    """Observe the destination while enforcing bounded source reads."""

    def __init__(
        self,
        payload: bytes,
        destination: Path,
    ) -> None:
        """Record the destination before each source read."""
        super().__init__(payload)
        self._destination = destination
        self.observed: list[bytes | None] = []

    def read(self, size: int | None = -1) -> bytes:
        """Reject unbounded reads and observe the destination."""
        if size is None or size <= 0:
            raise AssertionError("The source must be copied in sized chunks")
        self.observed.append(
            self._destination.read_bytes()
            if self._destination.is_file()
            else None,
        )
        return super().read(size)


class LocalBlobStoreTest(IsolatedAsyncioTestCase):
    """Test successful publication and preservation on interrupted writes."""

    def setUp(self) -> None:
        """Create a real filesystem store in a temporary directory."""
        self._root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self._root)
        self._store = LocalBlobStore(self._root)
        self._key = "kb/doc"
        self._uri = f"local://{self._key}"

    async def _snapshot(self) -> dict:
        """Read the complete externally visible blob and remaining files."""
        exists = await self._store.exists(self._uri)
        content = None
        if exists:
            async with self._store.open(self._uri) as reader:
                content = await reader.read()
        return {
            "exists": exists,
            "content": content,
            "size": await self._store.size(self._uri),
            "files": sorted(
                path.relative_to(self._root).as_posix()
                for path in self._root.rglob("*")
                if path.is_file()
            ),
        }

    def _expected(self, content: bytes | None) -> dict:
        """Describe the full state of a present or absent blob."""
        return {
            "exists": content is not None,
            "content": content,
            "size": len(content) if content is not None else None,
            "files": [self._key] if content is not None else [],
        }

    async def test_destination_changes_only_after_complete_copy(self) -> None:
        """Readers see the original or absent blob until publication."""
        for original in (None, b"original complete document"):
            with self.subTest(original=original):
                if original is not None:
                    await self._store.write_stream(
                        self._key,
                        io.BytesIO(original),
                    )
                before = await self._snapshot()
                payload = b"complete replacement\n" * 100000
                source = _ObservedStream(payload, self._root / self._key)
                uri = await self._store.write_stream(self._key, source)
                self.assertEqual(
                    {
                        "uri": uri,
                        "observed": source.observed,
                        "after": await self._snapshot(),
                        "source_closed": source.closed,
                    },
                    {
                        "uri": self._uri,
                        "observed": [before["content"]] * len(source.observed),
                        "after": self._expected(payload),
                        "source_closed": False,
                    },
                )
                self.assertGreater(len(source.observed), 2)
                await self._store.delete(self._uri)

    async def test_source_failure_preserves_destination(self) -> None:
        """Failed new writes stay absent and failed overwrites keep bytes."""
        for original in (None, b"original complete document"):
            with self.subTest(original=original):
                if original is not None:
                    await self._store.write_stream(
                        self._key,
                        io.BytesIO(original),
                    )
                with self.assertRaisesRegex(OSError, "upload source failed"):
                    await self._store.write_stream(
                        self._key,
                        _InterruptedStream(),
                    )
                self.assertEqual(
                    await self._snapshot(),
                    self._expected(original),
                )

    async def test_cancellation_preserves_destination(self) -> None:
        """Awaiting a cancelled writer leaves no published partial bytes."""
        for original in (None, b"original complete document"):
            with self.subTest(original=original):
                if original is not None:
                    await self._store.write_stream(
                        self._key,
                        io.BytesIO(original),
                    )
                writer = asyncio.create_task(
                    self._store.write_stream(
                        self._key,
                        _InterruptedStream(cancel=True),
                    ),
                )
                with self.assertRaises(asyncio.CancelledError):
                    await writer
                self.assertEqual(
                    await self._snapshot(),
                    self._expected(original),
                )

    async def test_publish_failure_preserves_destination(self) -> None:
        """A failed replacement removes staging files and keeps old bytes."""
        for original in (None, b"original complete document"):
            with self.subTest(original=original):
                if original is not None:
                    await self._store.write_stream(
                        self._key,
                        io.BytesIO(original),
                    )
                with patch(
                    "agentscope.app.rag.blob_store._local.os.replace",
                    side_effect=OSError("publication failed"),
                ):
                    with self.assertRaisesRegex(OSError, "publication failed"):
                        await self._store.write_stream(
                            self._key,
                            io.BytesIO(b"complete replacement"),
                        )
                self.assertEqual(
                    await self._snapshot(),
                    self._expected(original),
                )

    async def test_empty_overwrite_publishes_empty_blob(self) -> None:
        """A successful empty upload intentionally replaces existing bytes."""
        await self._store.write_stream(self._key, io.BytesIO(b"original"))
        self.assertEqual(
            await self._store.write_stream(self._key, io.BytesIO(b"")),
            self._uri,
        )
        self.assertEqual(await self._snapshot(), self._expected(b""))
