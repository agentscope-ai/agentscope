# -*- coding: utf-8 -*-
"""Sandbox — the single agent-facing proxy class.

Lifecycle: ``start()`` → use → ``close()``.  Use as async context manager.
``close()`` calls ``connection.destroy()`` — full resource cleanup.
"""

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import frontmatter
import mcp.types as mtypes
from mcp.types import Tool as MCPToolSchema

from ..skill import Skill
from ..tool import MCPTool
from .._logging import logger
from .config import SandboxConfig, ToolDefinition
from .connection import SandboxConnection, create_sandbox_connection
from .mcp_gateway import MCPGateway
from .types import SandboxInitializationConfig

# ---------------------------------------------------------------------------
# File accessor facade — sandbox.file.read / sandbox.file.write
# ---------------------------------------------------------------------------


class FileAccessor:
    """Thin facade so callers can write ``sandbox.file.read(path)``."""

    def __init__(self, conn: SandboxConnection) -> None:
        """Wrap a connection for path-oriented read/write."""
        self._conn = conn

    async def read(self, path: str) -> bytes:
        """Read bytes from a sandbox-relative path."""
        return await self._conn.read(path)

    async def write(self, path: str, data: bytes) -> None:
        """Write bytes to a sandbox-relative path."""
        return await self._conn.write(path, data)


# ---------------------------------------------------------------------------
# Tool / Skill / MCP internal registries
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _ToolEntry:
    definition: ToolDefinition
    source: str = "static"  # "static" | "mcp" | "skill"


@dataclass(slots=True)
class _MCPServerHandle:
    name: str
    command: str
    pid: int | None = None


@dataclass(slots=True)
class _SkillEntry:
    name: str
    path: str
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Sandbox — the single agent-facing class
# ---------------------------------------------------------------------------


