# -*- coding: utf-8 -*-
"""Host-side client for the in-workspace MCP gateway.

Three classes live here:

* :class:`GatewayClient` — workspace-side facade over the gateway's
  ``/health`` and ``/mcps`` endpoints. Used by ``DockerWorkspace`` (and
  later ``E2BWorkspace``) for top-level operations.

* :class:`GatewayMCPClient` — an :class:`MCPClient` subclass whose
  protocol behaviour is replaced by HTTP calls to the gateway. The
  field surface is identical to ``MCPClient`` (instances are built from
  ``MCPClient.model_dump()`` data the gateway returns), so callers
  that ``model_dump()`` it round-trip cleanly. Local stdio/HTTP
  session machinery is bypassed: ``model_post_init`` is a no-op,
  ``connect`` POSTs to ``/mcps``, ``close`` DELETEs ``/mcps/{name}``,
  and ``list_tools`` / ``get_tool`` fetch / wrap upstream tools.

* :class:`GatewayMCPTool` — :class:`ToolBase` subclass whose
  ``__call__`` posts to ``/mcps/{name}/tools/{tool}`` and reconstructs
  the returned ``ToolChunk``.
"""

import contextlib
from typing import Any, AsyncIterator

import httpx
import mcp.types
from pydantic import PrivateAttr

from ..mcp import MCPClient
from ..message import ToolResultState
from ..permission import (
    PermissionBehavior,
    PermissionDecision,
)
from ..tool import ToolBase, ToolChunk


# ── tool ───────────────────────────────────────────────────────────


class GatewayMCPTool(ToolBase):
    """An MCP tool whose ``__call__`` is a single HTTP POST to the gateway.

    Mirrors :class:`agentscope.tool.MCPTool` field-by-field so the toolkit
    treats it identically (same ``name`` format, same permission policy)
    — only the call path changes.
    """

    is_mcp: bool = True
    is_state_injected: bool = False

    def __init__(
        self,
        mcp_name: str,
        tool: mcp.types.Tool,
        gateway_url: str,
        token: str,
        http: httpx.AsyncClient | None = None,
        timeout: float | None = None,
    ) -> None:
        """Build a gateway-backed MCP tool.

        The instance mirrors the field surface of
        :class:`agentscope.tool.MCPTool` (``name``, ``description``,
        ``input_schema``, ``is_read_only``, …) so the host-side toolkit
        cannot tell the difference between a local MCP tool and one that
        forwards through the in-container gateway.

        Args:
            mcp_name: Name of the upstream MCP server this tool belongs
                to. Used both for the visible ``mcp__{mcp}__{tool}``
                name and for the gateway URL path.
            tool: Raw upstream tool descriptor as returned by the
                gateway. Its ``name`` is the upstream-side identifier
                (no ``mcp__`` prefix), ``inputSchema`` is forwarded
                verbatim, and ``annotations.readOnlyHint`` drives the
                permission policy.
            gateway_url: Host-visible base URL of the gateway, e.g.
                ``http://127.0.0.1:<host_port>``. Trailing slash is
                stripped.
            token: Bearer token the host injected into the gateway's
                config; sent as ``Authorization: Bearer …`` on every
                call.
            http: Shared :class:`httpx.AsyncClient` to reuse connection
                pooling across many tool calls. When ``None`` each
                ``__call__`` creates and disposes a one-shot client.
            timeout: Per-call HTTP timeout in seconds. Only consulted
                when ``http`` is ``None`` (the shared client carries
                its own timeout).
        """
        self.mcp_name = mcp_name
        self.name = f"mcp__{mcp_name}__{tool.name}"
        self.description = tool.description or ""

        schema = dict(tool.inputSchema) if tool.inputSchema else {}
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        schema.setdefault("required", [])
        self.input_schema = schema

        self.is_concurrency_safe = False
        self.is_external_tool = False

        self.is_read_only = False
        if tool.annotations and hasattr(tool.annotations, "readOnlyHint"):
            self.is_read_only = tool.annotations.readOnlyHint or False

        self._tool = tool
        self._gateway_url = gateway_url.rstrip("/")
        self._token = token
        self._http = http
        self._timeout = timeout

    async def check_permissions(
        self,
        *_args: Any,
        **_kwargs: Any,
    ) -> PermissionDecision:
        """Default policy: read-only tools auto-allow, everything else
        defers to the user via ``ASK``. Mirrors
        :class:`agentscope.tool.MCPTool.check_permissions` so toolkit
        callers see identical behaviour through the gateway.
        """
        if self.is_read_only:
            return PermissionDecision(
                behavior=PermissionBehavior.ALLOW,
                message="This is a read-only MCP tool. Allowing execution.",
            )
        return PermissionDecision(
            behavior=PermissionBehavior.ASK,
            message="MCP tools must be explicitly allowed by the user.",
        )

    async def __call__(self, **kwargs: Any) -> ToolChunk:
        """Invoke the upstream tool by POSTing to
        ``/mcps/{mcp}/tools/{tool}`` on the gateway.

        Args:
            **kwargs: Tool arguments forwarded as the JSON body's
                ``arguments`` field; the gateway re-dispatches them to
                the upstream MCP session.

        Returns:
            `ToolChunk`:
                The reconstructed chunk returned by the upstream tool.
                4xx / 5xx responses are surfaced as a
                ``ToolChunk(state=ERROR)`` so the agent loop can reason
                about the failure instead of crashing.

        Raises:
            RuntimeError: If the gateway returns 2xx but no ``chunk``
                payload (protocol violation on the gateway side).
        """
        url = (
            f"{self._gateway_url}/mcps/{self.mcp_name}"
            f"/tools/{self._tool.name}"
        )
        headers = _bearer_headers(self._token)
        async with _http_session(self._http, self._timeout) as http:
            resp = await http.post(
                url,
                json={"arguments": kwargs},
                headers=headers,
            )
            if resp.status_code >= 400:
                # Surface gateway-side error as a failed ToolChunk so
                # the agent loop can reason about it instead of crashing.
                detail = _safe_detail(resp)
                return ToolChunk(
                    content=[{"type": "text", "text": detail}],
                    state=ToolResultState.ERROR,
                )
            payload = resp.json()
        chunk_dict = payload.get("chunk")
        if chunk_dict is None:
            raise RuntimeError(
                f"gateway returned no chunk for {self.name!r}",
            )
        return ToolChunk.model_validate(chunk_dict)


