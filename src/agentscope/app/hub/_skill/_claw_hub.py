# -*- coding: utf-8 -*-
"""The ClawHub skill provider.

A thin async client around the ClawHub HTTP API (``https://clawhub.ai``)
that exposes the registry skills through the
:class:`~agentscope.app._hub._skill._base.SkillHubBase` interface.

`ClawHub HTTP API <https://clawhub.ai/api/v1/openapi.json>`_

.. note:: Only the public read endpoints are required. A Bearer token
    (``clh_...``) is optional and, when provided, raises the per-user
    rate limit.
"""
import asyncio
import random
from typing import TYPE_CHECKING, AsyncIterator

from ._base import SkillHubBase
from ....skill import Skill
from ...._logging import logger

if TYPE_CHECKING:
    import httpx

DEFAULT_BASE_URL = "https://clawhub.ai"

# The default streaming chunk size (64 KiB) used when downloading skill
# archives, balancing memory usage and per-chunk overhead.
DEFAULT_CHUNK_SIZE = 64 * 1024


class ClawHubError(Exception):
    """Raised when the ClawHub API returns a non-success response.

    Public v1 error responses are plain text, so the response body is
    surfaced verbatim as a human-readable message.
    """

    def __init__(self, status_code: int, message: str) -> None:
        """Initialize the error.

        Args:
            status_code (`int`):
                The HTTP status code returned by the ClawHub API.
            message (`str`):
                The plain-text error body returned by the ClawHub API.
        """
        self.status_code = status_code
        self.message = message
        super().__init__(f"ClawHub API error {status_code}: {message}")