class Sandbox:
    """Agent-side proxy to one running sandbox.

    **Construction:** pass ``SandboxConfig``; ``config.backend.type`` is used
    by ``create_sandbox_connection(options)`` to dispatch to the correct
    ``SandboxConnection`` subclass.

    Lifecycle: ``start()`` → use → ``close()``.  Use as async context manager.
    ``close()`` calls ``connection.destroy()`` — full resource cleanup.
    """

    def __init__(self, config: SandboxConfig) -> None:
        self._config = config
        self._conn: SandboxConnection | None = None
        self._id: str = uuid.uuid4().hex[:12]
        self._started = False

        self._tools: dict[str, _ToolEntry] = {}
        self._exec_mcp_servers: dict[str, _MCPServerHandle] = {}
        self._skills: dict[str, _SkillEntry] = {}

        self._gateway: MCPGateway | None = None

    @property
    def sandbox_id(self) -> str:
        """Stable id for this sandbox instance."""
        return self._id

    @property
    def backend_type(self) -> str:
        """Backend id string from config (``local_temp``, ``docker``, …)."""
        return self._config.backend.type

    @property
    def started(self) -> bool:
        """Whether ``start()`` has completed successfully."""
        return self._started

    @property
    def connection(self) -> SandboxConnection:
        """Low-level backend connection (must call ``start()`` first)."""
        if not self._conn:
            raise RuntimeError("Sandbox not started — call start() first")
        return self._conn

    @property
    def file(self) -> FileAccessor | None:
        """Path-oriented read/write facade (available after ``start()``)."""
        return FileAccessor(self._conn) if self._conn else None

    @property
    def gateway(self) -> MCPGateway | None:
        """MCP gateway instance when gateway mode is enabled."""
        return self._gateway

    # ─── lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        """Provision backend, tools/skills scan, and MCP servers."""
        if self._started:
            return

        self._conn = await self._create_connection()

        for td in self._config.tools:
            self._tools[td.name] = _ToolEntry(definition=td, source="static")

        if self._config.skills:
            await self._register_skills()

        if self._config.mcp_gateway.enabled and self._config.mcp_servers:
            await self._start_gateway()
        else:
            for mcp_cfg in self._config.mcp_servers:
                await self._start_mcp_server(
                    mcp_cfg.name,
                    mcp_cfg.command,
                    mcp_cfg.args,
                    mcp_cfg.env,
                )

        self._started = True

    async def close(self) -> None:
        """Destroy the underlying sandbox (hard cleanup)."""
        if self._gateway:
            await self._gateway.close()
            self._gateway = None
        if self._conn:
            await self._conn.destroy()
            self._conn = None
        self._started = False

    async def __aenter__(self) -> "Sandbox":
        """Enter async context: ``await start()``."""
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        """Exit async context: ``await close()``."""
        await self.close()

    # ─── tool surface ─────────────────────────────────────────

    async def list_tools(self) -> list[MCPTool]:
        """Return ``MCPTool`` instances for all registered tools.

        Includes both static ``ToolDefinition`` entries and tools aggregated
        by the ``MCPGateway`` (if enabled).
        """
        mcp_name = self._config.mcp_gateway.mcp_name
        session = _SandboxSession(self)
        tools: list[MCPTool] = []

        for entry in self._tools.values():
            td = entry.definition
            schema = dict(td.parameters) if td.parameters else {}
            if not schema or "type" not in schema:
                schema = {"type": "object", "properties": schema}
            mcp_tool = MCPToolSchema(
                name=td.name,
                description=td.description or "",
                inputSchema=schema,
            )
            tools.append(
                MCPTool(mcp_name=mcp_name, tool=mcp_tool, session=session),
            )

        if self._gateway:
            seen = {t.name for t in tools}
            for gw_tool in await self._gateway.list_tools():
                if gw_tool.name not in seen:
                    tools.append(
                        MCPTool(
                            mcp_name=mcp_name,
                            tool=gw_tool,
                            session=session,
                        ),
                    )
                    seen.add(gw_tool.name)

        return tools

    async def call_tool(
        self,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> Any:
        """Dispatch a tool call.

        Resolution order:
        1. MCPGateway (if enabled and tool is known to the gateway).
        2. Local tool registry (static ToolDefinition with shell_cmd).
        """
        args = args or {}

        if self._gateway and self._gateway.has_tool(name):
            return await self._gateway.call_tool(name, args)

        entry = self._tools.get(name)
        if not entry:
            raise KeyError(
                f"Tool {name!r} not found. Available: {list(self._tools)}",
            )
        return await self._run_tool_handler(entry, args)

    # ─── skill surface ────────────────────────────────────────

    async def list_skills(self) -> list[Skill]:
        """Return :class:`agentscope.skill.Skill` for each registered skill.

        When ``SKILL.md`` exists under the skill directory, it is parsed with
        ``python-frontmatter`` (same as :class:`LocalSkillLoader`). Otherwise
        a minimal ``Skill`` is built from scan / import metadata.
        """
        return [
            await self._skill_entry_to_skill(s) for s in self._skills.values()
        ]

    def _resolve_skill_dir(self, entry: _SkillEntry) -> str:
        """Host path to the skill dir when ``workspace_root`` is available."""
        root = getattr(self._conn, "workspace_root", None)
        if root:
            return str((Path(root) / entry.path).resolve())
        return entry.path

    async def _skill_entry_to_skill(self, entry: _SkillEntry) -> Skill:
        skill_dir = self._resolve_skill_dir(entry)
        rel_md = f"{entry.path.rstrip('/')}/SKILL.md"
        fallback_desc = (
            str(entry.metadata.get("description", "") or "").strip()
            or f"Skill at {entry.path}"
        )

        try:
            raw = await self.connection.read(rel_md)
        except (FileNotFoundError, OSError):
            return Skill(
                name=entry.name,
                description=fallback_desc,
                dir=skill_dir,
                markdown="",
                updated_at=0.0,
            )

        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")

        doc = frontmatter.loads(text)
        name = doc.get("name")
        name_str = str(name).strip() if name else entry.name
        desc_raw = doc.get("description")
        if desc_raw and str(desc_raw).strip():
            desc_str = str(desc_raw)
        else:
            desc_str = fallback_desc
        body = doc.content
        if isinstance(body, str):
            markdown = body
        elif body:
            markdown = str(body)
        else:
            markdown = ""

        updated_at = 0.0
        try:
            md_host = (Path(skill_dir) / "SKILL.md").resolve()
            if md_host.exists():
                updated_at = float(md_host.stat().st_mtime)
        except OSError:
            pass

        return Skill(
            name=name_str,
            description=desc_str,
            dir=skill_dir,
            markdown=markdown,
            updated_at=updated_at,
        )

    async def import_skills(self, spec: str | list[str]) -> None:
        """Copy skill trees into the configured skills directory."""
        if isinstance(spec, str):
            spec = [spec]
        skills_dir = (
            self._config.skills.skills_dir
            if self._config.skills
            else "/root/skills"
        )
        for s in spec:
            cmd = (
                f"cp -r {s} {skills_dir}/ 2>/dev/null || "
                f"echo '__import_placeholder:{s}'"
            )
            await self.connection.exec(cmd, timeout=60)
            name = s.rsplit("/", 1)[-1]
            self._skills[name] = _SkillEntry(
                name=name,
                path=f"{skills_dir}/{name}",
            )
            logger.info("Imported skill %r into sandbox %s", name, self._id)

    # ─── MCP surface ──────────────────────────────────────────

    async def list_mcps(self) -> list[dict[str, Any]]:
        """List managed MCP servers (gateway-managed or exec-started)."""
        if self._gateway:
            return self._gateway.list_servers()
        return [
            {"name": h.name, "command": h.command, "pid": h.pid}
            for h in self._exec_mcp_servers.values()
        ]

    async def add_mcp(
        self,
        name: str,
        command: str,
        *,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        """Start an MCP server via exec (disabled when gateway mode is on)."""
        if self._gateway:
            raise RuntimeError(
                "Dynamic add_mcp is not supported when MCPGateway "
                "is enabled (one-period limitation)",
            )
        await self._start_mcp_server(name, command, args or [], env or {})

    async def remove_mcp(self, name: str) -> None:
        """Stop a dynamically added MCP server (non-gateway mode only)."""
        if self._gateway:
            raise RuntimeError(
                "Dynamic remove_mcp is not supported when MCPGateway "
                "is enabled (one-period limitation)",
            )
        handle = self._exec_mcp_servers.pop(name, None)
        if handle and handle.pid:
            kill_cmd = f"kill {handle.pid} 2>/dev/null || true"
            await self.connection.exec(kill_cmd, timeout=5)
        self._tools = {
            k: v
            for k, v in self._tools.items()
            if not (v.source == "mcp" and v.definition.shell_cmd == name)
        }

    # ─── run (general request dispatch) ───────────────────────

    async def run(self, request: str | dict[str, Any]) -> Any:
        """High-level entry point for agents.

        ``str`` → exec; ``dict`` with ``tool`` key → call_tool.
        """
        if isinstance(request, str):
            return await self.connection.exec(request)
        if isinstance(request, dict):
            tool = request.get("tool")
            if tool:
                return await self.call_tool(tool, request.get("args", {}))
        msg = f"Cannot interpret request: {request!r}"
        raise ValueError(msg)

    # ─── internal helpers ─────────────────────────────────────

    async def _create_connection(self) -> SandboxConnection:
        opts = self._merge_infra_requirements()
        return await create_sandbox_connection(opts)

    def _merge_infra_requirements(self) -> SandboxInitializationConfig:
        """Merge implied ports, volumes, and env into create options."""
        cfg = self._config
        ports = list(cfg.exposed_ports)
        volumes = dict(cfg.volumes)
        env = dict(cfg.env)

        if cfg.mcp_gateway.enabled:
            if cfg.mcp_gateway.port not in ports:
                ports.append(cfg.mcp_gateway.port)

        if cfg.skills and cfg.skills.persist and cfg.skills.host_dir:
            volumes[cfg.skills.host_dir] = cfg.skills.skills_dir

        extra = dict(cfg.backend.extra)
        if hasattr(cfg.backend, "image"):
            extra["image"] = cfg.backend.image  # type: ignore[attr-defined]
        if hasattr(cfg.backend, "template"):
            t = cfg.backend.template  # type: ignore[attr-defined]
            extra["template"] = t
        if hasattr(cfg.backend, "base_dir"):
            b = cfg.backend.base_dir  # type: ignore[attr-defined]
            extra["base_dir"] = b
        if hasattr(cfg.backend, "api_key"):
            ak = cfg.backend.api_key  # type: ignore[attr-defined]
            if ak:
                extra["api_key"] = ak
        if hasattr(cfg.backend, "domain"):
            dm = cfg.backend.domain  # type: ignore[attr-defined]
            if dm:
                extra["domain"] = dm
        if hasattr(cfg.backend, "timeout"):
            extra[
                "timeout"
            ] = cfg.backend.timeout  # type: ignore[attr-defined]
        if hasattr(cfg.backend, "metadata"):
            md = cfg.backend.metadata  # type: ignore[attr-defined]
            if md:
                extra["metadata"] = md
        if hasattr(cfg.backend, "env"):
            ev = cfg.backend.env  # type: ignore[attr-defined]
            if ev:
                extra["env"] = ev
        if cfg.endpoint:
            extra["endpoint"] = cfg.endpoint

        return SandboxInitializationConfig(
            backend_id=cfg.backend.type,
            env=env,
            exposed_ports=ports,
            volumes=volumes,
            startup_commands=list(cfg.startup_commands),
            extra=extra,
        )

    async def _start_gateway(self) -> None:
        """Create and start the MCPGateway with all configured MCP servers."""
        ws_root = getattr(self._conn, "workspace_root", None)
        cwd = str(ws_root) if ws_root else None
        self._gateway = MCPGateway(self._config.mcp_gateway)
        await self._gateway.start(self._config.mcp_servers, cwd=cwd)

    async def _start_mcp_server(
        self,
        name: str,
        command: str,
        args: list[str],
        env: dict[str, str],
    ) -> None:
        """Start an MCP server via exec (non-gateway fallback)."""
        env_prefix = " ".join(f"{k}={v}" for k, v in env.items())
        args_str = " ".join(args)
        full_cmd = f"{env_prefix} nohup {command} {args_str} &".strip()
        r = await self.connection.exec(full_cmd, timeout=30)
        handle = _MCPServerHandle(name=name, command=command)
        if r.is_ok():
            pid_line = (
                r.stdout.decode(errors="replace").strip().split("\n")[-1]
            )
            try:
                handle.pid = int(pid_line)
            except ValueError:
                pass
        else:
            logger.warning(
                "MCP server %r failed to start in sandbox %s "
                "(exit_code=%s, stderr=%s)",
                name,
                self._id,
                r.exit_code,
                r.stderr.decode(errors="replace").strip()[:200],
            )
        self._exec_mcp_servers[name] = handle
        logger.info(
            "Started MCP server %r in sandbox %s (pid=%s)",
            name,
            self._id,
            handle.pid,
        )

    async def _register_skills(self) -> None:
        """Fill ``_skills`` by listing the configured skills directory."""
        if not self._config.skills:
            return
        skills_dir = self._config.skills.skills_dir
        ls_cmd = f"ls {skills_dir} 2>/dev/null || true"
        r = await self.connection.exec(ls_cmd, timeout=10)
        if not r.is_ok():
            return
        for name in r.stdout.decode(errors="replace").strip().split("\n"):
            name = name.strip()
            if name:
                self._skills[name] = _SkillEntry(
                    name=name,
                    path=f"{skills_dir}/{name}",
                )

    async def _run_tool_handler(
        self,
        entry: _ToolEntry,
        args: dict[str, Any],
    ) -> Any:
        shell_cmd = entry.definition.shell_cmd
        if not shell_cmd:
            raise RuntimeError(
                f"Tool {entry.definition.name!r} has no shell_cmd configured",
            )
        args_json = json.dumps(args)
        r = await self.connection.exec(
            f"{shell_cmd} '{args_json}'",
            timeout=120,
        )
        return {
            "exit_code": r.exit_code,
            "stdout": r.stdout.decode(errors="replace"),
            "stderr": r.stderr.decode(errors="replace"),
        }


# ---------------------------------------------------------------------------
# Duck-typed session for MCPTool
# ---------------------------------------------------------------------------


class _SandboxSession:
    """Bridge ``MCPTool`` to :meth:`Sandbox.call_tool` for MCP-shaped calls."""

    def __init__(self, sandbox: Sandbox) -> None:
        """Hold the parent sandbox for delegated tool calls."""
        self._sandbox = sandbox

    async def call_tool(
        self,
        name: str,
        *,
        arguments: dict[str, Any] | None = None,
        read_timeout_seconds: Any = None,
    ) -> Any:
        """Bridge ``MCPTool`` to :meth:`Sandbox.call_tool`."""
        del read_timeout_seconds  # MCPTool protocol (reserved)
        raw = await self._sandbox.call_tool(name, arguments or {})
        if isinstance(raw, mtypes.CallToolResult):
            return raw
        if isinstance(raw, str):
            text = raw
        else:
            text = json.dumps(raw, ensure_ascii=False, default=str)
        return mtypes.CallToolResult(
            content=[mtypes.TextContent(type="text", text=text)],
            isError=False,
        )
