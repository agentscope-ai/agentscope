# -*- coding: utf-8 -*-
"""Host-side client for the in-sandbox MCP gateway, driven through
``backend.exec_shell``.

Three classes live here:

* :class:`GatewayClient` — workspace-side facade over the gateway's
  ``/health`` and ``/mcps`` endpoints. Used by :class:`DockerWorkspace`
  and :class:`E2BWorkspace` for top-level operations.

* :class:`GatewayMCPClient` — an :class:`MCPClient` subclass whose
  protocol behaviour is replaced by gateway-relayed calls. The field
  surface is identical to ``MCPClient`` (instances are built from
  ``MCPClient.model_dump()`` data the gateway returns), so callers
  that ``model_dump()`` it round-trip cleanly. Local stdio/HTTP
  session machinery is bypassed: ``model_post_init`` is a no-op,
  ``connect`` registers via ``POST /mcps``, ``close`` deregisters via
  ``DELETE /mcps/{name}``, and ``list_raw_tools`` / ``get_tool`` fetch
  / wrap upstream tools.

* :class:`GatewayMCPTool` — :class:`ToolBase` subclass whose
  ``__call__`` invokes the upstream tool via
  ``POST /mcps/{name}/tools/{tool}`` and reconstructs the returned
  ``ToolChunk``.

Transport
---------

Unlike a normal HTTP client, **every** request runs **inside the
sandbox**. The host serialises the request body (if any) to a tempfile
via :meth:`BackendBase.write_file`, then spawns a tiny Python script
through :meth:`BackendBase.exec_shell` (see
:mod:`agentscope.workspace._gateway_shim`) which performs the actual
``urllib.request`` call against the gateway's loopback port, writes a
JSON envelope to stdout, and exits 0. The host parses the envelope and
reconstructs status code + response bytes (base64-decoded inline, or
read back from a sandbox tempfile for large payloads).

This removes the host→sandbox network reachability requirement
(previously satisfied by Docker port mapping or the E2B HTTPS proxy);
the gateway listens on a fixed loopback port inside the sandbox and is
no longer reachable from the host at all.
"""

from __future__ import annotations

import base64
import json
import uuid
from typing import TYPE_CHECKING, Any

import mcp.types
from pydantic import PrivateAttr

from ..mcp import MCPClient
from ..message import ToolResultState
from ..permission import (
    PermissionBehavior,
    PermissionDecision,
)
from ..tool import ToolBase, ToolChunk
from ._gateway_shim import (
    BODY_INLINE_LIMIT,
    SANDBOX_TMP_DIR,
    SHIM_SCRIPT,
)

if TYPE_CHECKING:
    from ..tool import BackendBase, ExecResult


# ── tool ───────────────────────────────────────────────────────────


