# -*- coding: utf-8 -*-
"""An external skillhub provider.

A thin async client around the deployment's own skillhub HTTP API that
exposes the catalog and download endpoints through the
:class:`~agentscope.app.hub._skill._base.SkillHubBase` interface, so the
web UI and the workspace flows treat it like any other skill hub.

Authentication is cookie-based and token-driven: the caller passes a
``guwpToken`` per request via :meth:`set_token`; every call exchanges
it for a fresh ``SESSION`` cookie against the login endpoint (no
caching).
"""
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any, AsyncIterator

from ._base import SkillArchive, SkillHubBase
from .._error import HubError
from ._card import SkillCard, SkillHubPage
from ...._logging import logger

if TYPE_CHECKING:
    import httpx

#: Default host + prefix of the deployment's skillhub.
DEFAULT_BASE_URL = "http://53.12.9.18/skillhub-server"

#: Catalog endpoint path; the namespace is passed as a query parameter.
CATALOG_PATH = "/api/web/skills"

#: Download endpoint prefix — the final URL is
#: ``{base_url}{DOWNLOAD_PREFIX}/{card_id}/download``.
DOWNLOAD_PREFIX = "/api/web/skills/global"

#: Catalog namespace.
CATALOG_NAMESPACE = "global"

#: Endpoint listing the current user's own uploaded skills.
MY_SKILLS_PATH = "/api/web/me/skills"
#: Default streaming chunk size (64 KiB).
DEFAULT_CHUNK_SIZE = 64 * 1024


