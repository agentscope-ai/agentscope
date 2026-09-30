# -*- coding: utf-8 -*-
"""OAuth 2.0 authentication for the HTTP MCP transports."""
import asyncio
import time
from typing import Any, AsyncGenerator, Awaitable, Callable, Literal

import httpx
from pydantic import BaseModel, Field

from .._logging import logger


class OAuthToken(BaseModel):
    """An access token together with what is known about its lifetime.

    A token provider returns this when it knows when the token expires, so
    the client can refresh ahead of time instead of waiting for the server
    to reject a request.
    """

    access_token: str = Field(
        title="Access Token",
        description="The bearer token to send to the MCP server.",
    )

    token_type: str = Field(
        default="Bearer",
        title="Token Type",
        description="The token type, used as the credential's scheme.",
    )

    expires_at: float | None = Field(
        default=None,
        title="Expires At",
        description=(
            "The UNIX timestamp the token expires at. `None` means the "
            "lifetime is unknown, so the token is kept until the server "
            "rejects it."
        ),
    )

    refresh_token: str | None = Field(
        default=None,
        title="Refresh Token",
        description=(
            "The refresh token issued alongside the access token, if the "
            "provider rotates it."
        ),
    )

    def is_fresh(self, leeway: float = 0.0) -> bool:
        """Whether the token is still usable ``leeway`` seconds from now.

        Args:
            leeway (`float`, defaults to `0.0`):
                How far ahead to look, so a token that is about to expire
                is refreshed before it is sent rather than after it fails.

        Returns:
            `bool`:
                `True` when the token has no known expiry, or expires
                later than ``leeway`` seconds from now.
        """
        if self.expires_at is None:
            return True
        return time.time() + leeway < self.expires_at


TokenProvider = Callable[[], Awaitable["OAuthToken | str"]]
"""An async callable returning the credential for the next request.

Returning an :class:`OAuthToken` with ``expires_at`` set engages the cache:
the callable is not called again until the token is within the configured
leeway of expiring. Returning a bare `str`, or a token without
``expires_at``, means the lifetime is unknown, so the value is cached until
the server answers 401 and the callable is asked for a new one.
"""


class OAuthClientConfig(BaseModel):
    """OAuth 2.0 client configuration for an HTTP MCP server.

    This covers the two grants that need no user interaction at request
    time: ``client_credentials``, and ``refresh_token`` for an
    authorization code flow whose refresh token was obtained out of band.
    Tokens are fetched on first use, cached, and refreshed
    :attr:`refresh_leeway` seconds before they expire.

    Providers whose token endpoint is not standard are served by
    :attr:`extra_params`, or, when that is not enough, by passing a
    ``token_provider`` callable to :class:`~agentscope.mcp.MCPClient`.

    Example:

    .. code-block:: python

        MCPClient(
            name="feishu",
            is_stateful=False,
            mcp_config=HttpMCPConfig(
                url="https://example.com/mcp",
                oauth=OAuthClientConfig(
                    grant_type="refresh_token",
                    token_url="https://example.com/oauth/token",
                    client_id="cli_xxx",
                    client_secret="***",
                    refresh_token="***",
                ),
            ),
        )
    """

    grant_type: Literal["client_credentials", "refresh_token"] = Field(
        default="client_credentials",
        title="Grant Type",
        description="The OAuth 2.0 grant used to obtain access tokens.",
    )

    token_url: str = Field(
        title="Token URL",
        description="The token endpoint to request access tokens from.",
    )

    client_id: str | None = Field(
        default=None,
        title="Client ID",
        description="The OAuth client identifier.",
    )

    client_secret: str | None = Field(
        default=None,
        title="Client Secret",
        description="The OAuth client secret.",
    )

    refresh_token: str | None = Field(
        default=None,
        title="Refresh Token",
        description=(
            "The initial refresh token, required by the `refresh_token` "
            "grant. A rotated one returned by the token endpoint replaces "
            "it for subsequent refreshes, in memory only."
        ),
    )

    scope: str | None = Field(
        default=None,
        title="Scope",
        description="The space-separated scopes to request.",
    )

    client_auth: Literal["client_secret_post", "client_secret_basic"] = Field(
        default="client_secret_post",
        title="Client Authentication",
        description=(
            "How the client credentials reach the token endpoint: in the "
            "form body, or as HTTP Basic credentials."
        ),
    )

    extra_params: dict[str, str] | None = Field(
        default=None,
        title="Extra Parameters",
        description=(
            "Additional form fields to send to the token endpoint, for "
            "providers that require them (`audience`, `resource`, ...). "
            "They override the fields derived from this config."
        ),
    )

    header_name: str = Field(
        default="Authorization",
        title="Header Name",
        description="The request header the credential is sent in.",
    )

    refresh_leeway: float = Field(
        default=60.0,
        ge=0.0,
        title="Refresh Leeway",
        description=(
            "How many seconds before expiry a token is refreshed, so a "
            "request is never sent with a token that is about to expire."
        ),
    )

    default_expires_in: float | None = Field(
        default=3600.0,
        title="Default Expires In",
        description=(
            "The lifetime assumed when the token endpoint omits "
            "`expires_in`. `None` keeps the token until it is rejected."
        ),
    )

    timeout: float = Field(
        default=10.0,
        title="Timeout",
        description="The token request timeout in seconds.",
    )

    def model_post_init(self, __context: Any) -> None:
        """Validate the grant's required fields."""
        if self.grant_type == "refresh_token" and not self.refresh_token:
            raise ValueError(
                "The 'refresh_token' grant requires a refresh_token.",
            )


