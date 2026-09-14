# -*- coding: utf-8 -*-
# pylint: disable=protected-access
"""Tests for OAuth 2.0 authentication of HTTP MCP clients."""
import asyncio
import base64
import time
import unittest
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator, Callable
from unittest.async_case import IsolatedAsyncioTestCase
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

from agentscope.mcp import (
    HttpMCPConfig,
    MCPClient,
    OAuthClientConfig,
    OAuthToken,
    StdioMCPConfig,
)
from agentscope.mcp._oauth import (
    TokenAuth,
    _TokenEndpointFetcher,
    build_token_auth,
)

_MCP_URL = "https://mcp.example.com/mcp"
_TOKEN_URL = "https://auth.example.com/oauth/token"


class _TokenEndpoint:
    """A token endpoint that records what was posted to it."""

    def __init__(self, **payload: Any) -> None:
        """Initialize the endpoint with the payload it answers with.

        Args:
            payload (`Any`):
                The JSON body of the token response. ``access_token`` is
                suffixed with the request count, so successive tokens are
                distinguishable.
        """
        self.payload = payload
        self.requests: list[httpx.Request] = []
        self.forms: list[dict[str, str]] = []

    @property
    def transport(self) -> httpx.MockTransport:
        """A transport serving this endpoint."""
        return httpx.MockTransport(self)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer a token request."""
        self.requests.append(request)
        self.forms.append(
            {
                key: values[0]
                for key, values in parse_qs(
                    request.content.decode(),
                ).items()
            },
        )
        payload = dict(self.payload)
        issued = len(self.requests)
        payload["access_token"] = f"{payload['access_token']}-{issued}"
        return httpx.Response(200, json=payload)


def _echo_authorization(request: httpx.Request) -> httpx.Response:
    """Answer an MCP request with the credential it carried."""
    return httpx.Response(
        200,
        json={"authorization": request.headers.get("Authorization")},
    )


async def _send(auth: httpx.Auth, handler: Callable) -> str:
    """Send one request through ``auth`` and report the credential seen."""
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        auth=auth,
    ) as client:
        response = await client.get(_MCP_URL)
    return response.json()["authorization"]


class MCPOAuthTokenEndpointTest(IsolatedAsyncioTestCase):
    """The declarative OAuth config talks to a token endpoint correctly."""

    async def test_client_credentials_request(self) -> None:
        """The grant, scope and client credentials go in the form body."""
        endpoint = _TokenEndpoint(
            access_token="at",
            token_type="bearer",
            expires_in=3600,
        )
        fetcher = _TokenEndpointFetcher(
            OAuthClientConfig(
                token_url=_TOKEN_URL,
                client_id="cid",
                client_secret="secret",
                scope="read write",
                extra_params={"audience": "mcp"},
            ),
            transport=endpoint.transport,
        )

        token = await fetcher()

        self.assertDictEqual(
            {
                "url": str(endpoint.requests[0].url),
                "form": endpoint.forms[0],
                "authorization": endpoint.requests[0].headers.get(
                    "Authorization",
                ),
                "access_token": token.access_token,
                # `bearer` is normalised, so the header reads as the
                # scheme HTTP expects.
                "token_type": token.token_type,
                "expires_within_the_hour": token.expires_at is not None
                and token.expires_at - time.time() <= 3600,
            },
            {
                "url": _TOKEN_URL,
                "form": {
                    "grant_type": "client_credentials",
                    "scope": "read write",
                    "client_id": "cid",
                    "client_secret": "secret",
                    "audience": "mcp",
                },
                "authorization": None,
                "access_token": "at-1",
                "token_type": "Bearer",
                "expires_within_the_hour": True,
            },
        )

    async def test_client_secret_basic_keeps_the_secret_out_of_the_body(
        self,
    ) -> None:
        """`client_secret_basic` sends the credentials as HTTP Basic."""
        endpoint = _TokenEndpoint(access_token="at", expires_in=60)
        fetcher = _TokenEndpointFetcher(
            OAuthClientConfig(
                token_url=_TOKEN_URL,
                client_id="cid",
                client_secret="secret",
                client_auth="client_secret_basic",
            ),
            transport=endpoint.transport,
        )

        await fetcher()

        expected = base64.b64encode(b"cid:secret").decode()
        self.assertDictEqual(
            {
                "form": endpoint.forms[0],
                "authorization": endpoint.requests[0].headers["Authorization"],
            },
            {
                "form": {"grant_type": "client_credentials"},
                "authorization": f"Basic {expected}",
            },
        )

    async def test_rotated_refresh_token_is_used_next_time(self) -> None:
        """A single-use refresh token is replaced by the rotated one."""
        endpoint = _TokenEndpoint(
            access_token="at",
            expires_in=3600,
            refresh_token="rotated",
        )
        fetcher = _TokenEndpointFetcher(
            OAuthClientConfig(
                grant_type="refresh_token",
                token_url=_TOKEN_URL,
                client_id="cid",
                refresh_token="original",
            ),
            transport=endpoint.transport,
        )

        await fetcher()
        await fetcher()

        self.assertListEqual(
            [form["refresh_token"] for form in endpoint.forms],
            ["original", "rotated"],
        )

    async def test_refresh_token_grant_requires_a_refresh_token(self) -> None:
        """The config rejects a grant it cannot perform."""
        with self.assertRaises(ValueError):
            OAuthClientConfig(
                grant_type="refresh_token",
                token_url=_TOKEN_URL,
                client_id="cid",
            )

    async def test_token_endpoint_failure_reports_only_the_oauth_error(
        self,
    ) -> None:
        """The error names the OAuth fields, not the credentials sent."""

        def handler(request: httpx.Request) -> httpx.Response:
            del request
            return httpx.Response(
                401,
                json={
                    "error": "invalid_client",
                    "error_description": "bad client",
                    "internal": "secret-echo",
                },
            )

        fetcher = _TokenEndpointFetcher(
            OAuthClientConfig(
                token_url=_TOKEN_URL,
                client_id="cid",
                client_secret="secret",
            ),
            transport=httpx.MockTransport(handler),
        )

        with self.assertRaises(RuntimeError) as ctx:
            await fetcher()

        message = str(ctx.exception)
        self.assertDictEqual(
            {
                "names_the_error": "invalid_client" in message,
                "names_the_status": "401" in message,
                "leaks_the_secret": "secret" in message,
                "echoes_unknown_fields": "secret-echo" in message,
            },
            {
                "names_the_error": True,
                "names_the_status": True,
                "leaks_the_secret": False,
                "echoes_unknown_fields": False,
            },
        )

    async def test_the_assumed_lifetime_covers_a_missing_expires_in(
        self,
    ) -> None:
        """A response without `expires_in`, or with a stringly typed one,
        still yields a token the client knows when to refresh."""
        lifetimes = []
        payloads: list[dict[str, Any]] = [
            {},
            {"expires_in": "7200"},
            {"expires_in": None},
        ]
        for expires_in in payloads:
            endpoint = _TokenEndpoint(access_token="at", **expires_in)
            fetcher = _TokenEndpointFetcher(
                OAuthClientConfig(
                    token_url=_TOKEN_URL,
                    client_id="cid",
                    default_expires_in=600.0,
                ),
                transport=endpoint.transport,
            )
            token = await fetcher()
            lifetimes.append(round(token.expires_at - time.time()))

        self.assertListEqual(lifetimes, [600, 7200, 600])

    async def test_response_without_an_access_token_is_rejected(self) -> None:
        """A 200 that carries no token is an error, not a blank header."""
        fetcher = _TokenEndpointFetcher(
            OAuthClientConfig(token_url=_TOKEN_URL, client_id="cid"),
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"ok": True}),
            ),
        )

        with self.assertRaises(RuntimeError):
            await fetcher()


class MCPTokenAuthTest(IsolatedAsyncioTestCase):
    """Tokens are cached, refreshed ahead of expiry, and retried on 401."""

    def setUp(self) -> None:
        """Count how often the provider is asked for a token."""
        self.calls = 0

    def _provider(self, lifetime: float | None) -> Callable:
        """A provider issuing a numbered token with ``lifetime`` seconds."""

        async def provide() -> OAuthToken:
            self.calls += 1
            return OAuthToken(
                access_token=f"token-{self.calls}",
                expires_at=(
                    None if lifetime is None else time.time() + lifetime
                ),
            )

        return provide

    async def test_a_fresh_token_is_reused(self) -> None:
        """A token that is not near expiry is fetched once."""
        auth = TokenAuth(self._provider(3600), leeway=60)

        seen = [
            await _send(auth, _echo_authorization),
            await _send(auth, _echo_authorization),
        ]

        self.assertDictEqual(
            {"seen": seen, "calls": self.calls},
            {"seen": ["Bearer token-1", "Bearer token-1"], "calls": 1},
        )

    async def test_a_token_near_expiry_is_refreshed_before_it_is_sent(
        self,
    ) -> None:
        """The leeway refreshes ahead of expiry rather than after a
        failure, which is what a 2-hour token needs to survive."""
        auth = TokenAuth(self._provider(10), leeway=60)

        seen = [
            await _send(auth, _echo_authorization),
            await _send(auth, _echo_authorization),
        ]

        self.assertDictEqual(
            {"seen": seen, "calls": self.calls},
            {"seen": ["Bearer token-1", "Bearer token-2"], "calls": 2},
        )

    async def test_a_token_of_unknown_lifetime_is_kept(self) -> None:
        """Without an expiry there is nothing to pre-empt, so it is kept
        until the server objects."""
        auth = TokenAuth(self._provider(None))

        seen = [
            await _send(auth, _echo_authorization),
            await _send(auth, _echo_authorization),
        ]

        self.assertDictEqual(
            {"seen": seen, "calls": self.calls},
            {"seen": ["Bearer token-1", "Bearer token-1"], "calls": 1},
        )

    async def test_a_bare_string_provider_is_accepted(self) -> None:
        """A custom provider may return just the token."""

        async def provide() -> str:
            return "opaque"

        self.assertEqual(
            await _send(TokenAuth(provide), _echo_authorization),
            "Bearer opaque",
        )

    async def test_a_provider_returning_the_wrong_type_is_rejected(
        self,
    ) -> None:
        """A misbehaving provider fails loudly instead of sending junk."""

        async def provide() -> Any:
            return 42

        with self.assertRaises(TypeError):
            await _send(TokenAuth(provide), _echo_authorization)

    async def test_a_rejected_token_is_refreshed_and_the_call_retried(
        self,
    ) -> None:
        """A token revoked before its stated expiry costs one 401, not a
        failed tool call."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["Authorization"])
            if len(seen) == 1:
                return httpx.Response(401, json={"error": "expired"})
            return _echo_authorization(request)

        auth = TokenAuth(self._provider(3600), leeway=60)
        credential = await _send(auth, handler)

        self.assertDictEqual(
            {"seen": seen, "credential": credential, "calls": self.calls},
            {
                "seen": ["Bearer token-1", "Bearer token-2"],
                "credential": "Bearer token-2",
                "calls": 2,
            },
        )

    async def test_a_second_401_is_not_retried_again(self) -> None:
        """The retry is bounded, so a server answering 401 to everything
        surfaces the 401 instead of looping on the token endpoint."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["Authorization"])
            return httpx.Response(401, json={"error": "nope"})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            auth=TokenAuth(self._provider(3600)),
        ) as client:
            response = await client.get(_MCP_URL)

        self.assertDictEqual(
            {"attempts": len(seen), "status": response.status_code},
            {"attempts": 2, "status": 401},
        )

    async def test_concurrent_requests_share_one_refresh(self) -> None:
        """Parallel tool calls do not each hit the token endpoint."""
        slow_calls = 0

        async def provide() -> OAuthToken:
            nonlocal slow_calls
            slow_calls += 1
            await asyncio.sleep(0.05)
            return OAuthToken(
                access_token=f"token-{slow_calls}",
                expires_at=time.time() + 3600,
            )

        auth = TokenAuth(provide, leeway=60)
        seen = await asyncio.gather(
            *(_send(auth, _echo_authorization) for _ in range(5)),
        )

        self.assertDictEqual(
            {"seen": sorted(set(seen)), "calls": slow_calls},
            {"seen": ["Bearer token-1"], "calls": 1},
        )

    def test_refreshing_survives_a_new_event_loop(self) -> None:
        """A client reopened under a fresh `asyncio.run` refreshes there
        too, rather than dying on a lock bound to the closed loop."""

        async def provide() -> OAuthToken:
            """A refresh slow enough for the second request to queue."""
            self.calls += 1
            await asyncio.sleep(0.01)
            return OAuthToken(
                access_token=f"token-{self.calls}",
                expires_at=time.time() + 3600,
            )

        auth = TokenAuth(provide, leeway=60)

        async def two_at_once() -> list[str]:
            """Contend for the refresh, which is what binds the lock."""
            return list(
                await asyncio.gather(
                    *(_send(auth, _echo_authorization) for _ in range(2)),
                ),
            )

        first = asyncio.run(two_at_once())
        # The token expires while nothing is running, so the next round
        # refreshes -- on a loop the first one never saw.
        auth._token.expires_at = time.time()
        second = asyncio.run(two_at_once())

        self.assertDictEqual(
            {
                "first": sorted(set(first)),
                "second": sorted(set(second)),
                "calls": self.calls,
            },
            {
                # Each round refreshes once and both requests share it.
                "first": ["Bearer token-1"],
                "second": ["Bearer token-2"],
                "calls": 2,
            },
        )

    def test_the_synchronous_flow_is_refused(self) -> None:
        """There is no way to await a token from a sync client."""
        with self.assertRaises(RuntimeError):
            list(
                TokenAuth(self._provider(3600)).sync_auth_flow(
                    httpx.Request("GET", _MCP_URL),
                ),
            )


class MCPClientOAuthWiringTest(IsolatedAsyncioTestCase):
    """The client installs the credential on the transport it opens."""

    def setUp(self) -> None:
        """Replace the transport with one exposing its HTTP client."""
        self.seen: dict[str, Any] = {}

        @asynccontextmanager
        async def fake_transport(
            url: str,
            *,
            http_client: httpx.AsyncClient,
        ) -> AsyncGenerator[tuple[object, object, object], None]:
            self.seen.update(url=url, http_client=http_client)
            yield object(), object(), object()

        patcher = patch(
            "agentscope.mcp._mcp_client.streamable_http_client",
            fake_transport,
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_oauth_reaches_the_streamable_http_client(self) -> None:
        """Every request the transport makes, the GET stream included,
        goes through the auth handler."""
        client = MCPClient(
            name="oauth_wiring",
            is_stateful=True,
            mcp_config=HttpMCPConfig(
                url=_MCP_URL,
                oauth=OAuthClientConfig(
                    token_url=_TOKEN_URL,
                    client_id="cid",
                    client_secret="secret",
                ),
            ),
        )

        async with client._create_http_client():
            installed = self.seen["http_client"].auth

        self.assertIsInstance(installed, TokenAuth)

    async def test_a_client_without_oauth_stays_unauthenticated(
        self,
    ) -> None:
        """Existing static-header configs are untouched."""
        client = MCPClient(
            name="no_oauth",
            is_stateful=True,
            mcp_config=HttpMCPConfig(
                url=_MCP_URL,
                headers={"Authorization": "Bearer static"},
            ),
        )

        async with client._create_http_client():
            http_client = self.seen["http_client"]
            request = http_client.build_request("POST", _MCP_URL)
            self.assertDictEqual(
                {
                    "auth": http_client.auth,
                    "authorization": request.headers["Authorization"],
                },
                {"auth": None, "authorization": "Bearer static"},
            )

    async def test_runtime_headers_cannot_shadow_the_oauth_header(
        self,
    ) -> None:
        """Setting the header the credential owns would silently do
        nothing, so it is refused."""
        client = MCPClient(
            name="runtime_vs_oauth",
            is_stateful=True,
            mcp_config=HttpMCPConfig(
                url=_MCP_URL,
                oauth=OAuthClientConfig(
                    token_url=_TOKEN_URL,
                    client_id="cid",
                ),
            ),
        )

        with self.assertRaises(ValueError):
            await client.set_runtime_headers({"authorization": "Bearer x"})

        # A header the credential does not own is still allowed.
        await client.set_runtime_headers({"X-Tenant": "acme"})
        self.assertDictEqual(client._runtime_headers, {"X-Tenant": "acme"})


class MCPClientTokenProviderTest(unittest.TestCase):
    """The custom provider is live state, and excludes the config path."""

    @staticmethod
    async def _provide() -> str:
        """A custom token source."""
        return "custom"

    def _client(self, **kwargs: Any) -> MCPClient:
        """An HTTP MCP client with the given overrides."""
        return MCPClient(
            name="provider",
            is_stateful=False,
            mcp_config=HttpMCPConfig(url=_MCP_URL),
            **kwargs,
        )

    def test_a_callable_provider_is_wrapped(self) -> None:
        """A plain async callable becomes a caching auth handler."""
        client = self._client(token_provider=self._provide)
        self.assertIsInstance(client._auth, TokenAuth)

    def test_an_httpx_auth_is_used_as_is(self) -> None:
        """A provider with its own exchange, such as the MCP SDK's OAuth
        provider, is installed unchanged."""
        auth = httpx.BasicAuth("user", "pass")
        self.assertIs(self._client(token_provider=auth)._auth, auth)

    def test_the_provider_is_not_serialized(self) -> None:
        """It is instance state, so it never reaches persisted config."""
        dumped = self._client(token_provider=self._provide).model_dump()
        self.assertNotIn("token_provider", dumped)

    def test_a_provider_and_a_config_cannot_both_be_set(self) -> None:
        """One of them would be dead configuration."""
        with self.assertRaises(ValueError):
            MCPClient(
                name="both",
                is_stateful=False,
                mcp_config=HttpMCPConfig(
                    url=_MCP_URL,
                    oauth=OAuthClientConfig(
                        token_url=_TOKEN_URL,
                        client_id="cid",
                    ),
                ),
                token_provider=self._provide,
            )

    def test_a_non_callable_provider_is_rejected(self) -> None:
        """A config mistake fails at construction, not mid-request."""
        with self.assertRaises(ValueError):
            self._client(token_provider="a token string")

    def test_stdio_rejects_a_provider(self) -> None:
        """There is no HTTP request to authenticate."""
        with self.assertRaises(ValueError):
            MCPClient(
                name="stdio",
                is_stateful=True,
                mcp_config=StdioMCPConfig(command="mcp-server"),
                token_provider=self._provide,
            )

    def test_no_credential_source_installs_no_handler(self) -> None:
        """The default stays exactly as it was."""
        self.assertIsNone(build_token_auth(None, None))
