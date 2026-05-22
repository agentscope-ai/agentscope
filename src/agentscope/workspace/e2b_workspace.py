# -*- coding: utf-8 -*-
"""E2BWorkspace — E2B cloud-sandbox workspace.

Similar to :class:`DockerWorkspace` but uses the E2B SDK
(``e2b.AsyncSandbox``) as the backend. The in-container MCP gateway
runs inside the E2B sandbox; the host connects to it over HTTPS.

Requires ``e2b``::

    pip install e2b
"""

import asyncio
import base64
import hashlib
import mimetypes
import posixpath
import shlex
from copy import deepcopy
from typing import Any, ClassVar

from pydantic import Field, PrivateAttr

from .._logging import logger
from ..message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
)
from ..skill import Skill
from ..tool import ToolBase
from .mcp_enhanced_workspace import WorkspaceWithMCP
from .types import ExecutionResult, SerializedWorkspaceState

_DEFAULT_INSTRUCTIONS = """<workspace>
You have access to an E2B cloud-sandbox workspace.

All tool calls are executed inside the remote sandbox. Use the tools
provided by the MCP servers to interact with the sandbox's filesystem
and processes.
</workspace>"""


class E2BWorkspace(WorkspaceWithMCP):
    """Workspace backed by an E2B cloud sandbox.

    Similar to :class:`DockerWorkspace` but uses the E2B SDK
    (``e2b.AsyncSandbox``) as the compute backend.  The MCP gateway
    runs inside the sandbox and is accessed over HTTPS via E2B's
    proxy.

    Args:
        template: E2B sandbox template identifier.
            Defaults to ``"base"``.
        api_key: E2B API key for authentication.  If empty, the
            SDK falls back to the ``E2B_API_KEY`` environment
            variable.
        domain: Optional custom E2B domain.
        timeout_seconds: Sandbox keep-alive timeout in seconds.
            Defaults to ``300``.
        working_dir: Working directory inside the sandbox.
            Defaults to ``"/home/user"``.
        env: Environment variables to set inside the sandbox
            as ``{key: value}``.
        metadata: Arbitrary metadata attached to the sandbox
            instance as ``{key: value}``.
        startup_commands: Shell commands to run inside the sandbox
            after creation (before the MCP gateway starts).  A
            non-zero exit code from any command aborts initialization.
        instructions: Workspace-specific system prompt fragment
            returned by :meth:`get_instructions`.
        mcp_servers: MCP server configurations (inherited from
            :class:`WorkspaceWithMCP`).
        gateway_port: MCP gateway port (inherited from
            :class:`WorkspaceWithMCP`).  Defaults to ``5600``.

    Usage::

        workspace = E2BWorkspace(
            template="base",
            api_key="...",
            mcp_servers=[...],
        )
        await workspace.initialize()
        agent = Agent(..., workspace=workspace)
        # ...
        await workspace.close()
    """

    # ── class-level constants ─────────────────────────────────────

    DEFAULT_TEMPLATE: ClassVar[str] = "base"
    DEFAULT_WORKING_DIR: ClassVar[str] = "/home/user"
    DEFAULT_TIMEOUT: ClassVar[int] = 300
    GATEWAY_PORT: ClassVar[int] = 5600
    SKILLS_DIR: ClassVar[str] = "/home/user/skills"

    # ── serializable configuration fields ─────────────────────────

    template: str = DEFAULT_TEMPLATE
    api_key: str = ""
    domain: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT
    working_dir: str = DEFAULT_WORKING_DIR
    env: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, str] = Field(default_factory=dict)
    startup_commands: list[str] = Field(default_factory=list)
    instructions: str = _DEFAULT_INSTRUCTIONS

    # ── runtime state (excluded from serialisation) ───────────────

    _sandbox: Any = PrivateAttr(default=None)  # e2b.AsyncSandbox

    @property
    def sandbox_id(self) -> str | None:
        """E2B sandbox ID, or ``None`` if not started."""
        return self._sandbox.sandbox_id if self._sandbox else None

    # ── lifecycle ──────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Provision the E2B sandbox, run startup commands, start gateway."""
        if self._started:
            return

        from e2b import AsyncSandbox

        create_kwargs: dict[str, Any] = {
            "template": self.template,
            "timeout": self.timeout_seconds,
        }
        if self.api_key:
            create_kwargs["api_key"] = self.api_key
        if self.domain:
            create_kwargs["domain"] = self.domain
        if self.metadata:
            create_kwargs["metadata"] = self.metadata
        if self.env:
            create_kwargs["envs"] = self.env

        self._sandbox = await AsyncSandbox.create(**create_kwargs)

        await self._exec(f"mkdir -p {self.working_dir}")

        for cmd in self.startup_commands:
            r = await self._exec(cmd)
            if not r.is_ok():
                raise RuntimeError(
                    "E2BWorkspace startup_commands failed "
                    f"(exit {r.exit_code}) for: {cmd!r}\n"
                    f"stderr: {r.stderr.decode(errors='replace')}\n"
                    f"stdout: {r.stdout.decode(errors='replace')}",
                )

        await super().initialize()
        self._started = True

    async def reset(self) -> None:
        """Reset sandbox workspace to a clean state.

        Clears session data and offloaded files inside the sandbox.
        """
        await self._exec(
            f"rm -rf {self.working_dir}/sessions {self.working_dir}/data",
        )

    async def is_alive(self) -> bool:
        """Check infrastructure-level liveness of the sandbox.

        Returns ``True`` if the underlying E2B sandbox process is
        still running.  This is a *resource-level* health check — it
        does **not** verify that MCP servers or skills are functional.
        A sandbox with no MCP servers configured is still considered
        alive as long as the sandbox process itself is running.
        """
        if not self._sandbox or not self._started:
            return False
        try:
            return await self._sandbox.is_running()
        except Exception:
            return False

    async def close(self) -> None:
        """Kill the E2B sandbox and release resources."""
        if self._gateway_mcp_client and self._gateway_mcp_client.is_connected:
            try:
                await self._gateway_mcp_client.close()
            except Exception:
                pass
            self._gateway_mcp_client = None

        if self._sandbox:
            try:
                await self._sandbox.kill()
            except Exception:
                pass
            self._sandbox = None
        self._started = False

    # ── instructions ───────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the workspace-specific system prompt fragment."""
        return self.instructions

    # ── tool & MCP discovery ───────────────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        """No built-in tools — all tools come via the MCP gateway."""
        return []

    # list_mcps is provided by WorkspaceWithMCP

    # ── skill discovery ────────────────────────────────────────────

    async def list_skills(self) -> list[Skill]:
        """Discover skills by scanning ``SKILLS_DIR`` inside the sandbox."""
        import frontmatter as fm

        r = await self._exec(
            f"find {self.SKILLS_DIR} -name SKILL.md 2>/dev/null || true",
        )
        if not r.is_ok():
            return []
        stdout = r.stdout.decode(errors="replace").strip()
        if not stdout:
            return []

        skills: list[Skill] = []
        for md_path in stdout.split("\n"):
            md_path = md_path.strip()
            if not md_path:
                continue
            try:
                raw = await self._sandbox.files.read(md_path, format="bytes")
                text = bytes(raw).decode("utf-8")
                doc = fm.loads(text)
                name = doc.get("name")
                desc = doc.get("description")
                if not name or not desc:
                    continue
                skills.append(
                    Skill(
                        name=str(name),
                        description=str(desc),
                        dir=posixpath.dirname(md_path),
                        markdown=doc.content or "",
                        updated_at=0.0,
                    ),
                )
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", md_path, e)
        return skills

    # ── offload ────────────────────────────────────────────────────

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
        **kwargs: Any,
    ) -> str:
        """Offload conversation context to a JSONL file inside the sandbox."""
        base = f"sessions/{session_id}"
        path = f"{self.working_dir}/{base}/context.jsonl"

        copied = deepcopy(msgs)
        lines: list[str] = []
        for msg in copied:
            if not isinstance(msg.content, str):
                content = []
                for block in msg.content:
                    if isinstance(block, DataBlock) and isinstance(
                        block.source,
                        Base64Source,
                    ):
                        block = await self._offload_data(block)
                    content.append(block)
                msg.content = content
            lines.append(msg.model_dump_json())

        payload = "\n".join(lines) + "\n"
        await self._exec(f"mkdir -p {self.working_dir}/{base}")

        existing = b""
        try:
            raw = await self._sandbox.files.read(path, format="bytes")
            existing = bytes(raw)
        except Exception:
            pass
        await self._sandbox.files.write(
            path,
            existing + payload.encode("utf-8"),
        )
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
        **kwargs: Any,
    ) -> str:
        """Persist a tool result inside the sandbox."""
        base = f"sessions/{session_id}"
        path = f"{self.working_dir}/{base}/tool_result-{tool_result.id}.txt"

        parts: list[str] = []
        if isinstance(tool_result.output, str):
            parts.append(tool_result.output)
        else:
            for block in tool_result.output:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        d = await self._offload_data(block)
                        url = str(d.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await self._exec(f"mkdir -p {self.working_dir}/{base}")
        await self._sandbox.files.write(
            path,
            "".join(parts).encode("utf-8"),
        )
        return path

    # add_mcp / remove_mcp are provided by WorkspaceWithMCP

    # ── dynamic skill management ───────────────────────────────────

    async def add_skill(self, skill_path: str) -> None:
        """Copy a local skill directory into the sandbox via file write."""
        import os

        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path}: SKILL.md not found",
            )

        dir_name = os.path.basename(os.path.abspath(skill_path))
        await self._exec(f"mkdir -p {self.SKILLS_DIR}")

        for root, _dirs, files in os.walk(skill_path):
            for fname in files:
                local = os.path.join(root, fname)
                rel = os.path.relpath(local, skill_path)
                remote = f"{self.SKILLS_DIR}/{dir_name}/{rel}"
                with open(local, "rb") as f:
                    data = f.read()
                await self._sandbox.files.write(remote, data)

        logger.info("E2BWorkspace: added skill %r", dir_name)

    async def remove_skill(self, name: str) -> None:
        """Remove a skill directory from the sandbox by name."""
        skills = await self.list_skills()
        target_dir: str | None = None
        for skill in skills:
            if skill.name == name:
                target_dir = skill.dir
                break
        if target_dir is None:
            available = [s.name for s in skills]
            raise KeyError(
                f"Skill {name!r} not found. Available: {available}",
            )
        dest = shlex.quote(target_dir)
        r = await self._exec(f"rm -rf {dest}")
        if not r.is_ok():
            raise RuntimeError(
                f"Failed to remove skill {name!r}: {r.stderr.decode()}",
            )
        logger.info("E2BWorkspace: removed skill %r", name)

    # ── export state ───────────────────────────────────────────────

    async def export_state(self) -> SerializedWorkspaceState:
        """Serialize workspace identity for later restore.

        Credentials (api_key, domain) are managed by the manager and
        are intentionally excluded from the serialized state.
        """
        return SerializedWorkspaceState(
            backend_type="e2b",
            payload={
                "sandbox_id": self._sandbox.sandbox_id,
                "workspace_id": self.workspace_id,
                "working_dir": self.working_dir,
            },
        )

    # ── internal: exec ─────────────────────────────────────────────

    async def _exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> ExecutionResult:
        """Execute a shell command inside the E2B sandbox (with retry).

        Args:
            command: Shell command string to execute.
            timeout: Maximum seconds to wait. ``None`` means
                no limit.
        """
        run_kwargs: dict[str, Any] = {"cwd": self.working_dir}
        if timeout is not None:
            run_kwargs["timeout"] = timeout

        delay = 1.0
        for attempt in range(5):
            try:
                result = await self._sandbox.commands.run(
                    command,
                    **run_kwargs,
                )
                return ExecutionResult(
                    exit_code=result.exit_code,
                    stdout=(result.stdout or "").encode("utf-8"),
                    stderr=(result.stderr or "").encode("utf-8"),
                )
            except Exception as e:
                msg = str(e).lower()
                transient = "pending" in msg or "not ready" in msg
                if not transient or attempt >= 4:
                    if hasattr(e, "exit_code"):
                        return ExecutionResult(
                            exit_code=e.exit_code,
                            stdout=(getattr(e, "stdout", "") or "").encode(
                                "utf-8",
                            ),
                            stderr=(getattr(e, "stderr", "") or "").encode(
                                "utf-8",
                            ),
                        )
                    raise
                await asyncio.sleep(delay)
        raise RuntimeError("unreachable")

    # ── WorkspaceWithMCP hooks ──────────────────────────────────────

    async def _write_remote(self, path: str, data: bytes) -> None:
        """Write bytes to a file in the E2B sandbox.

        Args:
            path: Absolute path inside the sandbox.
            data: Raw bytes to write.
        """
        await self._sandbox.files.write(path, data)

    async def _resolve_base_url(self, port: int) -> str:
        """Return the HTTPS URL via E2B's host resolution.

        Args:
            port: Container port to resolve.
        """
        host = self._sandbox.get_host(port)
        return f"https://{host}"

    def _platform_headers(self) -> dict[str, str]:
        """Return the ``X-Access-Token`` header for E2B proxy auth."""
        if not self._sandbox:
            return {}
        token = getattr(
            self._sandbox,
            "_SandboxBase__envd_access_token",
            None,
        )
        if token:
            return {"X-Access-Token": token}
        return {}

    async def _offload_data(self, data_block: DataBlock) -> DataBlock:
        """Decode base64 data, save in sandbox, return URL block.

        Args:
            data_block: A :class:`DataBlock` with base64 source.
        """
        h = hashlib.sha256(data_block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(data_block.source.media_type) or ".bin"
        path = f"{self.working_dir}/data/{h}{ext}"
        await self._exec(f"mkdir -p {self.working_dir}/data")
        await self._sandbox.files.write(
            path,
            base64.b64decode(data_block.source.data),
        )
        from pydantic import AnyUrl

        return DataBlock(
            id=data_block.id,
            name=data_block.name,
            source=URLSource(
                url=AnyUrl(f"file:///{path}"),
                media_type=data_block.source.media_type,
            ),
        )
