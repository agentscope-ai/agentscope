# -*- coding: utf-8 -*-
"""Host-side client for the in-sandbox MCP gateway, driven through
``backend.exec_shell``.

Three classes:

* :class:`GatewayClient` — workspace-side facade over ``/health`` and
  ``/mcps``. Used by the sandboxed workspaces for top-level operations.
* :class:`GatewayMCPClient` — an :class:`MCPClient` subclass whose
  protocol behaviour is replaced by gateway-relayed calls. Local
  stdio/HTTP machinery is bypassed; ``model_post_init`` is a no-op;
  ``connect`` / ``close`` / ``list_raw_tools`` / ``get_tool`` all
  round-trip through ``/mcps``.
* :class:`GatewayMCPTool` — :class:`ToolBase` whose ``__call__``
  invokes the upstream tool via ``POST /mcps/{name}/tools/{tool}``.

Every request runs inside the sandbox: the host writes an optional
body to a sandbox tempfile, spawns a small Python shim via
:meth:`BackendBase.exec_shell` (see :mod:`._gateway_shim`), and parses
the JSON envelope the shim prints on stdout. No host→sandbox network
reachability is required — the gateway only binds sandbox loopback.
"""

from __future__ import annotations

import base64
import json
import secrets
import uuid
from typing import TYPE_CHECKING, Any

import mcp.types
from pydantic import PrivateAttr