class ClawSkillHub(SkillHubBase):
    """A skill hub backed by the ClawHub registry.

    Exposes two operations: :meth:`list_skills` to browse the registry
    catalog and :meth:`download` to stream a skill archive.

    .. code-block:: python

        hub = ClawHub(api_token="clh_xxx")
        skills = await hub.list_skills(user_id="alice")
        async for chunk in hub.download("gifgrep"):
            ...  # stream-write into a sandbox
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        api_token: str | None = None,
        limit: int = 100,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        """Initialize the ClawHub skill provider.

        Args:
            base_url (`str`, defaults to `"https://clawhub.ai"`):
                The base URL of the ClawHub registry.
            api_token (`str | None`, optional):
                The ClawHub Bearer token (``clh_...``). When provided,
                requests are authenticated and use the higher per-user
                rate limit bucket.
            limit (`int`, defaults to `100`):
                The maximum number of skills to list (1-200).
            timeout (`float`, defaults to `30.0`):
                The per-request timeout in seconds.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries when a request is rate
                limited (HTTP ``429``).
        """
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.limit = limit
        self.timeout = timeout
        self.max_retries = max_retries

    def _headers(self) -> dict[str, str]:
        """Build the request headers, including auth when available.

        Returns:
            `dict[str, str]`:
                The HTTP headers to attach to a request.
        """
        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"
        return headers

    @staticmethod
    def _retry_delay(headers: dict) -> float:
        """Compute the retry delay (in seconds) from rate-limit headers.

        Honors ``Retry-After`` first, then falls back to
        ``RateLimit-Reset`` (delay) and ``X-RateLimit-Reset`` (absolute
        Unix epoch seconds), as documented by ClawHub.

        Args:
            headers (`dict`):
                The response headers from a ``429`` response.

        Returns:
            `float`:
                The number of seconds to wait before retrying.
        """
        import time

        retry_after = headers.get("Retry-After") or headers.get(
            "retry-after",
        )
        if retry_after:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass

        reset = headers.get("RateLimit-Reset") or headers.get(
            "ratelimit-reset",
        )
        if reset:
            try:
                return max(0.0, float(reset))
            except ValueError:
                pass

        x_reset = headers.get("X-RateLimit-Reset") or headers.get(
            "x-ratelimit-reset",
        )
        if x_reset:
            try:
                return max(0.0, float(x_reset) - time.time())
            except ValueError:
                pass

        return 1.0

    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
    ) -> "httpx.Response":
        """Send a request to the ClawHub API with rate-limit handling.

        Args:
            method (`str`):
                The HTTP method, e.g. ``"GET"``.
            path (`str`):
                The request path under :attr:`base_url`, e.g.
                ``"/api/v1/skills/gifgrep"``.
            params (`dict | None`, optional):
                The query parameters to attach to the request.

        Returns:
            `httpx.Response`:
                The successful HTTP response.

        Raises:
            `ClawHubError`:
                When the API returns a non-success status code.
        """
        import httpx

        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                response = await client.request(
                    method,
                    url,
                    params=params,
                    headers=self._headers(),
                )

                if response.status_code == 429 and attempt < self.max_retries:
                    # Wait with jittered backoff to avoid synchronized
                    # retries, as recommended by ClawHub.
                    delay = self._retry_delay(response.headers)
                    await asyncio.sleep(delay + random.uniform(0, 1))
                    continue

                if response.status_code >= 400:
                    raise ClawHubError(response.status_code, response.text)

                return response

        raise ClawHubError(429, "Rate limit exceeded after retries")

    async def download(
        self,
        slug: str,
        version: str | None = None,
        tag: str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """Stream a skill archive via ``GET /api/v1/download``.

        The archive is yielded chunk by chunk so callers never hold the
        whole (potentially large) ZIP in memory and can stream-write it
        into any sink, e.g. a sandbox filesystem. The body is consumed
        lazily; rate-limit (``429``) responses are retried before any
        bytes are produced.

        Args:
            slug (`str`):
                The canonical slug of the skill.
            version (`str | None`, optional):
                A specific semver version to download. When omitted with
                ``tag``, the latest version is used.
            tag (`str | None`, optional):
                A tag name (e.g. ``"latest"``) to resolve the version.
            chunk_size (`int`, defaults to `65536`):
                The number of bytes to yield per chunk.

        Yields:
            `bytes`:
                The next chunk of the skill ZIP archive.

        Raises:
            `ClawHubError`:
                When the API returns a non-success status code.
        """
        import httpx

        params: dict = {"slug": slug}
        if version is not None:
            params["version"] = version
        if tag is not None:
            params["tag"] = tag

        url = f"{self.base_url}/api/v1/download"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for attempt in range(self.max_retries + 1):
                async with client.stream(
                    "GET",
                    url,
                    params=params,
                    headers=self._headers(),
                ) as response:
                    if (
                        response.status_code == 429
                        and attempt < self.max_retries
                    ):
                        # Wait with jittered backoff before re-opening
                        # the stream, as recommended by ClawHub.
                        delay = self._retry_delay(response.headers)
                        await asyncio.sleep(delay + random.uniform(0, 1))
                        continue

                    if response.status_code >= 400:
                        # Read the (plain-text) error body before the
                        # stream context closes.
                        body = await response.aread()
                        raise ClawHubError(
                            response.status_code,
                            body.decode("utf-8", errors="replace"),
                        )

                    async for chunk in response.aiter_bytes(chunk_size):
                        yield chunk
                    return

        raise ClawHubError(429, "Rate limit exceeded after retries")

    async def _load_skill(self, item: dict) -> Skill:
        """Build a `Skill` from a catalog item and its ``SKILL.md``.

        No local files are written: the markdown is fetched directly from
        the registry file endpoint, and the archive bytes are left to the
        caller (e.g. a sandbox) to stream via :meth:`download`.

        Args:
            item (`dict`):
                A skill record from ``GET /api/v1/skills``.

        Returns:
            `Skill`:
                The parsed skill object.
        """
        import frontmatter

        slug = item["slug"]
        response = await self._request(
            "GET",
            f"/api/v1/skills/{slug}/file",
            {"path": "SKILL.md"},
        )
        parsed = await asyncio.to_thread(frontmatter.loads, response.text)

        # ``updatedAt`` is reported in Unix milliseconds; convert to
        # seconds to match the ``Skill.updated_at`` convention.
        updated_at = float(item.get("updatedAt") or 0) / 1000.0

        return Skill(
            name=slug,
            description=item.get("summary")
            or str(parsed.get("description", "")),
            dir=slug,
            markdown=parsed.content,
            updated_at=updated_at,
        )

    async def list_skills(self, user_id: str) -> list[Skill]:
        """Get the available skills from the ClawHub registry.

        Browses the catalog via ``GET /api/v1/skills`` and loads each
        skill's ``SKILL.md`` concurrently. Skills that fail to load are
        skipped with a warning so a single failure does not break the
        whole listing.

        Args:
            user_id (`str`):
                The user identifier to query the skill. Currently unused
                by the public ClawHub catalog, kept for interface
                compatibility.

        Returns:
            `list[Skill]`:
                The list of successfully loaded skills.
        """

        response = await self._request(
            "GET",
            "/api/v1/skills",
            {"limit": self.limit},
        )
        items = response.json().get("items", [])

        results = await asyncio.gather(
            *(self._load_skill(item) for item in items),
            return_exceptions=True,
        )

        skills: list[Skill] = []
        for item, result in zip(items, results):
            if isinstance(result, Skill):
                skills.append(result)
            else:
                logger.warning(
                    "Failed to load ClawHub skill %s: %s",
                    item.get("slug"),
                    str(result),
                )

        return skills