class ExternalSkillHub(SkillHubBase):
    """A skill hub backed by the deployment's own skillhub server.

    .. code-block:: python

        hub = ExternalSkillHub(base_url="http://53.12.9.18/skillhub-server")
        hub.set_token("guwp_...")          # optional, per request
        page = await hub.list_skills(user_id="alice", q="write")
        archive = await hub.download("alice", "write")
        # ... stream archive into a workspace
    """

    def __init__(
        self,
        hub_id: str = "external",
        display_name: str = "External SkillHub",
        description: str = "The deployment's skillhub catalog.",
        icon_url: str | None = None,
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the external skillhub provider.

        Args:
            hub_id (`str`): Stable id addressing the hub in the routes.
            display_name (`str`): The user-facing hub name.
            description (`str`): The user-facing hub description.
            icon_url (`str | None`): The hub's icon.
            base_url (`str`): Base URL of the skillhub server.
            api_token (`str | None`): Initial ``guwpToken``; may be
                updated at runtime via :meth:`set_token`.
            timeout (`float`): Per-request timeout in seconds.
        """
        super().__init__(hub_id, display_name, description, icon_url)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._guwp_token = api_token
        self._client: "httpx.AsyncClient | None" = None

    def set_token(self, token: str | None) -> None:
        """Update the ``guwpToken`` used for cookie refresh.

        Safe to call per request — the next call re-authenticates with
        the new token.
        """
        self._guwp_token = token

    # ── lifecycle ────────────────────────────────────────────────

    async def __aenter__(self) -> "ExternalSkillHub":
        """Open the shared HTTP client."""
        import httpx

        self._client = httpx.AsyncClient(timeout=self.timeout)
        return self

    async def __aexit__(self, *exc: Any) -> None:
        """Close the shared HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> "httpx.AsyncClient":
        """Return the shared client, opening one if never entered."""
        import httpx

        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _headers(self, cookie: str) -> dict[str, str]:
        """Build the request headers, including the session cookie."""
        return {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Content-Type": "application/json",
            "Cookie": cookie,
            "User-Agent": "PostmanRuntime-ApipostRuntime/1.1.0",
        }

    # ── auth ─────────────────────────────────────────────────────

    async def _cookie(self) -> str:
        """Return a fresh ``SESSION=...`` cookie for the current token.

        No caching: every call re-authenticates against the login
        endpoint with the current ``guwpToken`` (set via
        :meth:`set_token`). No token means the unauthenticated session
        (empty cookie).
        """
        token = self._guwp_token
        if not token:
            return ""

        import json

        login_url = f"{self.base_url}/api/v1/auth/third-party/login"
        body = json.dumps(
            {"loginMethod": "TOKEN", "platform": "GUWP", "token": token},
        )
        try:
            resp = await self._http().post(
                login_url,
                content=body,
                headers=self._headers(""),
            )
            resp.raise_for_status()
            new_session_id = resp.headers.get("x-session-id", "")
            if new_session_id:
                return f"SESSION={new_session_id}"
        except Exception as e:  # noqa: BLE001
            logger.error(
                "Failed to refresh external skillhub cookie: %s",
                e,
            )
        return ""

    # ── SkillHubBase ─────────────────────────────────────────────

    async def list_skills(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> SkillHubPage:
        """Browse the catalog.

        ``cursor`` encodes the upstream page number as ``page:N``.
        """
        import urllib.parse

        page = 0
        if cursor and cursor.startswith("page:"):
            try:
                page = int(cursor.split(":", 1)[1])
            except ValueError:
                page = 0

        url = (
            f"{self.base_url}{CATALOG_PATH}"
            f"?page={page}&q={urllib.parse.quote(q or '', safe='')}"
            f"&size={limit}&sort=&label=&namespace={CATALOG_NAMESPACE}"
        )
        try:
            resp = await self._http().get(
                url,
                headers=self._headers(await self._cookie()),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            status_code = getattr(getattr(e, "response", None), "status_code", 0)
            raise HubError(self.hub_id, status_code, str(e)) from e

        payload = data.get("data") or {}
        items = payload.get("items") or []
        total = payload.get("total") or 0

        cards = [
            self._to_card(item)
            for item in items
            if item.get("slug")
        ]
        next_cursor = f"page:{page + 1}" if (page + 1) * limit < total else None
        return SkillHubPage(
            cards=cards,
            next_cursor=next_cursor,
            total=total,
        )

    def _to_card(self, item: dict) -> SkillCard:
        """Build a :class:`SkillCard` from one catalog record."""
        slug = item["slug"]
        return SkillCard(
            hub_id=self.hub_id,
            id=slug,
            name=slug,
            description=item.get("summary", "") or "",
            metadata={
                k: v
                for k, v in item.items()
                if k not in ("slug", "summary")
            },
        )

    async def list_uploaded_skills(self, user_id: str) -> SkillHubPage:
        """Browse the skills the current user uploaded to the skillhub.

        Requires a ``guwpToken`` set via :meth:`set_token` — the
        endpoint is per-user and the session cookie carries the
        identity.

        Args:
            user_id (`str`): The user identifier (unused by the remote
                endpoint; kept for interface consistency).

        Returns:
            `SkillHubPage`: The uploaded skills plus their total count.
        """
        url = f"{self.base_url}{MY_SKILLS_PATH}"
        try:
            resp = await self._http().get(
                url,
                headers=self._headers(await self._cookie()),
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:  # noqa: BLE001
            status_code = getattr(getattr(e, "response", None), "status_code", 0)
            raise HubError(self.hub_id, status_code, str(e)) from e

        payload = data.get("data") or {}
        items = payload.get("items") or []
        total = payload.get("total")
        if total is None:
            total = len(items)

        return SkillHubPage(
            cards=[self._to_card(item) for item in items if item.get("slug")],
            next_cursor=None,
            total=total,
        )

    async def get_skill(self, user_id: str, card_id: str) -> SkillCard:
        """Not implemented yet.

        The remote service currently exposes only the catalog and the
        download endpoint; no per-card detail endpoint is wired up.
        Install-to-library (``POST /hub/skill/.../install``) via this
        hub therefore fails until this is implemented.

        Raises:
            NotImplementedError: Always, for now.
        """
        raise NotImplementedError(
            "ExternalSkillHub.get_skill is not implemented yet — the "
            "remote skillhub exposes no single-card detail endpoint.",
        )

    async def download(
        self,
        user_id: str,
        card_id: str,
        version: str | None = None,
    ) -> SkillArchive:
        """Open a skill archive via ``{base}/api/web/skills/global/<id>/download``.

        The response headers are awaited here — so a missing skill (404)
        raises before the caller commits to an install — while the body
        stays lazy, letting the archive be piped into a workspace
        without ever being held whole.
        """
        import urllib.parse

        url = (
            f"{self.base_url}{DOWNLOAD_PREFIX}/"
            f"{urllib.parse.quote(card_id, safe='')}/download"
        )
        client = self._http()
        stack = AsyncExitStack()
        try:
            response = await stack.enter_async_context(
                client.stream(
                    "GET",
                    url,
                    headers=self._headers(await self._cookie()),
                ),
            )
            if response.status_code == 404:
                raise KeyError(card_id)
            if response.status_code >= 400:
                body = await response.aread()
                raise HubError(
                    self.hub_id,
                    response.status_code,
                    body.decode("utf-8", errors="replace"),
                )
            return SkillArchive("zip", self._drain(stack, response))
        except Exception:
            await stack.aclose()
            raise

    async def _drain(
        self,
        stack: AsyncExitStack,
        response: Any,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> AsyncIterator[bytes]:
        """Yield the archive bytes, closing the stream when done."""
        try:
            async for chunk in response.aiter_bytes(chunk_size):
                yield chunk
        finally:
            await stack.aclose()
