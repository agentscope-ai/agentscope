# -*- coding: utf-8 -*-
"""S3-compatible implementation of :class:`BlobStoreBase`.

Works against any service that implements the S3 wire protocol —
AWS S3, MinIO, Cloudflare R2, Aliyun OSS (S3-compatible), Tencent yun COS, etc.
The discriminator is ``endpoint_url``: ``None`` means real AWS S3
(``aioboto3`` resolves the regional endpoint); any other value points
at the compatible service's S3 endpoint.

URIs are formatted as ``s3://{bucket}/{key}``. The bucket is baked
into the URI (rather than carried out-of-band) so a single deployment
can read blobs that were written under a different bucket — for
example, after a bucket migration — without breaking existing
document records.

The implementation is intentionally minimal: this is a byte store,
not a multipart upload optimiser. Production-scale uploads (>5 GiB)
would need the multipart API explicitly, but our documents are
bounded by ``UPLOAD_MAX_BYTES`` well below that ceiling, so a single
``upload_fileobj`` call (which uses multipart internally when the
body is large enough) is sufficient.
"""
from contextlib import asynccontextmanager
from typing import IO, Any, AsyncIterator

from ._base import AsyncReadable, BlobStoreBase


_SCHEME = "s3://"


class _StreamingBody:
    """Adapter that exposes :class:`AsyncReadable` over an aioboto3 body.

    ``aioboto3`` returns ``aiohttp``-backed streaming bodies that
    already expose ``read(n)`` — but ``n=-1`` is "read until EOF"
    which our ``IndexWorker._read_blob`` does not want; it asks for
    a bounded chunk every iteration. We wrap the body in a thin
    object so the adapter contract is explicit and so the underlying
    body type can change without the worker noticing.
    """

    def __init__(self, body: Any) -> None:
        """Initialize the body."""
        self._body = body

    async def read(self, n: int = -1) -> bytes:
        """Read up to *n* bytes; ``-1`` reads to EOF."""
        if n == -1:
            return await self._body.read()
        # aiohttp's StreamReader.read(n) returns up to n bytes; if
        # the server-side stream is bursty, we may get a short read,
        # which is exactly the contract we want for chunked indexing.
        return await self._body.read(n)