class _TokenEndpointFetcher:
    """Fetches tokens from an OAuth 2.0 token endpoint.

    A rotated refresh token is kept on the instance rather than written
    back to the config, so the credential that is persisted stays the one
    the user configured.
    """

    def __init__(
        self,
        config: OAuthClientConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Initialize the fetcher.

        Args:
            config (`OAuthClientConfig`):
                The OAuth client configuration.
            transport (`httpx.AsyncBaseTransport | None`, defaults to \
            `None`):
                The transport to send token requests over. Only used by
                the tests; `None` means the httpx default.
        """
        self._config = config
        self._refresh_token = config.refresh_token
        self._transport = transport

    def _request_kwargs(self) -> dict[str, Any]:
        """The form body and client authentication for a token request."""
        config = self._config
        data: dict[str, str] = {"grant_type": config.grant_type}
        if config.scope:
            data["scope"] = config.scope
        if config.grant_type == "refresh_token":
            # A rotated token, if the endpoint issued one, else the
            # configured one.
            data["refresh_token"] = self._refresh_token or ""

        auth: tuple[str, str] | None = None
        if config.client_auth == "client_secret_basic":
            auth = (config.client_id or "", config.client_secret or "")
        else:
            if config.client_id is not None:
                data["client_id"] = config.client_id
            if config.client_secret is not None:
                data["client_secret"] = config.client_secret

        if config.extra_params:
            data.update(config.extra_params)

        return {"data": data, "auth": auth}

    async def __call__(self) -> OAuthToken:
        """Request a new access token.

        Returns:
            `OAuthToken`:
                The freshly issued token.

        Raises:
            `RuntimeError`:
                The token endpoint refused the request or returned a
                response without an access token.
        """
        config = self._config
        async with httpx.AsyncClient(
            timeout=config.timeout,
            transport=self._transport,
        ) as client:
            response = await client.post(
                config.token_url,
                headers={"Accept": "application/json"},
                **self._request_kwargs(),
            )

        payload: Any = None
        try:
            payload = response.json()
        except ValueError:
            payload = None

        if response.status_code >= 400:
            raise RuntimeError(
                f"OAuth token request to {config.token_url} failed with "
                f"HTTP {response.status_code}: {_describe_error(payload)}",
            )

        if not isinstance(payload, dict) or not payload.get("access_token"):
            raise RuntimeError(
                f"OAuth token response from {config.token_url} carries no "
                "access_token.",
            )

        # Rotated refresh tokens are single use: keep the new one, or the
        # next refresh replays a token the provider has already retired.
        rotated = payload.get("refresh_token")
        if isinstance(rotated, str) and rotated:
            self._refresh_token = rotated

        expires_at = _expires_at(
            payload.get("expires_in"),
            config.default_expires_in,
        )

        token_type = payload.get("token_type") or "Bearer"
        if not isinstance(token_type, str) or token_type.lower() == "bearer":
            token_type = "Bearer"

        return OAuthToken(
            access_token=str(payload["access_token"]),
            token_type=token_type,
            expires_at=expires_at,
            refresh_token=self._refresh_token,
        )


def _expires_at(expires_in: Any, default: float | None) -> float | None:
    """When a token issued now expires.

    Args:
        expires_in (`Any`):
            The ``expires_in`` field of the token response, which some
            providers send as a string rather than a number.
        default (`float | None`):
            The lifetime to assume when the field is absent or unusable.

    Returns:
        `float | None`:
            The expiry timestamp, or `None` when the lifetime is unknown.
    """
    if isinstance(expires_in, str):
        try:
            expires_in = float(expires_in)
        except ValueError:
            expires_in = None
    if not isinstance(expires_in, (int, float)) or isinstance(
        expires_in,
        bool,
    ):
        expires_in = default
    return None if expires_in is None else time.time() + expires_in


def _describe_error(payload: Any) -> str:
    """Summarize an OAuth error response without echoing the whole body,
    which may carry the credentials that were sent."""
    if not isinstance(payload, dict):
        return "the response carried no OAuth error object"
    described = {
        key: payload[key]
        for key in ("error", "error_description", "error_uri")
        if key in payload
    }
    return str(described) if described else "no error details were returned"


class TokenAuth(httpx.Auth):
    """An :class:`httpx.Auth` that keeps one token fresh.

    Every request made by the transport passes through here, the long-lived
    GET stream of a Streamable HTTP session included, so a token refreshed
    between calls reaches the server without reconnecting. The token is
    fetched once, reused while it is fresh, refreshed
    :attr:`_leeway` seconds before it expires, and refreshed once more if
    the server still answers 401 -- which happens when a token is revoked
    early or the two clocks disagree.
    """

    def __init__(
        self,
        token_provider: TokenProvider,
        leeway: float = 60.0,
        header_name: str = "Authorization",
    ) -> None:
        """Initialize the authentication handler.

        Args:
            token_provider (`TokenProvider`):
                The async callable that obtains a credential.
            leeway (`float`, defaults to `60.0`):
                How many seconds before expiry to refresh.
            header_name (`str`, defaults to `"Authorization"`):
                The header the credential is sent in.
        """
        self._token_provider = token_provider
        self._leeway = leeway
        self._header_name = header_name
        self._token: OAuthToken | None = None
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    @property
    def header_name(self) -> str:
        """The request header the credential is sent in."""
        return self._header_name

    def sync_auth_flow(self, request: httpx.Request) -> Any:
        """Refuse the synchronous flow, which cannot await a token."""
        raise RuntimeError(
            "MCP OAuth requires an async HTTP client.",
        )

    def _guard(self) -> asyncio.Lock:
        """The lock serializing refreshes on the running event loop.

        A client may be closed and reopened on a different loop (a new
        `asyncio.run`, for one) while this handler and its cached token
        live on, so the lock is rebuilt whenever the loop changes.
        """
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    async def _credential(
        self,
        rejected: OAuthToken | None = None,
    ) -> tuple[str, OAuthToken]:
        """The header value for the next request, refreshing if needed.

        Args:
            rejected (`OAuthToken | None`, defaults to `None`):
                The token the server has just refused. It is refreshed
                even though it looks fresh, unless another request has
                already replaced it, so several requests failing together
                trigger a single refresh.

        Returns:
            `tuple[str, OAuthToken]`:
                The credential, as ``"<token type> <access token>"``, and
                the token it was built from.
        """
        async with self._guard():
            token = self._token
            if (
                token is None
                or token is rejected
                or not token.is_fresh(self._leeway)
            ):
                fetched = await self._token_provider()
                if isinstance(fetched, str):
                    fetched = OAuthToken(access_token=fetched)
                if not isinstance(fetched, OAuthToken):
                    raise TypeError(
                        "A token provider must return an OAuthToken or a "
                        f"str, but got {type(fetched).__name__}.",
                    )
                self._token = fetched
                token = fetched

            return f"{token.token_type} {token.access_token}", token

    async def async_auth_flow(
        self,
        request: httpx.Request,
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        """Attach a fresh credential, retrying once if it is rejected.

        Args:
            request (`httpx.Request`):
                The request to authenticate.

        Yields:
            `httpx.Request`:
                The authenticated request, and its retry when the first
                attempt is answered with 401.
        """
        credential, token = await self._credential()
        request.headers[self._header_name] = credential
        response = yield request

        if response.status_code != 401:
            return

        # The freshness check accepted this token but the server did not:
        # it was revoked early, or the two clocks disagree. Refresh the
        # token this request carried, so requests that failed together
        # share one refresh rather than each forcing their own.
        logger.debug("MCP OAuth token rejected with 401, refreshing once.")
        credential, _ = await self._credential(rejected=token)
        request.headers[self._header_name] = credential
        yield request


def build_token_auth(
    token_provider: Any,
    oauth: OAuthClientConfig | None,
) -> httpx.Auth | None:
    """Build the HTTP authentication handler for an MCP client.

    Args:
        token_provider (`Any`):
            The explicit provider passed to the client: an async callable
            returning a token, an :class:`httpx.Auth`, or `None`.
        oauth (`OAuthClientConfig | None`):
            The declarative OAuth configuration, if any.

    Returns:
        `httpx.Auth | None`:
            The handler to install on the HTTP client, or `None` when
            neither source is configured.

    Raises:
        `ValueError`:
            Both sources are configured, or ``token_provider`` is neither
            callable nor an :class:`httpx.Auth`.
    """
    if token_provider is None:
        if oauth is None:
            return None
        return TokenAuth(
            _TokenEndpointFetcher(oauth),
            leeway=oauth.refresh_leeway,
            header_name=oauth.header_name,
        )

    if oauth is not None:
        raise ValueError(
            "Set either token_provider or mcp_config.oauth, not both: the "
            "explicit provider would make the configured OAuth flow dead "
            "configuration.",
        )

    if isinstance(token_provider, httpx.Auth):
        return token_provider

    if callable(token_provider):
        return TokenAuth(token_provider)

    raise ValueError(
        "token_provider must be an async callable returning a token, or "
        f"an httpx.Auth, but got {type(token_provider).__name__}.",
    )
