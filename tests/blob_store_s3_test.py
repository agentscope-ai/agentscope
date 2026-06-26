# -*- coding: utf-8 -*-
"""Tests for :class:`S3BlobStore`.

Uses ``moto.server.ThreadedMotoServer``: a real HTTP S3 endpoint
served in-process on a thread. The aioboto3 client talks to it over
TCP the way it would talk to AWS / MinIO / R2 — so the test
exercises the real wire path (and the ``endpoint_url`` plumbing the
production deployment will rely on for non-AWS services).

We deliberately avoid ``moto.mock_aws()`` because that decorator
patches ``botocore``'s endpoint layer, which does not play nicely
with ``aiobotocore``: the patched response object is sync, but
aiobotocore awaits ``http_response.content`` and gets a TypeError.
The threaded server bypasses every monkey-patch path and gives us
end-to-end coverage.

Properties under test:

- A blob written through ``write_stream`` reads back identical
  bytes via ``open``.
- The URI emitted is ``s3://{bucket}/{key}`` and round-trips
  through ``exists`` / ``delete``.
- ``exists`` distinguishes present from missing without raising.
- ``delete`` is idempotent.
- Bad URIs raise ``ValueError`` before any network call.
- A store used outside ``async with`` raises ``RuntimeError`` —
  callers MUST manage lifecycle via :class:`AsyncExitStack`.
"""
import io
import os
import socket
from typing import TYPE_CHECKING
from unittest import IsolatedAsyncioTestCase

import boto3
from moto.server import ThreadedMotoServer

if TYPE_CHECKING:
    from agentscope.app.rag.blob_store import S3BlobStore


_BUCKET = "test-blobs"
_REGION = "us-east-1"


def _pick_port() -> int:
    """Find an unused TCP port to bind moto on."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class S3BlobStoreTest(IsolatedAsyncioTestCase):
    """Round-trip + URI parsing + streaming behaviour."""

    def setUp(self) -> None:
        # ThreadedMotoServer needs explicit credentials — without
        # them aiobotocore looks for the AWS config chain and may
        # fail on CI runners without credentials baked in.
        self._env_overrides = {
            "AWS_ACCESS_KEY_ID": "testing",
            "AWS_SECRET_ACCESS_KEY": "testing",
            "AWS_SECURITY_TOKEN": "testing",
            "AWS_SESSION_TOKEN": "testing",
            "AWS_DEFAULT_REGION": _REGION,
        }
        self._saved_env = {k: os.environ.get(k) for k in self._env_overrides}
        for k, v in self._env_overrides.items():
            os.environ[k] = v

        port = _pick_port()
        self._endpoint_url = f"http://127.0.0.1:{port}"
        self._server = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
        self._server.start()

        client = boto3.client(
            "s3",
            region_name=_REGION,
            endpoint_url=self._endpoint_url,
        )
        client.create_bucket(Bucket=_BUCKET)

    def tearDown(self) -> None:
        self._server.stop()
        for k, prev in self._saved_env.items():
            if prev is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = prev

    def _store(self) -> "S3BlobStore":
        """Build a fresh :class:`S3BlobStore` pointing at the moto endpoint.

        Returns:
            `S3BlobStore`:
                The store under test, configured for the moto-backed
                ``_BUCKET`` in ``_REGION``.
        """
        # Import inside the method so the skip decorator can fire
        # without the import path running.
        from agentscope.app.rag.blob_store import S3BlobStore

        return S3BlobStore(
            bucket=_BUCKET,
            region_name=_REGION,
            endpoint_url=self._endpoint_url,
            use_ssl=False,
        )

    async def test_write_open_round_trip(self) -> None:
        """A blob written through ``write_stream`` reads back the
        same bytes via ``open`` and the URI is the documented
        ``s3://{bucket}/{key}`` shape."""
        async with self._store() as store:
            payload = b"hello world\n" * 100  # ~1.2 KiB
            uri = await store.write_stream(
                "kb/abc/doc-1",
                io.BytesIO(payload),
            )
            self.assertEqual(uri, "s3://test-blobs/kb/abc/doc-1")

            received: list[bytes] = []
            async with store.open(uri) as fp:
                while True:
                    chunk = await fp.read(64)
                    if not chunk:
                        break
                    received.append(chunk)
            self.assertEqual(b"".join(received), payload)

    async def test_exists_and_delete(self) -> None:
        """``exists`` flips from ``True`` to ``False`` after
        ``delete``; ``delete`` is idempotent on a missing key."""
        async with self._store() as store:
            uri = await store.write_stream(
                "kb/abc/doc-2",
                io.BytesIO(b"data"),
            )
            self.assertTrue(await store.exists(uri))
            await store.delete(uri)
            self.assertFalse(await store.exists(uri))
            # Second delete is a no-op.
            await store.delete(uri)

    async def test_bad_uri_scheme_rejected(self) -> None:
        """Non-``s3://`` URIs are refused before any network call."""
        async with self._store() as store:
            with self.assertRaises(ValueError):
                await store.exists("local://kb/abc/doc-3")
            with self.assertRaises(ValueError):
                await store.delete("s3://wrong-bucket/kb/abc/doc-4")

    async def test_methods_require_aenter(self) -> None:
        """Calling a blob method without entering the context fails
        loudly — production code MUST manage the lifecycle through
        ``async with`` / ``AsyncExitStack``."""
        store = self._store()  # not entered
        with self.assertRaises(RuntimeError):
            await store.write_stream("kb/abc/doc-5", io.BytesIO(b""))
