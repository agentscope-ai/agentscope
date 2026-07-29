# -*- coding: utf-8 -*-
"""The ClawHub skill provider.

A thin async client around the ClawHub HTTP API (``https://clawhub.ai``)
that exposes the registry skills through the
:class:`~agentscope.app.hub._skill._base.SkillHubBase` interface.

`ClawHub HTTP API <https://clawhub.ai/api/v1/openapi.json>`_

.. note:: Only the public read endpoints are required. A Bearer token
    (``clh_...``) is optional and, when provided, raises the per-user
    rate limit.

.. note:: Browsing and searching are different upstream endpoints:
    ``GET /api/v1/skills`` is cursor-paginated but takes no query, while
    ``GET /api/v1/search`` takes a query but returns a single page.
"""
import asyncio
import random
from typing import TYPE_CHECKING, AsyncIterator

from ._base import SkillHubBase
from ._card import SkillCard, SkillHubPage

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

    .. code-block:: python

        hub = ClawSkillHub(api_token="clh_xxx")
        page = await hub.list_skills(user_id="alice", q="git")
        async for chunk in hub.download("gifgrep"):
            ...  # stream-write into a sandbox
    """

    def __init__(
        self,
        hub_id: str = "clawhub",
        display_name: str = "ClawHub",
        description: str = "The ClawHub public skill registry.",
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_token: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
    ) -> None:
        """Initialize the ClawHub skill provider.

        Args:
            hub_id (`str`, defaults to `"clawhub"`):
                The stable identifier addressing this hub in the API
                routes.
            display_name (`str`, defaults to `"ClawHub"`):
                The user-facing hub name.
            description (`str`, optional):
                The user-facing hub description.
            base_url (`str`, defaults to `"https://clawhub.ai"`):
                The base URL of the ClawHub registry.
            api_token (`str | None`, optional):
                The ClawHub Bearer token (``clh_...``). When provided,
                requests are authenticated and use the higher per-user
                rate limit bucket.
            timeout (`float`, defaults to `30.0`):
                The per-request timeout in seconds.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries when a request is rate
                limited (HTTP ``429``).
        """
        super().__init__(hub_id, display_name, description)
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
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
        card_id: str,
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
            card_id (`str`):
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
            `KeyError`:
                When the registry has no skill under ``card_id``.
            `ClawHubError`:
                When the API returns any other non-success status code.
        """
        import httpx

        params: dict = {"slug": card_id}
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
                        if response.status_code == 404:
                            raise KeyError(card_id)
                        raise ClawHubError(
                            response.status_code,
                            body.decode("utf-8", errors="replace"),
                        )

                    async for chunk in response.aiter_bytes(chunk_size):
                        yield chunk
                    return

        raise ClawHubError(429, "Rate limit exceeded after retries")

    def _to_card(self, item: dict) -> SkillCard:
        """Build a :class:`SkillCard` from one catalog or search record.

        No per-card request is made: everything comes from the record the
        listing already returned, which is what keeps browsing at one
        upstream call per page.

        The slug is used as the card id — it is what every lookup and
        download endpoint takes. The opaque ``id`` the search endpoint
        also returns cannot be resolved back through the public API, so
        it is deliberately ignored.

        Args:
            item (`dict`):
                A record from ``GET /api/v1/skills`` (``items``) or
                ``GET /api/v1/search`` (``results``).

        Returns:
            `SkillCard`:
                The card describing this skill.
        """
        # ``latestVersion`` is an object on the catalog endpoint and
        # absent on the search endpoint, which carries ``version``.
        latest = item.get("latestVersion")
        if isinstance(latest, dict):
            version = latest.get("version")
        else:
            version = latest or item.get("version")

        updated_at = item.get("updatedAt")

        return SkillCard(
            hub_id=self.hub_id,
            id=item["slug"],
            name=item["slug"],
            display_name=item.get("displayName"),
            description=item.get("summary") or "",
            # ``topics`` holds the categorical labels. Upstream ``tags``
            # is a version-tag -> version map, not something to show.
            tags=list(item.get("topics") or []),
            version=version,
            # ``updatedAt`` is reported in Unix milliseconds.
            updated_at=float(updated_at) / 1000.0 if updated_at else None,
            metadata={
                key: item[key]
                for key in ("stats", "downloads", "tags")
                if item.get(key)
            },
        )

    async def list_skills(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> SkillHubPage:
        """Browse or search the ClawHub registry in one request.

        With ``q`` the search endpoint is used, which ranks by relevance
        but returns a single page — ``next_cursor`` is then always
        ``None``. Without ``q`` the cursor-paginated catalog is browsed.

        Args:
            user_id (`str`):
                The user identifier to query skill cards for. Unused by
                the public ClawHub catalog, kept for interface
                compatibility.
            q (`str | None`, optional):
                A keyword filtering the cards.
            cursor (`str | None`, optional):
                The opaque cursor from a previous page. Ignored when
                ``q`` is given, since search is not paginated upstream.
            limit (`int`, defaults to `20`):
                The maximum number of cards per page (1-200).

        Returns:
            `SkillHubPage`:
                The requested page of cards plus the next cursor.

        Raises:
            `ClawHubError`:
                When the API returns a non-success status code.
        """
        if q:
            response = await self._request(
                "GET",
                "/api/v1/search",
                {"q": q, "limit": limit},
            )
            records = response.json().get("results") or []
            next_cursor = None
        else:
            params: dict = {"limit": limit}
            if cursor is not None:
                params["cursor"] = cursor
            response = await self._request("GET", "/api/v1/skills", params)
            payload = response.json()
            records = payload.get("items") or []
            next_cursor = payload.get("nextCursor")

        return SkillHubPage(
            cards=[self._to_card(r) for r in records if r.get("slug")],
            next_cursor=next_cursor,
        )

    async def get_skill(self, user_id: str, card_id: str) -> SkillCard:
        """Fetch one skill plus its ``SKILL.md`` body.

        Args:
            user_id (`str`):
                The user identifier to query the card for. Unused by the
                public ClawHub catalog, kept for interface compatibility.
            card_id (`str`):
                The canonical slug of the skill.

        Returns:
            `SkillCard`:
                The card with :attr:`SkillCard.markdown` populated.

        Raises:
            `KeyError`:
                When the registry has no skill under ``card_id``.
            `ClawHubError`:
                When the API returns any other non-success status code.
        """
        import frontmatter

        try:
            detail, markdown = await asyncio.gather(
                self._request("GET", f"/api/v1/skills/{card_id}"),
                self._request(
                    "GET",
                    f"/api/v1/skills/{card_id}/file",
                    {"path": "SKILL.md"},
                ),
            )
        except ClawHubError as e:
            if e.status_code == 404:
                raise KeyError(card_id) from e
            raise

        payload = detail.json()
        item = dict(payload.get("skill") or {})
        item.setdefault("slug", card_id)
        item["latestVersion"] = payload.get("latestVersion")

        card = self._to_card(item)
        parsed = await asyncio.to_thread(frontmatter.loads, markdown.text)
        card.markdown = parsed.content
        if not card.description:
            card.description = str(parsed.get("description", ""))
        return card