# ── pseudo MCP client ──────────────────────────────────────────────


class GatewayMCPClient(MCPClient):
    """An :class:`MCPClient` whose protocol logic is replaced by HTTP.

    Constructed from the dict returned by ``GET /mcps`` (or freshly from
    user input via :meth:`GatewayClient.make_client`). The local MCP
    machinery is short-circuited entirely:

    * ``model_post_init`` does nothing (parent's ``_initialize_client``
      is never called — no stdio context manager is built).
    * ``connect`` POSTs to ``/mcps`` to register-and-start the upstream
      server inside the gateway.
    * ``close`` DELETEs ``/mcps/{name}``.
    * ``list_tools`` / ``get_tool`` fetch and wrap upstream tools.
    """

    _gateway_url: str = PrivateAttr(default="")
    _gateway_token: str = PrivateAttr(default="")
    _http_timeout: float | None = PrivateAttr(default=None)
    _http: httpx.AsyncClient | None = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        """Skip the parent's stdio/HTTP client preparation.

        For a real :class:`MCPClient`, ``model_post_init`` builds the
        local stdio context manager (or wires up an HTTP client) so
        the in-process session can be opened. For
        :class:`GatewayMCPClient` all MCP-side work happens inside the
        gateway container; the host-side proxy needs no local session
        machinery, so this override is a no-op.
        """
        return

    # ── lifecycle ─────────────────────────────────────────────────

    def attach(
        self,
        *,
        gateway_url: str,
        token: str,
        http: httpx.AsyncClient | None,
        timeout: float | None,
        connected: bool = False,
    ) -> None:
        """Wire this client to a gateway transport.

        :class:`GatewayMCPClient` is normally produced by
        ``model_validate(spec)`` over a dict returned by the gateway's
        ``GET /mcps`` endpoint — that step recovers the public field
        surface but leaves all transport-related private attributes
        empty. ``attach`` injects them in a single call so subsequent
        :meth:`connect`, :meth:`close`, :meth:`list_raw_tools`, and
        :meth:`get_tool` can talk to the gateway. It is the only
        supported way to populate the transport state from outside the
        class — encapsulating the writes here keeps callers free of
        ``protected-access`` warnings.

        Args:
            gateway_url: Host-visible base URL of the gateway (e.g.
                ``http://127.0.0.1:<host_port>``). Trailing slash is
                stripped before storage.
            token: Bearer token the host generated for this gateway.
                Sent as ``Authorization: Bearer …`` on every request.
            http: Shared :class:`httpx.AsyncClient` provided by the
                owning :class:`GatewayClient` so connection pooling is
                shared across all derived clients and tools. When
                ``None`` each call creates a one-shot client.
            timeout: Default per-request timeout in seconds, used only
                when ``http`` is ``None``.
            connected: When ``True``, mark this client as already
                connected (i.e. the gateway is already maintaining the
                upstream session). Used by
                :meth:`GatewayClient.list_mcps` for clients that came
                back from the gateway as registered. Leave ``False``
                when the caller will call :meth:`connect` themselves.
        """
        self._gateway_url = gateway_url.rstrip("/")
        self._gateway_token = token
        self._http = http
        self._http_timeout = timeout
        if connected:
            self._is_connected = True

    async def connect(self) -> None:
        """Register this MCP on the gateway via ``POST /mcps``.

        Stateless MCPs are a no-op (the gateway invokes them on
        demand). Stateful MCPs are registered and started inside the
        gateway container; this method blocks until the gateway has
        confirmed the upstream connection.

        Raises:
            RuntimeError: If the client is already connected, or if
                the gateway returns a 4xx/5xx response.
        """
        if not self.is_stateful:
            return
        if self._is_connected:
            raise RuntimeError(
                f"MCP {self.name!r} is already connected. "
                "Call close() before reconnecting.",
            )
        body = self.model_dump(mode="json")
        async with _http_session(self._http, self._http_timeout) as http:
            resp = await http.post(
                f"{self._gateway_url}/mcps",
                json=body,
                headers=_bearer_headers(self._gateway_token),
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"gateway failed to add MCP {self.name!r}: "
                    f"{_safe_detail(resp)}",
                )
        self._is_connected = True

    async def close(self, ignore_errors: bool = True) -> None:
        """Deregister this MCP from the gateway via
        ``DELETE /mcps/{name}``.

        Stateless MCPs are a no-op. For stateful MCPs the gateway
        closes the upstream session before responding.

        Args:
            ignore_errors: When ``True`` (the default), suppress both
                "not connected" precondition failures and
                gateway-side 4xx/5xx responses; when ``False`` such
                conditions raise :class:`RuntimeError`. Mirrors
                :meth:`MCPClient.close` so callers can use the same
                shutdown idiom regardless of transport.
        """
        if not self.is_stateful:
            return
        if not self._is_connected:
            if ignore_errors:
                return
            raise RuntimeError(
                f"MCP {self.name!r} is not connected. Call connect() first.",
            )
        try:
            async with _http_session(self._http, self._http_timeout) as http:
                resp = await http.delete(
                    f"{self._gateway_url}/mcps/{self.name}",
                    headers=_bearer_headers(self._gateway_token),
                )
                if resp.status_code >= 400 and not ignore_errors:
                    raise RuntimeError(
                        f"gateway failed to remove MCP {self.name!r}: "
                        f"{_safe_detail(resp)}",
                    )
        except Exception:
            if not ignore_errors:
                raise
        self._is_connected = False

    # ── tool discovery ────────────────────────────────────────────

    async def list_raw_tools(self) -> list[mcp.types.Tool]:
        """Fetch the upstream tool list via ``GET /mcps/{name}/tools``.

        Returns the raw :class:`mcp.types.Tool` descriptors the gateway
        forwarded — i.e. with their **upstream** names (no ``mcp__``
        prefix) so the inherited :meth:`list_tools` / :meth:`get_tool`
        path can re-wrap them through :meth:`_wrap_tool` exactly as a
        local :class:`MCPClient` would. The full unfiltered list is
        cached on ``_cached_tools`` first; the returned list then has
        ``enable_tools`` / ``disable_tools`` filtering applied
        identically to :meth:`MCPClient.list_raw_tools`.

        Returns:
            `list[mcp.types.Tool]`:
                The upstream-named, post-filter tool descriptors.

        Raises:
            httpx.HTTPStatusError: If the gateway returns a non-2xx
                response.
        """
        async with _http_session(self._http, self._http_timeout) as http:
            resp = await http.get(
                f"{self._gateway_url}/mcps/{self.name}/tools",
                headers=_bearer_headers(self._gateway_token),
            )
            resp.raise_for_status()
            data = resp.json()

        raw_tools = [mcp.types.Tool.model_validate(d) for d in data]
        self._cached_tools = raw_tools

        # Honour the same enable/disable filtering MCPClient does locally —
        # gateway returns the unfiltered upstream view.
        if self.enable_tools is not None:
            raw_tools = [t for t in raw_tools if t.name in self.enable_tools]
        if self.disable_tools is not None:
            raw_tools = [
                t for t in raw_tools if t.name not in self.disable_tools
            ]
        return raw_tools

    async def get_tool(  # type: ignore[override]
        self,
        name: str,
    ) -> GatewayMCPTool:
        """Look up a single tool by upstream name and wrap it.

        Falls back to :meth:`list_raw_tools` on cache miss, then
        searches ``_cached_tools`` (which holds the **unfiltered**
        upstream view) so tools that ``enable_tools`` /
        ``disable_tools`` would have hidden are still resolvable —
        matching :meth:`MCPClient.get_tool`'s behaviour.

        The wrapped tool inherits the gateway-wide HTTP timeout
        (``_http_timeout``, set via :meth:`attach`). Per-call execution
        timeout is the upstream MCP server's responsibility — it is
        carried in :attr:`MCPClient.execution_timeout`, serialised into
        the spec, and reconstructed on the gateway side; the host has
        no need to override it.

        Args:
            name: Upstream tool name (no ``mcp__`` prefix). The
                returned :class:`GatewayMCPTool` exposes the prefixed
                form via its own ``name`` attribute.

        Returns:
            `GatewayMCPTool`:
                A fresh wrapper around the upstream descriptor, ready
                to be ``await``-ed or registered with a toolkit.

        Raises:
            ValueError: If no tool with that upstream name exists on
                the gateway side.
        """
        if self._cached_tools is None:
            await self.list_raw_tools()
        for raw in self._cached_tools or []:
            if raw.name == name:
                return self._wrap_tool(raw)
        raise ValueError(
            f"Tool {name!r} not found in MCP {self.name!r}.",
        )

    # ── helpers ───────────────────────────────────────────────────

    def _wrap_tool(self, tool: mcp.types.Tool) -> GatewayMCPTool:
        """Build a :class:`GatewayMCPTool` bound to this client's
        gateway transport. Always uses the client-wide
        ``_http_timeout`` — there is no per-call override path.

        Args:
            tool: Raw upstream tool descriptor (typically pulled out of
                ``_cached_tools``).
        """
        return GatewayMCPTool(
            mcp_name=self.name,
            tool=tool,
            gateway_url=self._gateway_url,
            token=self._gateway_token,
            http=self._http,
            timeout=self._http_timeout,
        )