class S3BlobStore(BlobStoreBase):
    """Store blobs in an S3-compatible bucket."""

    def __init__(
        self,
        bucket: str,
        *,
        region_name: str | None = None,
        endpoint_url: str | None = None,
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        session_token: str | None = None,
        use_ssl: bool = True,
        config: Any = None,
    ) -> None:
        """Initialize an S3-compatible bucket.

        Args:
            bucket (`str`):
                Bucket name. The bucket MUST exist; this store will not
                create it. Production deployments commonly provision the
                bucket out-of-band via Terraform / CloudFormation so the
                app's IAM role does not need ``s3:CreateBucket``.
            region_name (`str | None`, optional):
                AWS region for AWS S3. Required for AWS, ignored for
                MinIO / R2 / other services that locate the bucket via
                ``endpoint_url``.
            endpoint_url (`str | None`, optional):
                Full URL of the S3-compatible service. ``None`` selects
                real AWS S3. Examples: ``http://minio:9000``,
                ``https://<account>.r2.cloudflarestorage.com``,
                ``https://oss-cn-hangzhou.aliyuncs.com``.
            aws_access_key_id (`str | None`, optional):
                Static credential. Prefer leaving this ``None`` and letting
                the IAM role / environment chain resolve credentials when
                running on AWS infrastructure.
            aws_secret_access_key (`str | None`, optional):
                Paired with ``aws_access_key_id``.
            session_token (`str | None`, optional):
                For STS-issued temporary credentials.
            use_ssl (`bool`, defaults to ``True``):
                Force HTTPS. Production deployments must keep this on;
                local MinIO with self-signed certs is the only place
                ``False`` is reasonable.
            config (`Any | None`, optional):
                ``aiobotocore.config.AioConfig`` instance for users who
                need to tune timeouts, retry mode, signature version
                (e.g. ``s3v4`` for Aliyun OSS), or addressing style.
                Path-style addressing is needed for MinIO; pass
                ``AioConfig(s3={"addressing_style": "path"})``.
        """
        try:
            import aioboto3
        except ImportError as e:
            raise ImportError(
                "S3BlobStore requires the optional dependency ``aioboto3``. "
                "Install it with ``uv pip install aioboto3`` or with the "
                "``[s3]`` extra.",
            ) from e

        self._bucket = bucket
        self._region_name = region_name
        self._endpoint_url = endpoint_url
        self._aws_access_key_id = aws_access_key_id
        self._aws_secret_access_key = aws_secret_access_key
        self._session_token = session_token
        self._use_ssl = use_ssl
        self._config = config

        # Session is cheap; the actual transport is the ``client``
        # context manager opened per call. We deliberately do NOT
        # cache a long-lived client across the lifespan: aiobotocore
        # clients hold an aiohttp connection pool tied to a loop, and
        # the per-call cost is negligible compared to the network
        # round-trip.
        self._session: (
            aioboto3.Session | None
        ) = None  # type: ignore[name-defined]

    async def __aenter__(self) -> "S3BlobStore":
        try:
            import aioboto3
        except ImportError as e:
            raise ImportError(
                "S3BlobStore requires the optional dependency ``aioboto3``. "
                "Install it with ``uv pip install aioboto3`` or with the "
                "``[s3]`` extra.",
            ) from e

        self._session = aioboto3.Session()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        self._session = None

    def _client(self) -> Any:
        """Open a fresh S3 client context manager for one call."""
        if self._session is None:
            raise RuntimeError(
                "S3BlobStore is not entered; use it inside "
                "``async with`` before calling blob methods.",
            )
        return self._session.client(
            "s3",
            region_name=self._region_name,
            endpoint_url=self._endpoint_url,
            aws_access_key_id=self._aws_access_key_id,
            aws_secret_access_key=self._aws_secret_access_key,
            aws_session_token=self._session_token,
            use_ssl=self._use_ssl,
            config=self._config,
        )

    @staticmethod
    def _parse_uri(uri: str) -> tuple[str, str]:
        """Split an ``s3://{bucket}/{key}`` URI into ``(bucket, key)``."""
        if not uri.startswith(_SCHEME):
            raise ValueError(f"Not an S3 blob URI: {uri!r}")
        rest = uri[len(_SCHEME) :]
        bucket, _, key = rest.partition("/")
        if not bucket or not key:
            raise ValueError(f"Malformed S3 blob URI: {uri!r}")
        return bucket, key

    @classmethod
    def _key_from_uri(cls, uri: str, expected_bucket: str) -> str:
        """Return the object key, asserting the bucket matches.

        Used by mutating operations (``delete``, ``exists``) where
        crossing into another bucket would be a bug — the configured
        bucket is the only place the store owns objects.
        """
        bucket, key = cls._parse_uri(uri)
        if bucket != expected_bucket:
            raise ValueError(
                f"Bucket {bucket!r} in URI {uri!r} does not match "
                f"configured bucket {expected_bucket!r}.",
            )
        return key

    async def write_stream(self, key: str, stream: IO[bytes]) -> str:
        """Stream-write a blob and return its ``s3://{bucket}/{key}`` URI.

        Uses ``upload_fileobj`` so aioboto3 picks multipart upload
        automatically for bodies above the multipart threshold
        (8 MiB by default in botocore). For smaller bodies it
        promotes to a single ``PutObject`` call.
        """
        async with self._client() as s3:
            await s3.upload_fileobj(stream, self._bucket, key)
        return f"{_SCHEME}{self._bucket}/{key}"

    @asynccontextmanager
    async def open(  # type: ignore[override]
        self,
        uri: str,
    ) -> AsyncIterator[AsyncReadable]:
        """Stream-read a blob by URI.

        Yields an :class:`AsyncReadable` backed by the response
        ``Body``. The S3 GET response is held open for the duration
        of the ``async with`` block — exit promptly so the connection
        returns to the pool.

        The bucket is read from the URI rather than the configured
        bucket so post-migration deployments can still resolve
        document records that were written under the previous bucket.
        The IAM role must grant ``s3:GetObject`` on the legacy bucket
        for this to actually succeed at the wire level.
        """
        bucket, key = self._parse_uri(uri)
        async with self._client() as s3:
            response = await s3.get_object(Bucket=bucket, Key=key)
            body = response["Body"]
            try:
                yield _StreamingBody(body)
            finally:
                # aiobotocore's body has a ``close`` coroutine; call
                # it explicitly so the connection is released rather
                # than waiting for GC.
                close = getattr(body, "close", None)
                if close is not None:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result

    async def delete(self, uri: str) -> None:
        """Delete the object at ``uri``. Idempotent."""
        key = self._key_from_uri(uri, self._bucket)
        async with self._client() as s3:
            # S3 ``DeleteObject`` is idempotent on a missing key —
            # no special handling needed for the "already gone" case.
            await s3.delete_object(Bucket=self._bucket, Key=key)

    async def exists(self, uri: str) -> bool:
        """Return whether the object at ``uri`` is present."""
        key = self._key_from_uri(uri, self._bucket)
        async with self._client() as s3:
            try:
                await s3.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception as exc:  # noqa: BLE001
                # botocore's ClientError has ``response["Error"]["Code"]``
                # of "404" or "NoSuchKey" for missing objects; we
                # treat anything that isn't a successful HEAD as
                # "missing" rather than re-raising, matching the
                # local backend's permissive contract. A genuinely
                # broken backend (auth failure, network) will surface
                # on the next read/write.
                code = (
                    getattr(exc, "response", {}).get("Error", {}).get("Code")
                )
                if code in {"404", "NoSuchKey", "NotFound"}:
                    return False
                raise
