# -*- coding: utf-8 -*-
"""The GitHub MCP Registry provider.

A thin async client around GitHub's MCP registry
(``https://api.mcp.github.com``), exposing its servers through the
:class:`~agentscope.app.hub._mcp._base.MCPHubBase` interface.

.. note:: The registry is public and needs no credentials. A GitHub token
    only raises the rate limit.

.. note:: A registry entry describes how to *reach* a server either as a
    ``remote`` (an HTTP endpoint) or as a ``package`` (something to run
    locally). Around a third of the catalog names a package without
    saying how to run it — no ``runtime_hint`` and no ``registry_name`` —
    and those entries are skipped rather than listed as cards that
    cannot install. See :meth:`GitHubMCPHub.list_mcps`.
"""
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

from ._base import MCPHubBase
from ._card import MCPCard, MCPHubPage
from .._error import HubError

if TYPE_CHECKING:
    import httpx

DEFAULT_BASE_URL = "https://api.mcp.github.com"

# The registry writes secret placeholders as ``{TOKEN}``; the renderer
# uses ``string.Template``, which wants ``${TOKEN}``.
_PLACEHOLDER = re.compile(r"(?<!\$)\{([A-Za-z_][A-Za-z0-9_]*)\}")

# How to run a package, by the registry's ``runtime_hint``. Anything not
# listed here cannot be turned into a command, so the entry is skipped.
_RUNTIMES: dict[str, list[str]] = {
    "npx": ["-y"],
    "uvx": [],
    "uv": ["tool", "run"],
    "docker": ["run", "-i", "--rm"],
}


def _substitute(value: Any) -> Any:
    """Rewrite ``{VAR}`` placeholders to the ``${VAR}`` the renderer wants.

    Args:
        value (`Any`):
            A string, list or dict from the registry payload.

    Returns:
        `Any`:
            The same structure with placeholders rewritten.
    """
    if isinstance(value, str):
        return _PLACEHOLDER.sub(r"${\1}", value)
    if isinstance(value, list):
        return [_substitute(item) for item in value]
    if isinstance(value, dict):
        return {key: _substitute(item) for key, item in value.items()}
    return value


def _input_property(name: str, spec: dict) -> dict:
    """Convert one registry variable definition to JSON Schema."""
    prop: dict = {
        "type": "string",
        "title": name,
        "description": spec.get("description") or "",
    }
    if spec.get("is_secret"):
        prop["writeOnly"] = True
        prop["format"] = "password"
    if spec.get("choices"):
        prop["enum"] = [str(choice) for choice in spec["choices"]]
    if spec.get("default") is not None:
        prop["default"] = str(spec["default"])
    return prop


def _inputs_schema(inputs: dict[str, dict]) -> dict:
    """Build the install form schema for registry variables."""
    if not inputs:
        return {}

    schema: dict = {
        "type": "object",
        "properties": {
            name: _input_property(name, spec) for name, spec in inputs.items()
        },
    }
    required = sorted(
        name for name, spec in inputs.items() if spec.get("is_required")
    )
    if required:
        schema["required"] = required
    return schema