# ── workspace-side facade ──────────────────────────────────────────


class GatewayClient:
    """Workspace-side facade over the in-container MCP gateway.

    Owns a shared :class:`httpx.AsyncClient` so all derived
    :class:`GatewayMCPClient` and :class:`GatewayMCPTool` instances
    share connection pooling.

    The gateway ``base_url`` is the host-visible URL (e.g.
    ``http://127.0.0.1:<host_port>`` after Docker port mapping); the
    ``token`` is the bearer the host generated and shipped into the
    container's gateway config.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: float | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        """Build a workspace-side gateway facade.

        Args:
            base_url: Host-visible base URL of the gateway, e.g.
                ``http://127.0.0.1:<host_port>`` after Docker port
                mapping or ``https://<sandbox-id>.e2b.dev`` for E2B.
                Trailing slash is stripped.
            token: Bearer token shared with the gateway via its config
                file. Sent as ``Authorization: Bearer …`` on every
                request and propagated to every derived
                :class:`GatewayMCPClient` / :class:`GatewayMCPTool` via
                :meth:`make_client`.
            timeout: Default HTTP timeout in seconds, applied to the
                shared :class:`httpx.AsyncClient` (see
                :meth:`_client`) and propagated to every derived
                :class:`GatewayMCPClient` via :meth:`make_client`.
                There is no per-call override path; per-tool execution
                timeouts live on :attr:`MCPClient.execution_timeout`
                and are honoured upstream inside the gateway.
            extra_headers: Default headers applied to every request
                through the shared httpx client (in addition to the
                per-call bearer). :class:`E2BWorkspace` uses this to
                inject E2B's ``X-Access-Token`` proxy header.
        """
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout
        self.extra_headers: dict[str, str] = dict(extra_headers or {})
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        """Return (lazily creating on first use) the shared
        :class:`httpx.AsyncClient` reused by every gateway request."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                timeout=self.timeout,
                headers=self.extra_headers or None,
            )
        return self._http

    def _headers(self) -> dict[str, str]:
        """Build the bearer-auth header dict for direct
        :class:`GatewayClient` calls (``/health``, ``/mcps``)."""
        return _bearer_headers(self.token)

    async def health(self) -> bool:
        """Probe ``/health`` — used by the workspace to wait for readiness."""
        try:
            resp = await self._client().get(f"{self.base_url}/health")
        except Exception:
            return False
        return resp.status_code == 200

    async def list_mcps(self) -> list[GatewayMCPClient]:
        """Fetch every MCP currently registered on the gateway.

        The returned clients are marked as already connected (via
        :meth:`GatewayMCPClient.attach`'s ``connected=True``) because
        the gateway is already maintaining their upstream sessions —
        the host should not invoke :meth:`GatewayMCPClient.connect`
        again.

        Returns:
            `list[GatewayMCPClient]`:
                One transport-wired client per registered MCP. The
                workspace's :meth:`list_mcps` implementation surfaces
                this list straight to its consumer.

        Raises:
            httpx.HTTPStatusError: If the gateway returns a non-2xx
                response.
        """
        resp = await self._client().get(
            f"{self.base_url}/mcps",
            headers=self._headers(),
        )
        resp.raise_for_status()
        return [self.make_client(spec, connected=True) for spec in resp.json()]

    def make_client(
        self,
        spec: dict[str, Any],
        *,
        connected: bool = False,
    ) -> GatewayMCPClient:
        """Build a :class:`GatewayMCPClient` wired to this gateway.

        Reconstructs the public field surface from ``spec`` via
        :meth:`MCPClient.model_validate`, then hands the
        transport-related private state to the new client through
        :meth:`GatewayMCPClient.attach`. Doing the wiring through
        ``attach`` keeps the writes inside the target class and avoids
        ``protected-access`` warnings on every assignment.

        Args:
            spec: A dict produced by ``MCPClient.model_dump(mode="json")``
                — typically the body returned by the gateway's
                ``GET /mcps`` endpoint, or built from user input by
                ``DockerWorkspace.add_mcp``.
            connected: When ``True``, mark the new client as already
                connected so :meth:`GatewayMCPClient.connect` need not
                run again. Set by :meth:`list_mcps` for clients that
                came back from the gateway already registered. Leave
                ``False`` for fresh clients the caller will explicitly
                ``await client.connect()`` on (the ``add_mcp`` path).

        Returns:
            `GatewayMCPClient`:
                A pydantic-valid client whose transport state is fully
                populated. Stateful clients still require an explicit
                ``await client.connect()`` unless ``connected=True``.
        """
        client = GatewayMCPClient.model_validate(spec)
        client.attach(
            gateway_url=self.base_url,
            token=self.token,
            http=self._client(),
            timeout=self.timeout,
            connected=connected,
        )
        return client

    async def aclose(self) -> None:
        """Close the shared HTTP client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None


# ── module-private utilities ───────────────────────────────────────


def _bearer_headers(token: str) -> dict[str, str]:
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


@contextlib.asynccontextmanager
async def _http_session(
    shared: httpx.AsyncClient | None,
    timeout: float | None,
) -> AsyncIterator[httpx.AsyncClient]:
    """Yield a shared httpx client when injected, else a one-shot client.

    The shared variant lets the workspace pool connections across many
    tool calls without each call paying the TLS/handshake cost.
    """
    if shared is not None:
        yield shared
    else:
        async with httpx.AsyncClient(timeout=timeout) as http:
            yield http


def _safe_detail(resp: httpx.Response) -> str:
    """Best-effort extraction of an HTTPException-style detail from a
    response."""
    try:
        body = resp.json()
    except Exception:
        return f"HTTP {resp.status_code}: {resp.text[:200]}"
    if isinstance(body, dict) and "detail" in body:
        return f"HTTP {resp.status_code}: {body['detail']}"
    return f"HTTP {resp.status_code}: {str(body)[:200]}"