class GatewayMCPTool(ToolBase):
    """An MCP tool whose ``__call__`` is one ``exec_shell``-driven
    request to the gateway.

    Mirrors :class:`agentscope.tool.MCPTool` field-by-field so the
    toolkit treats it identically (same ``name`` format, same
    permission policy) — only the call path changes.
    """

    is_mcp: bool = True
    is_state_injected: bool = False

    def __init__(
        self,
        mcp_name: str,
        tool: mcp.types.Tool,
        gateway: "GatewayClient",
    ) -> None:
        """Build a gateway-backed MCP tool.

        The instance mirrors the field surface of
        :class:`agentscope.tool.MCPTool` (``name``, ``description``,
        ``input_schema``, ``is_read_only``, …) so the host-side toolkit
        cannot tell the difference between a local MCP tool and one
        that forwards through the in-sandbox gateway.

        Args:
            mcp_name (`str`):
                Name of the upstream MCP server this tool belongs to.
                Used both for the visible ``mcp__{mcp}__{tool}`` name
                and for the gateway URL path.
            tool (`mcp.types.Tool`):
                Raw upstream tool descriptor as returned by the gateway.
                Its ``name`` is the upstream-side identifier (no
                ``mcp__`` prefix), ``inputSchema`` is forwarded verbatim,
                and ``annotations.readOnlyHint`` drives the permission
                policy.
            gateway (`GatewayClient`):
                The workspace-side gateway facade that owns the
                backend handle, gateway port, and bearer token. All
                tool invocations are dispatched through its
                :meth:`GatewayClient.exec_request`.
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
        self._gateway = gateway

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
        """Invoke the upstream tool by relaying
        ``POST /mcps/{mcp}/tools/{tool}`` to the gateway via the
        sandbox shim.

        Args:
            **kwargs:
                Tool arguments forwarded as the JSON body's
                ``arguments`` field; the gateway re-dispatches them to
                the upstream MCP session.

        Returns:
            `ToolChunk`:
                The reconstructed chunk returned by the upstream tool.
                4xx / 5xx responses are surfaced as a
                ``ToolChunk(state=ERROR)`` so the agent loop can reason
                about the failure instead of crashing.

        Raises:
            `RuntimeError`:
                If the gateway returns 2xx but no ``chunk`` payload
                (protocol violation on the gateway side).
        """
        status, body = await self._gateway.exec_request(
            "POST",
            f"/mcps/{self.mcp_name}/tools/{self._tool.name}",
            body={"arguments": kwargs},
        )
        if status >= 400:
            return ToolChunk(
                content=[{"type": "text", "text": _safe_detail(status, body)}],
                state=ToolResultState.ERROR,
            )
        payload = json.loads(body)
        chunk_dict = payload.get("chunk")
        if chunk_dict is None:
            raise RuntimeError(
                f"gateway returned no chunk for {self.name!r}",
            )
        return ToolChunk.model_validate(chunk_dict)


# ── pseudo MCP client ──────────────────────────────────────────────


class GatewayMCPClient(MCPClient):
    """An :class:`MCPClient` whose protocol logic is replaced by
    gateway-relayed calls.

    Constructed from the dict returned by ``GET /mcps`` (or freshly
    from user input via :meth:`GatewayClient.make_client`). The local
    MCP machinery is short-circuited entirely:

    * ``model_post_init`` does nothing (parent's ``_initialize_client``
      is never called — no stdio context manager is built).
    * ``connect`` registers the MCP on the gateway via ``POST /mcps``.
    * ``close`` deregisters via ``DELETE /mcps/{name}``.
    * ``list_raw_tools`` / ``get_tool`` fetch and wrap upstream tools.
    """

    _gateway: "GatewayClient | None" = PrivateAttr(default=None)

    def model_post_init(self, __context: Any) -> None:
        """Skip the parent's stdio/HTTP client preparation.

        For a real :class:`MCPClient`, ``model_post_init`` builds the
        local stdio context manager (or wires up an HTTP client) so
        the in-process session can be opened. For
        :class:`GatewayMCPClient` all MCP-side work happens inside the
        gateway sandbox; the host-side proxy needs no local session
        machinery, so this override is a no-op.
        """
        return

    # ── lifecycle ─────────────────────────────────────────────────

    def attach(
        self,
        gateway: "GatewayClient",
        *,
        connected: bool = False,
    ) -> None:
        """Wire this client to a gateway facade.

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
            gateway (`GatewayClient`):
                The workspace-side gateway facade that owns the
                sandbox backend handle, gateway port, and bearer
                token. All subsequent calls are dispatched through its
                :meth:`GatewayClient.exec_request`.
            connected (`bool`, defaults to `False`):
                When ``True``, mark this client as already connected
                (i.e. the gateway is already maintaining the upstream
                session). Used by :meth:`GatewayClient.list_mcps` for
                clients that came back from the gateway as registered.
                Leave ``False`` when the caller will call
                :meth:`connect` themselves.
        """
        self._gateway = gateway
        if connected:
            self._is_connected = True

    async def connect(self) -> None:
        """Register this MCP on the gateway via ``POST /mcps``.

        All MCPs (stateless and stateful) must be registered so that
        ``/mcps/{name}/tools/{tool}`` can locate the client. For
        stateful MCPs the gateway additionally opens the upstream
        session before responding.

        Raises:
            `RuntimeError`:
                If the client is already connected, the gateway is
                unreachable, or the gateway returns a 4xx/5xx
                response.
        """
        if self._is_connected:
            raise RuntimeError(
                f"MCP {self.name!r} is already connected. "
                "Call close() before reconnecting.",
            )
        assert self._gateway is not None
        body = self.model_dump(mode="json")
        status, resp_body = await self._gateway.exec_request(
            "POST",
            "/mcps",
            body=body,
        )
        if status >= 400:
            raise RuntimeError(
                f"gateway failed to add MCP {self.name!r}: "
                f"{_safe_detail(status, resp_body)}",
            )
        self._is_connected = True

    async def close(self, ignore_errors: bool = True) -> None:
        """Deregister this MCP from the gateway via
        ``DELETE /mcps/{name}``.

        All MCPs are deregistered from the gateway registry; the
        gateway additionally closes the upstream session for stateful
        clients before responding.

        Args:
            ignore_errors (`bool`, defaults to `True`):
                When ``True`` (the default), suppress both "not
                connected" precondition failures and gateway-side
                4xx/5xx responses; when ``False`` such conditions
                raise :class:`RuntimeError`. Mirrors
                :meth:`MCPClient.close` so callers can use the same
                shutdown idiom regardless of transport.
        """
        if not self._is_connected:
            if ignore_errors:
                return
            raise RuntimeError(
                f"MCP {self.name!r} is not connected. Call connect() first.",
            )
        assert self._gateway is not None
        try:
            status, resp_body = await self._gateway.exec_request(
                "DELETE",
                f"/mcps/{self.name}",
            )
            if status >= 400 and not ignore_errors:
                raise RuntimeError(
                    f"gateway failed to remove MCP {self.name!r}: "
                    f"{_safe_detail(status, resp_body)}",
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
            `RuntimeError`:
                If the gateway returns a non-2xx response.
        """
        assert self._gateway is not None
        status, body = await self._gateway.exec_request(
            "GET",
            f"/mcps/{self.name}/tools",
        )
        if status >= 400:
            raise RuntimeError(
                f"gateway failed to list tools for MCP {self.name!r}: "
                f"{_safe_detail(status, body)}",
            )
        data = json.loads(body)

        raw_tools = [mcp.types.Tool.model_validate(d) for d in data]
        self._cached_tools = raw_tools

        # Honour the same enable/disable filtering MCPClient does
        # locally — gateway returns the unfiltered upstream view.
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

        Args:
            name (`str`):
                Upstream tool name (no ``mcp__`` prefix). The returned
                :class:`GatewayMCPTool` exposes the prefixed form via
                its own ``name`` attribute.

        Returns:
            `GatewayMCPTool`:
                A fresh wrapper around the upstream descriptor, ready
                to be ``await``-ed or registered with a toolkit.

        Raises:
            `ValueError`:
                If no tool with that upstream name exists on the
                gateway side.
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
        gateway facade.

        Args:
            tool (`mcp.types.Tool`):
                Raw upstream tool descriptor (typically pulled out of
                ``_cached_tools``).

        Returns:
            `GatewayMCPTool`:
                The host-side wrapper that will relay every call
                through the gateway facade.
        """
        assert self._gateway is not None
        return GatewayMCPTool(
            mcp_name=self.name,
            tool=tool,
            gateway=self._gateway,
        )


# ── workspace-side facade ──────────────────────────────────────────


class GatewayClient:
    """Workspace-side facade over the in-sandbox MCP gateway.

    Every method dispatches through :meth:`exec_request`, which in
    turn drives :meth:`BackendBase.exec_shell` on the workspace's
    backend so the network call always happens **inside** the sandbox.
    No host port mapping or HTTPS proxy is required.
    """

    def __init__(
        self,
        backend: "BackendBase",
        gateway_port: int,
        token: str,
        *,
        timeout: float | None = None,
        inline_limit: int = BODY_INLINE_LIMIT,
        tmp_dir: str = SANDBOX_TMP_DIR,
    ) -> None:
        """Build a workspace-side gateway facade.

        Args:
            backend (`BackendBase`):
                The workspace's backend handle. Every gateway request
                runs as ``backend.exec_shell([...])`` inside the
                sandbox (Docker container or E2B sandbox).
            gateway_port (`int`):
                TCP port the gateway listens on inside the sandbox.
                The URL dialed by the shim is always
                ``http://127.0.0.1:<gateway_port>``.
            token (`str`):
                Bearer token shared with the gateway via its config
                file. Sent as ``Authorization: Bearer …`` on every
                request. Pass an empty string to skip auth (useful
                only in tests).
            timeout (`float | None`, defaults to `None`):
                Per-request timeout in seconds, passed straight through
                to :meth:`BackendBase.exec_shell`. The shim itself
                does not apply an HTTP-level timeout — long-running
                MCP tools are limited only by the backend exec
                timeout. ``None`` waits indefinitely.
            inline_limit (`int`, defaults to `BODY_INLINE_LIMIT`):
                Threshold (in bytes) below which response bodies ride
                inline through stdout as base64. Larger bodies are
                spilled to a sandbox tempfile and fetched via
                :meth:`BackendBase.read_file` to avoid loading multi-MB
                payloads through the exec stdout channel.
            tmp_dir (`str`, defaults to `SANDBOX_TMP_DIR`):
                Sandbox-side directory used for both request body
                tempfiles (host → sandbox) and oversized response
                spills (sandbox → host). Must be writable by the
                gateway process; ``/tmp`` works on every supported
                image.
        """
        self.backend = backend
        self.gateway_port = gateway_port
        self.token = token
        self.timeout = timeout
        self.inline_limit = inline_limit
        self.tmp_dir = tmp_dir

    async def health(self) -> bool:
        """Probe ``/health`` — used by the workspace to wait for
        readiness.

        Returns:
            `bool`:
                ``True`` iff the gateway answered ``200``. Any other
                outcome (shim transport failure, non-200 status)
                returns ``False``; callers are expected to retry until
                this flips.
        """
        try:
            status, _ = await self.exec_request("GET", "/health")
        except Exception:
            return False
        return status == 200

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
            `RuntimeError`:
                If the gateway returns a non-2xx response.
        """
        status, body = await self.exec_request("GET", "/mcps")
        if status >= 400:
            raise RuntimeError(
                f"gateway failed to list MCPs: {_safe_detail(status, body)}",
            )
        specs = json.loads(body)
        return [self.make_client(spec, connected=True) for spec in specs]

    def make_client(
        self,
        spec: dict[str, Any],
        *,
        connected: bool = False,
    ) -> GatewayMCPClient:
        """Build a :class:`GatewayMCPClient` wired to this gateway.

        Reconstructs the public field surface from ``spec`` via
        :meth:`MCPClient.model_validate`, then hands the transport
        handle to the new client through
        :meth:`GatewayMCPClient.attach`. Doing the wiring through
        ``attach`` keeps the writes inside the target class and avoids
        ``protected-access`` warnings on every assignment.

        Args:
            spec (`dict[str, Any]`):
                A dict produced by ``MCPClient.model_dump(mode="json")``
                — typically the body returned by the gateway's
                ``GET /mcps`` endpoint, or built from user input by
                ``DockerWorkspace.add_mcp``.
            connected (`bool`, defaults to `False`):
                When ``True``, mark the new client as already connected
                so :meth:`GatewayMCPClient.connect` need not run again.
                Set by :meth:`list_mcps` for clients that came back
                from the gateway already registered. Leave ``False``
                for fresh clients the caller will explicitly
                ``await client.connect()`` on (the ``add_mcp`` path).

        Returns:
            `GatewayMCPClient`:
                A pydantic-valid client whose transport state is fully
                populated. Stateful clients still require an explicit
                ``await client.connect()`` unless ``connected=True``.
        """
        client = GatewayMCPClient.model_validate(spec)
        client.attach(self, connected=connected)
        return client

    async def aclose(self) -> None:
        """No-op kept for API parity with the previous httpx-based
        client.

        The new transport holds no host-side resources (every call is
        a one-shot ``exec_shell``), so there is nothing to close. The
        method stays so callers can keep their existing shutdown
        idiom.
        """
        return

    # ── transport ─────────────────────────────────────────────────

    async def exec_request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
    ) -> tuple[int, bytes]:
        """Relay one HTTP request through the sandbox.

        Mechanics:

        1. If ``body`` is given, JSON-encode it and write it to
           ``${tmp_dir}/<uuid>.json`` inside the sandbox via
           :meth:`BackendBase.write_file`.
        2. Run ``python3 -c <SHIM_SCRIPT> <method> <url> <token>
           <body_file_or_""> <inline_limit> <tmp_dir>`` via
           :meth:`BackendBase.exec_shell`. The shim performs the
           ``urllib.request`` call against the gateway's loopback port
           and prints a JSON envelope to stdout.
        3. Parse the envelope. Inline bodies are base64-decoded;
           oversized bodies are pulled back from
           ``envelope["body_file"]`` via :meth:`BackendBase.read_file`
           and then deleted.

        Both the request and response temp files are best-effort
        cleaned up so a crash does not leak files into the sandbox's
        ``${tmp_dir}``.

        Args:
            method (`str`):
                HTTP verb (``GET`` / ``POST`` / ``DELETE``).
            path (`str`):
                Path-only URL relative to the gateway root, e.g.
                ``/mcps`` or ``/mcps/<name>/tools/<tool>``. Always
                starts with ``/``.
            body (`Any`, optional):
                JSON-serializable request body. ``None`` (the default)
                means no body — typical for ``GET`` / ``DELETE``.

        Returns:
            `tuple[int, bytes]`:
                The HTTP status code returned by the gateway, paired
                with the raw response body bytes (always decoded;
                callers ``json.loads`` it themselves when needed).

        Raises:
            `RuntimeError`:
                If the shim crashed (non-zero exit code, non-JSON
                stdout) or reported a transport failure
                (``status == -1`` — gateway unreachable, urllib
                error, …).
        """
        body_file = ""
        wrote_body_file: str | None = None
        if body is not None:
            body_file = f"{self.tmp_dir}/{uuid.uuid4().hex}.json"
            wrote_body_file = body_file
            await self.backend.write_file(
                body_file,
                json.dumps(body, ensure_ascii=False).encode("utf-8"),
            )

        try:
            result: "ExecResult" = await self.backend.exec_shell(
                [
                    "python3",
                    "-c",
                    SHIM_SCRIPT,
                    method,
                    f"http://127.0.0.1:{self.gateway_port}{path}",
                    self.token or "",
                    body_file,
                    str(self.inline_limit),
                    self.tmp_dir,
                ],
                timeout=self.timeout,
            )
        finally:
            if wrote_body_file is not None:
                try:
                    await self.backend.delete_path(wrote_body_file)
                except Exception:
                    pass

        if result.exit_code != 0:
            raise RuntimeError(
                f"gateway shim exited with {result.exit_code}: "
                f"{result.stderr.decode(errors='replace')[:500]}",
            )

        try:
            env = json.loads(result.stdout)
        except Exception as e:
            raise RuntimeError(
                "gateway shim produced non-JSON stdout: "
                f"{result.stdout[:200]!r}",
            ) from e

        status = int(env["status"])
        if status == -1:
            raise RuntimeError(
                "gateway request failed: "
                f"{env.get('error', 'unknown error')}",
            )

        if "body_file" in env:
            spilled = env["body_file"]
            body_bytes = await self.backend.read_file(spilled)
            try:
                await self.backend.delete_path(spilled)
            except Exception:
                pass
        else:
            body_bytes = base64.b64decode(env.get("body", ""))

        return status, body_bytes


# ── module-private utilities ───────────────────────────────────────


def _safe_detail(status: int, body: bytes) -> str:
    """Best-effort extraction of an HTTPException-style detail from a
    gateway response body.

    Args:
        status (`int`):
            HTTP status code returned by the gateway.
        body (`bytes`):
            Raw response body bytes — typically a JSON object from
            FastAPI's ``HTTPException``, but tolerate anything.

    Returns:
        `str`:
            A human-readable diagnostic that always starts with
            ``HTTP <status>:`` so it slots into the same exception /
            ``ToolChunk`` messages as the previous httpx-based code.
    """
    try:
        data = json.loads(body)
    except Exception:
        return f"HTTP {status}: {body[:200].decode(errors='replace')}"
    if isinstance(data, dict) and "detail" in data:
        return f"HTTP {status}: {data['detail']}"
    return f"HTTP {status}: {str(data)[:200]}"