class GitHubMCPHub(MCPHubBase):
    """An MCP hub backed by GitHub's MCP registry.

    .. code-block:: python

        hub = GitHubMCPHub()
        page = await hub.list_mcps(user_id="alice", q="markdown")
    """

    def __init__(
        self,
        hub_id: str = "github",
        display_name: str = "GitHub MCP Registry",
        description: str = "MCP servers published to GitHub's registry.",
        icon_url: str
        | None = "https://avatars.githubusercontent.com/u/9919?s=64",
        *,
        base_url: str = DEFAULT_BASE_URL,
        api_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the GitHub MCP registry provider.

        Args:
            hub_id (`str`, defaults to `"github"`):
                The stable identifier addressing this hub in the routes.
            display_name (`str`, defaults to `"GitHub MCP Registry"`):
                The user-facing hub name.
            description (`str`, optional):
                The user-facing hub description.
            icon_url (`str | None`, optional):
                The hub's icon, defaulting to GitHub's avatar.
            base_url (`str`, defaults to `"https://api.mcp.github.com"`):
                The base URL of the registry.
            api_token (`str | None`, optional):
                A GitHub token. The registry is public; a token only
                raises the rate limit.
            timeout (`float`, defaults to `30.0`):
                The per-request timeout in seconds.
        """
        super().__init__(hub_id, display_name, description, icon_url)
        self.base_url = base_url.rstrip("/")
        self.api_token = api_token
        self.timeout = timeout
        self._client: "httpx.AsyncClient | None" = None

    async def __aenter__(self) -> "GitHubMCPHub":
        """Open the HTTP client shared by every request.

        Returns:
            `GitHubMCPHub`:
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

    async def _request(
        self,
        path: str,
        params: dict | None = None,
    ) -> "httpx.Response":
        """Send a GET request to the registry.

        Args:
            path (`str`):
                The request path under :attr:`base_url`.
            params (`dict | None`, optional):
                The query parameters.

        Returns:
            `httpx.Response`:
                The successful response.

        Raises:
            `HubError`:
                When the registry returns a non-success status code.
        """
        import httpx

        headers = {"Accept": "application/json"}
        if self.api_token:
            headers["Authorization"] = f"Bearer {self.api_token}"

        # Opened lazily as well as on __aenter__, so a hub used outside
        # the app still works.
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        response = await self._client.get(
            f"{self.base_url}{path}",
            params=params,
            headers=headers,
        )
        if response.status_code >= 400:
            raise HubError(
                self.hub_id,
                response.status_code,
                response.text,
            )
        return response

    @staticmethod
    def _from_remote(remote: dict) -> tuple[dict, dict]:
        """Build an HTTP config plus its inputs from a ``remotes`` entry.

        Args:
            remote (`dict`):
                One entry of the server's ``remotes`` list.

        Returns:
            `tuple[dict, dict]`:
                The config template and registry definitions of the
                install inputs it references.
        """
        inputs: dict[str, dict] = {}
        headers = {}
        for header in remote.get("headers") or []:
            value = header.get("value") or ""
            # The registry omits the header name when the value already
            # spells out the scheme, which in practice means bearer auth.
            key = header.get("name") or "Authorization"
            headers[key] = _substitute(value)
            for match in _PLACEHOLDER.finditer(value):
                inputs[match.group(1)] = {
                    "description": header.get("description") or "",
                    "is_required": True,
                    "is_secret": header.get("is_secret", True),
                }

        config: dict = {"type": "http_mcp", "url": remote["url"]}
        if headers:
            config["headers"] = headers
        return config, inputs

    @staticmethod
    def _from_package(package: dict) -> tuple[dict, dict] | None:
        """Build a stdio config plus its inputs from a ``packages`` entry.

        Args:
            package (`dict`):
                One entry of the server's ``packages`` list.

        Returns:
            `tuple[dict, dict] | None`:
                The config template and registry definitions of its
                install inputs, or ``None`` when the registry does not
                say how to run the package.
        """
        runtime = package.get("runtime_hint")
        if runtime not in _RUNTIMES:
            return None

        name = package.get("name")
        if not name:
            return None
        version = package.get("version")
        spec = f"{name}@{version}" if version and runtime == "npx" else name

        env = {}
        inputs: dict[str, dict] = {}
        for variable in package.get("environment_variables") or []:
            key = variable.get("name")
            if not key:
                continue

            nested = variable.get("variables")
            value = variable.get("value")
            if isinstance(nested, dict):
                env[key] = str(_substitute(value or ""))
                inputs.update(
                    {
                        input_name: input_spec
                        for input_name, input_spec in nested.items()
                        if isinstance(input_spec, dict)
                    },
                )
            elif value is not None:
                env[key] = str(value)
            else:
                env[key] = f"${{{key}}}"
                inputs[key] = variable

        config: dict = {
            "type": "stdio_mcp",
            "command": runtime,
            "args": [*_RUNTIMES[runtime], spec],
        }
        if env:
            config["env"] = env
        return config, inputs

    def _to_card(self, entry: dict, readme: bool = False) -> MCPCard | None:
        """Build a card from one registry entry.

        Args:
            entry (`dict`):
                One record of the ``servers`` list.
            readme (`bool`, defaults to `False`):
                Whether to carry the README. Left out when listing —
                they run to tens of kilobytes each, so a page of twenty
                would be megabytes of text nothing on screen shows.

        Returns:
            `MCPCard | None`:
                The card, or ``None`` when the entry describes no way to
                reach the server.
        """
        server = entry.get("server") or {}
        github = entry.get("x-github") or {}

        built: tuple[dict, dict] | None = None
        for remote in server.get("remotes") or []:
            if remote.get("url"):
                built = self._from_remote(remote)
                break
        if built is None:
            for package in server.get("packages") or []:
                built = self._from_package(package)
                if built is not None:
                    break
        if built is None:
            return None

        config, inputs = built
        inputs_schema = _inputs_schema(inputs)

        name = server.get("name") or ""
        owner = name.rsplit("/", 1)[0] if "/" in name else None
        repository = server.get("repository") or {}

        # Client names reach the model as ``mcp__{name}__{tool}`` and so
        # must match [a-zA-Z0-9_-]+, while registry names look like
        # ``io.github.upstash/context7``. Keep the last segment.
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.rsplit("/", 1)[-1])

        updated_at = None
        stamp = server.get("updated_at")
        if stamp:
            try:
                updated_at = datetime.fromisoformat(
                    stamp.replace("Z", "+00:00"),
                ).timestamp()
            except ValueError:
                pass

        return MCPCard(
            hub_id=self.hub_id,
            id=server["id"],
            name=slug.strip("-") or "mcp",
            display_name=github.get("display_name") or name,
            description=server.get("description") or "",
            tags=[github["primary_language"]]
            if github.get("primary_language")
            else [],
            version=(server.get("version_detail") or {}).get("version"),
            updated_at=updated_at,
            author=github.get("name_with_owner", "").rsplit("/", 1)[0]
            or owner,
            icon_url=github.get("preferred_image")
            or github.get("owner_avatar_url"),
            url=repository.get("url"),
            # The registry mirrors the repository README on both blocks;
            # they agree, so either will do.
            readme=(repository.get("readme") or github.get("readme"))
            if readme
            else None,
            # STDIO servers must be stateful — the process is the session.
            is_stateful=config["type"] == "stdio_mcp",
            auth="inputs" if inputs else "none",
            inputs_schema=inputs_schema,
            config_template=config,  # type: ignore[arg-type]
        )

    async def list_mcps(
        self,
        user_id: str,
        q: str | None = None,
        cursor: str | None = None,
        limit: int = 20,
    ) -> MCPHubPage:
        """Browse the registry, one cursor page at a time.

        ``q`` is applied here rather than upstream: the registry accepts
        no search parameter, so a page is fetched and then filtered. A
        page whose entries all filter out still carries a cursor, which
        the caller is expected to follow.

        Args:
            user_id (`str`):
                The user identifier. Unused by the public registry, kept
                for interface compatibility.
            q (`str | None`, optional):
                A keyword matched against the name and description.
            cursor (`str | None`, optional):
                The opaque cursor from a previous page.
            limit (`int`, defaults to `20`):
                The maximum number of entries to fetch.

        Returns:
            `MCPHubPage`:
                The cards of this page plus the next cursor.

        Raises:
            `HubError`:
                When the registry returns a non-success status code.
        """
        params: dict = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor

        payload = (await self._request("/v0/servers", params)).json()

        needle = (q or "").lower()
        cards = []
        for entry in payload.get("servers") or []:
            card = self._to_card(entry)
            if card is None:
                continue
            haystack = f"{card.name} {card.display_name} {card.description}"
            if needle and needle not in haystack.lower():
                continue
            cards.append(card)

        return MCPHubPage(
            cards=cards,
            next_cursor=(payload.get("metadata") or {}).get("next_cursor"),
        )

    async def get_mcp(self, user_id: str, card_id: str) -> MCPCard:
        """Fetch one registry entry.

        Args:
            user_id (`str`):
                The user identifier. Unused by the public registry, kept
                for interface compatibility.
            card_id (`str`):
                The registry id of the server.

        Returns:
            `MCPCard`:
                The card describing this server, README included.

        Raises:
            `KeyError`:
                When the registry has no such server, or describes no way
                to reach it.
            `HubError`:
                When the registry returns any other failure.
        """
        try:
            response = await self._request(f"/v0/servers/{card_id}")
        except HubError as e:
            if e.status_code == 404:
                raise KeyError(card_id) from e
            raise

        card = self._to_card(response.json(), readme=True)
        if card is None:
            raise KeyError(card_id)
        return card
