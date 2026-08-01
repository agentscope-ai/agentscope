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
import json
import random
import re
from datetime import datetime
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, AsyncIterator

from ._base import SkillArchive, SkillHubBase
from .._error import HubError
from ._card import SkillCard, SkillHubPage

if TYPE_CHECKING:
    import httpx

DEFAULT_BASE_URL = "https://clawhub.ai"

# The default streaming chunk size (64 KiB) used when downloading skill
# archives, balancing memory usage and per-chunk overhead.
DEFAULT_CHUNK_SIZE = 64 * 1024

# ClawHub's bulk skill generator stamps versions as ``0.<date>.<time>``
# rather than semver, e.g. ``0.20260729.110214``. Shown raw that is 17
# characters of noise next to a skill name, so it is rendered as the
# date it encodes.
_TIMESTAMP_VERSION = re.compile(r"^0\.(\d{8})\.(\d{6})$")


def _humanize_version(version: str | None) -> str | None:
    """Render a timestamp-style version as its date.

    Anything else — semver included — is returned untouched, so a hub
    that versions its skills properly is never rewritten.

    Args:
        version (`str | None`):
            The version as reported upstream.

    Returns:
        `str | None`:
            ``YYYY-MM-DD`` for a timestamp version, else the input.
    """
    if version is None:
        return None

    match = _TIMESTAMP_VERSION.match(version)
    if not match:
        return version

    try:
        stamp = datetime.strptime(match.group(1), "%Y%m%d")
    except ValueError:
        # Eight digits that are not a real date — leave it alone.
        return version
    return stamp.strftime("%Y-%m-%d")


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
        icon_url: str | None = "https://openclaw.ai/favicon.svg",
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
            icon_url (`str | None`, optional):
                The hub's icon, defaulting to the ClawHub favicon.
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
        super().__init__(hub_id, display_name, description, icon_url)
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self.max_retries = max_retries
        self._client: "httpx.AsyncClient | None" = None

    async def __aenter__(self) -> "ClawSkillHub":
        """Open the HTTP client shared by every request.

        Returns:
            `ClawSkillHub`:
                ``self``.
        """
        import httpx

        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Close the shared HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> "httpx.AsyncClient":
        """Return the shared client, opening one if the hub was never
        entered — which is how a hub used outside the app behaves.

        Returns:
            `httpx.AsyncClient`:
                The client to issue requests with.
        """
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

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
            `HubError`:
                When the API returns a non-success status code.
        """
        url = f"{self.base_url}{path}"
        client = self._http()
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
                raise HubError(
                    self.hub_id,
                    response.status_code,
                    self._describe_error(response.status_code, response.text),
                )

            return response

        raise HubError(
            self.hub_id,
            429,
            "Rate limit exceeded after retries",
        )

    async def download(
        self,
        user_id: str,
        card_id: str,
        version: str | None = None,
        tag: str | None = None,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> SkillArchive:
        """Open a skill archive via ``GET /api/v1/download``.

        The response headers are awaited here — so a missing skill or an
        exhausted rate limit raises before the caller commits to an
        install — while the body stays lazy, letting the archive be
        piped into a sandbox without ever being held whole.

        Args:
            user_id (`str`):
                The user identifier the download is authorized against.
                Unused by the public ClawHub catalog, kept for interface
                compatibility.
            card_id (`str`):
                The :attr:`SkillCard.id`, either ``owner/slug`` or a
                bare slug.
            version (`str | None`, optional):
                A specific semver version to download. When omitted with
                ``tag``, the latest version is used.
            tag (`str | None`, optional):
                A tag name (e.g. ``"latest"``) to resolve the version.
            chunk_size (`int`, defaults to `65536`):
                The number of bytes to yield per chunk.

        Returns:
            `SkillArchive`:
                The ZIP archive and its byte stream.

        Raises:
            `KeyError`:
                When the registry has no skill under ``card_id``.
            `HubError`:
                When the API returns any other non-success status code.
        """
        slug, owner_params = self._split_card_id(card_id)
        params: dict = {"slug": slug, **owner_params}
        if version is not None:
            params["version"] = version
        if tag is not None:
            params["tag"] = tag

        url = f"{self.base_url}/api/v1/download"
        client = self._http()
        # Only the response is owned here — the client outlives every
        # download, so closing it would break the next one.
        stack = AsyncExitStack()
        try:
            for attempt in range(self.max_retries + 1):
                response = await stack.enter_async_context(
                    client.stream(
                        "GET",
                        url,
                        params=params,
                        headers=self._headers(),
                    ),
                )
                if response.status_code == 429 and attempt < self.max_retries:
                    # Wait with jittered backoff before re-opening the
                    # stream, as recommended by ClawHub.
                    delay = self._retry_delay(response.headers)
                    await stack.aclose()
                    stack = AsyncExitStack()
                    await asyncio.sleep(delay + random.uniform(0, 1))
                    continue

                if response.status_code >= 400:
                    # Read the (plain-text) error body before the stream
                    # context closes.
                    body = await response.aread()
                    if response.status_code == 404:
                        raise KeyError(card_id)
                    raise HubError(
                        self.hub_id,
                        response.status_code,
                        self._describe_error(
                            response.status_code,
                            body.decode("utf-8", errors="replace"),
                        ),
                    )

                return SkillArchive(
                    "zip",
                    self._drain(stack, response, chunk_size),
                )

            raise HubError(
                self.hub_id,
                429,
                "Rate limit exceeded after retries",
            )
        except BaseException:
            await stack.aclose()
            raise

    @staticmethod
    async def _drain(
        stack: AsyncExitStack,
        response: "httpx.Response",
        chunk_size: int,
    ) -> AsyncIterator[bytes]:
        """Yield the response body, closing the response after.

        Args:
            stack (`AsyncExitStack`):
                Holds the open response, closed when the body is done.
            response (`httpx.Response`):
                The open response whose body is drained.
            chunk_size (`int`):
                The number of bytes to yield per chunk.

        Yields:
            `bytes`:
                The next chunk of the archive.
        """
        try:
            async for chunk in response.aiter_bytes(chunk_size):
                yield chunk
        finally:
            await stack.aclose()

    @staticmethod
    def _describe_error(status_code: int, body: str) -> str:
        """Render an API error body as something a person can act on.

        Only the ambiguous-slug 409 gets special treatment: it is the one
        error whose body is structured, and the raw JSON is unreadable in
        a toast. It reaches a user when a card came from the catalog
        endpoint, which names no owner, and that slug turns out to be
        shared.

        Args:
            status_code (`int`):
                The HTTP status code the API answered with.
            body (`str`):
                The raw response body.

        Returns:
            `str`:
                The message to carry on the :class:`HubError`.
        """
        if status_code != 409:
            return body
        try:
            payload = json.loads(body)
        except ValueError:
            return body
        if payload.get("code") != "AMBIGUOUS_SKILL_SLUG":
            return body

        refs = [m["ref"] for m in payload.get("matches", []) if m.get("ref")]
        return (
            f"Several publishers use the skill name "
            f"{payload.get('slug', '')!r}: {', '.join(refs)}. Search for "
            f"it by name and install the one you want from the results."
        )

    @staticmethod
    def _split_card_id(card_id: str) -> tuple[str, dict]:
        """Split a card id into its slug and the params that pin it down.

        Args:
            card_id (`str`):
                Either ``owner/slug`` or a bare ``slug``.

        Returns:
            `tuple[str, dict]`:
                The slug, and either ``{"ownerHandle": ...}`` or an empty
                dict for a card whose listing named no owner.
        """
        handle, _, slug = card_id.rpartition("/")
        return slug, {"ownerHandle": handle} if handle else {}

    def _to_card(self, item: dict) -> SkillCard:
        """Build a :class:`SkillCard` from one catalog or search record.

        No per-card request is made: everything comes from the record the
        listing already returned, which is what keeps browsing at one
        upstream call per page.

        The card id is ``owner/slug`` whenever the record names an owner,
        because a slug alone is not unique: several publishers may use
        one, and the lookup endpoints answer 409 rather than guess. The
        catalog endpoint omits the owner, so those cards fall back to the
        bare slug — see :meth:`_split_card_id`. The opaque ``id`` the
        search endpoint also returns cannot be resolved back through the
        public API, so it is deliberately ignored.

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

        stats = (
            item.get("stats") if isinstance(item.get("stats"), dict) else {}
        )

        # The owner sits beside the skill on the detail endpoint and under
        # ``native`` on search; the catalog endpoint omits it entirely, so
        # most browse cards carry no author and no icon.
        owner = item.get("owner") or (item.get("native") or {}).get("owner")
        owner = owner if isinstance(owner, dict) else {}

        # The search endpoint hands back an owner-scoped canonical path;
        # the catalog endpoint has no link at all, so the slug route is
        # built instead — it 307s to the same canonical page.
        canonical = item.get("canonicalUrl")
        url = (
            f"{self.base_url}{canonical}"
            if canonical
            else f"{self.base_url}/skills/{item['slug']}"
        )

        # ``ownerHandle`` sits at the top level on search and beside the
        # owner object on the detail endpoint.
        handle = item.get("ownerHandle") or owner.get("handle")

        return SkillCard(
            hub_id=self.hub_id,
            id=f"{handle}/{item['slug']}" if handle else item["slug"],
            # The name stays the bare slug: it becomes a directory name
            # in the workspace, so it cannot carry a separator.
            name=item["slug"],
            display_name=item.get("displayName"),
            description=item.get("summary") or "",
            # ``topics`` holds the categorical labels. Upstream ``tags``
            # is a version-tag -> version map, not something to show.
            tags=list(item.get("topics") or []),
            version=_humanize_version(version),
            # ``updatedAt`` is reported in Unix milliseconds.
            updated_at=float(updated_at) / 1000.0 if updated_at else None,
            author=owner.get("displayName") or owner.get("handle"),
            icon_url=owner.get("image"),
            installs=stats.get("installs"),
            # Nested under ``stats`` on the catalog, top level on search.
            downloads=stats.get("downloads", item.get("downloads")),
            url=url,
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
            `HubError`:
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
                The :attr:`SkillCard.id`, either ``owner/slug`` or a
                bare slug.

        Returns:
            `SkillCard`:
                The card with :attr:`SkillCard.markdown` populated.

        Raises:
            `KeyError`:
                When the registry has no skill under ``card_id``.
            `HubError`:
                When the API returns any other non-success status code.
        """
        import frontmatter

        slug, owner_params = self._split_card_id(card_id)
        try:
            detail, markdown = await asyncio.gather(
                self._request("GET", f"/api/v1/skills/{slug}", owner_params),
                self._request(
                    "GET",
                    f"/api/v1/skills/{slug}/file",
                    {"path": "SKILL.md", **owner_params},
                ),
            )
        except HubError as e:
            if e.status_code == 404:
                raise KeyError(card_id) from e
            raise

        payload = detail.json()
        item = dict(payload.get("skill") or {})
        item.setdefault("slug", slug)
        item["latestVersion"] = payload.get("latestVersion")
        # The owner is a sibling of ``skill`` here, but ``_to_card`` reads
        # it off the record, so fold it in.
        item["owner"] = payload.get("owner")

        card = self._to_card(item)
        parsed = await asyncio.to_thread(frontmatter.loads, markdown.text)
        card.markdown = parsed.content
        if not card.description:
            card.description = str(parsed.get("description", ""))
        return card