from .._logging import logger
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

        Mirrors the field surface of :class:`agentscope.tool.MCPTool`
        so the toolkit cannot tell the difference.

        Args:
            mcp_name (`str`):
                Upstream MCP server name; drives the visible
                ``mcp__{mcp}__{tool}`` name and the URL path.
            tool (`mcp.types.Tool`):
                Raw upstream tool descriptor as returned by the gateway.
                ``inputSchema`` is forwarded verbatim;
                ``annotations.readOnlyHint`` drives the permission
                policy.
            gateway (`GatewayClient`):
                Facade dispatching every call through
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
        """Read-only tools auto-allow; everything else defers via
        ``ASK``. Mirrors :meth:`MCPTool.check_permissions`.
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
        """Relay ``POST /mcps/{mcp}/tools/{tool}`` to the gateway.

        4xx / 5xx responses come back as ``ToolChunk(state=ERROR)`` so
        the agent loop can reason about failure. Raises
        :class:`RuntimeError` only if the gateway returns 2xx with no
        ``chunk`` payload (protocol violation).
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
        """No-op — the parent builds local stdio/HTTP transport, which
        the gateway-relayed client does not need.
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

        Instances come out of ``model_validate(spec)`` with the public
        field surface set but no transport wiring. ``attach`` injects
        it so :meth:`connect` / :meth:`close` / :meth:`list_raw_tools`
        can talk to the gateway.

        Args:
            gateway (`GatewayClient`):
                Facade dispatching calls through
                :meth:`GatewayClient.exec_request`.
            connected (`bool`, defaults to `False`):
                When ``True``, mark this client as already connected
                (used by :meth:`GatewayClient.list_mcps` for entries
                the gateway is already serving).
        """
        self._gateway = gateway
        if connected:
            self._is_connected = True

    async def connect(self) -> None:
        """Register this MCP on the gateway via ``POST /mcps``.

        All MCPs (stateless and stateful) must be registered so that
        ``/mcps/{name}/tools/{tool}`` can locate the client.

        Raises:
            `RuntimeError`:
                Already connected, gateway unreachable, or gateway
                returned 4xx/5xx.
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
        """Deregister this MCP via ``DELETE /mcps/{name}``.

        Args:
            ignore_errors (`bool`, defaults to `True`):
                Suppress "not connected" precondition and gateway
                4xx/5xx. Mirrors :meth:`MCPClient.close`.
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
        """Fetch upstream tools via ``GET /mcps/{name}/tools``.

        Returns raw :class:`mcp.types.Tool` descriptors (upstream names,
        no ``mcp__`` prefix) so the inherited :meth:`get_tool` can
        re-wrap them like a local :class:`MCPClient`. The unfiltered
        list is cached; the returned list has ``enable_tools`` /
        ``disable_tools`` applied.

        Raises:
            `RuntimeError`:
                Gateway returned non-2xx.
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

        # Gateway returns the unfiltered upstream view; honour the same
        # enable/disable filtering ``MCPClient`` applies locally.
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
        searches the unfiltered cache so tools that ``enable_tools`` /
        ``disable_tools`` would have hidden are still resolvable —
        matching :meth:`MCPClient.get_tool`.

        Raises:
            `ValueError`:
                No tool with that upstream name exists on the gateway.
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
        *,
        timeout: float | None = None,
        inline_limit: int = BODY_INLINE_LIMIT,
        tmp_dir: str = SANDBOX_TMP_DIR,
        gateway_log_path: str | None = None,
        auth_token: str | None = None,
        instance_nonce: str | None = None,
    ) -> None:
        """Build a workspace-side gateway facade.

        Args:
            backend (`BackendBase`):
                Workspace backend; every request runs as
                ``backend.exec_shell([...])`` inside the sandbox.
            gateway_port (`int`):
                TCP port the gateway listens on inside the sandbox.
                The shim dials ``http://127.0.0.1:<gateway_port>``.
            timeout (`float | None`, defaults to `None`):
                Per-request timeout forwarded to
                :meth:`BackendBase.exec_shell`; ``None`` waits
                indefinitely.
            inline_limit (`int`, defaults to `BODY_INLINE_LIMIT`):
                Response bodies above this size spill through a sandbox
                tempfile rather than base64-inline through stdout.
            tmp_dir (`str`, defaults to `SANDBOX_TMP_DIR`):
                Sandbox directory for request/response tempfiles. Must
                be writable by the gateway process.
            gateway_log_path (`str | None`, defaults to `None`):
                Sandbox-side path of the gateway's stdout/stderr log
                file. When set, :meth:`exec_request` failures trigger
                a ``/health`` probe and — if the gateway is
                unreachable — a tail of this log is emitted at
                ``ERROR`` level to help diagnose crashes.
            auth_token (`str | None`, defaults to `None`):
                Optional bearer token forwarded to the gateway by the
                in-sandbox shim.
            instance_nonce (`str | None`, defaults to `None`):
                Optional nonce expected from ``/health``. Used by shared
                network backends to make sure the probed port belongs to the
                gateway process that was just launched before sending auth.
        """
        self.backend = backend
        self.gateway_port = gateway_port
        self.timeout = timeout
        self.inline_limit = inline_limit
        self.tmp_dir = tmp_dir
        self.gateway_log_path = gateway_log_path
        self.auth_token = auth_token
        self.instance_nonce = instance_nonce
        # Health-probe timeout is kept short so the diagnostic path adds
        # little latency to the failing request. It only runs on the
        # error path, never on the hot path.
        self._health_probe_timeout: float = 5.0
        # Number of bytes of the gateway log to tail into the error
        # log on unreachable-gateway diagnosis.
        self._log_tail_bytes: int = 4000

    async def health(self) -> bool:
        """Probe ``/health``.
        any other outcome (shim transport failure, non-200) → ``False``.
        With no expected ``instance_nonce``, HTTP 200 means healthy. When a
        nonce is configured, the response must be a JSON object containing the
        matching ``instance_nonce``. Transport failures, non-200 responses,
        malformed JSON, non-object JSON, and nonce mismatches return
        ``False``.

        The ``/health`` path is treated specially by
        :meth:`exec_request` — it never triggers the failure
        diagnostic, so a dead gateway does not recursively probe
        itself.
        """
        try:
            status, body = await self.exec_request(
                "GET",
                "/health",
                include_auth=False,
            )
        except Exception:
            return False
        if status != 200:
            return False
        if self.instance_nonce is None:
            return True
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError):
            return False
        if not isinstance(payload, dict):
            return False
        nonce = payload.get("instance_nonce")
        return (
            isinstance(nonce, str)
            and nonce.isascii()
            and self.instance_nonce.isascii()
            and secrets.compare_digest(nonce, self.instance_nonce)
        )

    async def list_mcps(self) -> list[GatewayMCPClient]:
        """Fetch every MCP the gateway is currently serving.

        Returned clients are marked already-connected (via
        :meth:`GatewayMCPClient.attach`) — the gateway is already
        maintaining their upstream sessions.

        Raises:
            `RuntimeError`:
                Gateway returned non-2xx.
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

        Args:
            spec (`dict[str, Any]`):
                ``MCPClient.model_dump(mode="json")`` payload — either
                from ``GET /mcps`` or from user input via ``add_mcp``.
            connected (`bool`, defaults to `False`):
                Mark the client as already-connected. Set by
                :meth:`list_mcps`; leave ``False`` when the caller will
                ``await client.connect()`` itself.
        """
        client = GatewayMCPClient.model_validate(spec)
        client.attach(self, connected=connected)
        return client

    async def aclose(self) -> None:
        """No-op kept for API parity — the transport holds no host-side
        resources, but callers keep their shutdown idiom.
        """
        return

    # ── transport ─────────────────────────────────────────────────

    async def exec_request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        include_auth: bool = True,
    ) -> tuple[int, bytes]:
        """Relay one HTTP request through the sandbox.

        Writes ``body`` (if any) to a sandbox tempfile, runs
        ``python3 -c <SHIM_SCRIPT> ...`` inside the sandbox via
        :meth:`BackendBase.exec_shell`, and parses the JSON envelope
        the shim prints on stdout. Inline bodies are base64-decoded;
        oversized bodies are pulled back through ``body_file``. Request
        and response tempfiles are best-effort cleaned up.

        On any failure — shim non-zero exit, non-JSON stdout, or
        ``status == -1`` transport error — a self-diagnostic step
        probes ``/health`` and, if the gateway is unreachable, tails
        :attr:`gateway_log_path` at ``ERROR`` level so the real crash
        cause reaches the host log stream. The original exception is
        always re-raised so the caller's error contract is unchanged.
        The ``/health`` path itself skips diagnosis to avoid recursing
        on a dead gateway.

        Args:
            method (`str`):
                HTTP verb (``GET`` / ``POST`` / ``DELETE``).
            path (`str`):
                Path-only URL, e.g. ``/mcps/<name>/tools/<tool>``.
            body (`Any`, optional):
                JSON-serializable request body; ``None`` for no body.
            include_auth (`bool`, defaults to `True`):
                Whether to send the configured bearer token to the shim.
                Health probes set this to ``False`` so a port-race cannot
                leak the token to a process that is not the gateway.

        Returns:
            `tuple[int, bytes]`:
                Status code + raw response bytes (callers decode).

        Raises:
            `RuntimeError`:
                Shim crash (non-zero exit / non-JSON stdout) or
                transport failure (``status == -1``).
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
            try:
                result: "ExecResult" = await self.backend.exec_shell(
                    [
                        "python3",
                        "-c",
                        SHIM_SCRIPT,
                        method,
                        f"http://127.0.0.1:{self.gateway_port}{path}",
                        body_file,
                        str(self.inline_limit),
                        self.tmp_dir,
                        (self.auth_token or "") if include_auth else "",
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
        except Exception as exc:
            # ``/health`` never triggers diagnosis — otherwise a dead
            # gateway would recursively probe itself.
            if path != "/health":
                await self._diagnose_failure(method, path, exc)
            raise

    async def _diagnose_failure(
        self,
        method: str,
        path: str,
        exc: BaseException,
    ) -> None:
        """Best-effort post-failure diagnostic invoked by
        :meth:`exec_request` on any request failure.

        Probes ``/health`` (via :meth:`health`, which short-circuits
        the diagnostic path); if the gateway does not answer, emits
        the tail of :attr:`gateway_log_path` at ``ERROR`` level so the
        real crash cause reaches the host log stream. Every step is
        guarded — diagnosis must never raise, so the caller's original
        exception is always the one that surfaces.

        Args:
            method (`str`):
                HTTP verb of the failed request (for log context).
            path (`str`):
                Path of the failed request (for log context).
            exc (`BaseException`):
                Original exception raised by the shim call (for log
                context; not re-raised here).
        """
        try:
            healthy = await self.health()
        except Exception:  # pragma: no cover — defensive
            healthy = False

        if healthy:
            # Gateway is up — the failure came from the request itself
            # (bad payload, upstream MCP error, etc.). Original error
            # is enough context.
            return

        logger.error(
            "Gateway unreachable during %s %s: %s. Probing /health failed. "
            "Attempting to tail gateway log at %r ...",
            method,
            path,
            exc,
            self.gateway_log_path,
        )

        if self.gateway_log_path is None:
            return

        try:
            log_bytes = await self.backend.read_file(self.gateway_log_path)
        except Exception as read_exc:  # noqa: BLE001
            logger.error(
                "Failed to read gateway log at %r: %s",
                self.gateway_log_path,
                read_exc,
            )
            return

        tail = log_bytes[-self._log_tail_bytes :].decode(errors="replace")
        logger.error(
            "Gateway log tail (last %d bytes of %r):\n%s",
            len(tail),
            self.gateway_log_path,
            tail,
        )


# ── module-private utilities ───────────────────────────────────────


def _safe_detail(status: int, body: bytes) -> str:
    """Best-effort ``HTTP <status>: <detail>`` string from a gateway
    error response — tolerates non-JSON, missing ``detail``, etc.
    """
    try:
        data = json.loads(body)
    except Exception:
        return f"HTTP {status}: {body[:200].decode(errors='replace')}"
    if isinstance(data, dict) and "detail" in data:
        return f"HTTP {status}: {data['detail']}"
    return f"HTTP {status}: {str(data)[:200]}"
