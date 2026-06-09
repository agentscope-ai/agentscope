# -*- coding: utf-8 -*-
"""DockerWorkspace — sandboxed workspace backed by a Docker container.

Architecture
------------

* Container lifecycle (build + run + stop) via **aiodocker**.
* MCP servers run *inside* the container behind a FastAPI gateway
  (see :mod:`agentscope.workspace._mcp_gateway`); the host talks to it
  over HTTP via :class:`GatewayClient` / :class:`GatewayMCPClient`.
* Optional bind-mounted host ``workdir`` makes the workspace
  persistent — ``.mcp`` (registered MCPs), ``skills/``, ``sessions/``
  and ``data/`` survive restarts. Without ``workdir`` the container
  is ephemeral.
* Image is content-hashed by Dockerfile + COPY payloads
  (see :mod:`._make_dockerfile`); a cache hit skips the build.

Persistence model mirrors :class:`agentscope.workspace.LocalWorkspace`:
on each :meth:`initialize`, MCPs are restored from ``<workdir>/.mcp``
if it exists (otherwise ``default_mcps`` are used and persisted).
Every :meth:`add_mcp` / :meth:`remove_mcp` rewrites the file.

The gateway bearer token is freshly generated on each ``initialize``
and shipped into the container via the gateway config file — it is
*not* persisted.
"""

import asyncio
import base64
import hashlib
import io
import json
import mimetypes
import os
import posixpath
import shlex
import shutil
import sys
import tarfile
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

from pydantic import AnyUrl

from ..._logging import logger
from ...mcp import MCPClient
from ...message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
)
from ...skill import Skill
from ...tool import ToolBase
from .._base import WorkspaceBase
from .._gateway_client import (
    GatewayClient,
    GatewayMCPClient,
)
from ._make_dockerfile import (
    CONTAINER_DATA_DIR,
    CONTAINER_SESSIONS_DIR,
    CONTAINER_SKILLS_DIR,
    CONTAINER_WORKDIR,
    DEFAULT_BASE_IMAGE,
    DEFAULT_GATEWAY_PORT,
    GATEWAY_CONFIG,
    GATEWAY_HOME,
    GATEWAY_LOG,
    GATEWAY_SCRIPT,
    GATEWAY_VENV,
    prepare_build_context,
)


_DEFAULT_INSTRUCTIONS = """<workspace>
You have a Docker-based workspace. All tool calls execute **inside the
container** at ``{workdir}``.

Layout:

```
{workdir}
├── data/        # offloaded multimodal files
├── skills/      # reusable skills
└── sessions/    # session context and tool results
```

Use the MCP-provided tools to interact with the container's filesystem
and processes.
</workspace>"""


# ── small helpers ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _ExecResult:
    """Result of running a command inside the container via ``docker exec``.

    Attributes:
        exit_code: Process exit code from the container.
            ``-1`` indicates the exec call timed out or the engine
            failed to report a code.
        stdout: Raw bytes captured from the command's stdout
            (channel ``1`` of the docker exec stream).
        stderr: Raw bytes captured from the command's stderr
            (channel ``2`` of the docker exec stream).
    """

    exit_code: int
    stdout: bytes
    stderr: bytes

    def ok(self) -> bool:
        """Return ``True`` iff the command exited with code ``0``."""
        return self.exit_code == 0


# ── the workspace ──────────────────────────────────────────────────


class DockerWorkspace(WorkspaceBase):
    """Workspace backed by a Docker container.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.
    """

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        base_image: str = DEFAULT_BASE_IMAGE,
        host_workdir: str | None = None,
        node_version: str | None = None,
        extra_pip: list[str] | None = None,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        env: dict[str, str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        """Construct a :class:`DockerWorkspace`.

        The workspace is *not* started here; call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Existing workspace identifier to adopt. ``None``
                generates a fresh UUID. When the same
                ``workspace_id`` is paired with a persistent
                ``workdir``, restarts are stable across processes.
            base_image (`str`, defaults to `DEFAULT_BASE_IMAGE`):
                Base Docker image. Must provide ``python3`` in
                ``$PATH`` (e.g. ``"python:3.11-slim"``). The image is
                rebuilt on top of this base via the dynamic
                Dockerfile (uv venv + agentscope install + optional
                node + ``extra_pip``).
            host_workdir (`str | None`, optional):
                Host directory bind-mounted to ``/workspace`` inside
                the container. ``None`` makes the workspace
                ephemeral — files written under ``/workspace`` live
                only in the container's writable layer and are lost
                on :meth:`close`. When set, the directory is created
                on demand and the ``.mcp`` / ``skills/`` /
                ``sessions/`` / ``data/`` layout is mirrored
                host-side.
            node_version (`str | None`, optional):
                Major Node.js version to bake into the image (e.g.
                ``"20"``). ``None`` skips Node entirely.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages to install into the gateway
                venv at image-build time.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the gateway listens on inside the
                container; always exposed to a randomly assigned
                host port.
            env (`dict[str, str] | None`, optional):
                Environment variables to set inside the container.
            instructions (`str`, defaults to `_DEFAULT_INSTRUCTIONS`):
                System-prompt fragment template returned by
                :meth:`get_instructions`. Supports the ``{workdir}``
                placeholder, replaced with the container-side path.
            default_mcps (`list[MCPClient] | None`, optional):
                Initial MCPs registered on first :meth:`initialize`.
                On subsequent restarts with a persistent ``workdir``
                these are ignored in favour of the persisted
                ``<workdir>/.mcp`` file.
            skill_paths (`list[str] | None`, optional):
                Local skill directories seeded into
                ``<workdir>/skills`` on first :meth:`initialize`
                (only when ``workdir`` is set; subsequent starts
                treat the host directory as the source of truth).
        """
        super().__init__(workspace_id=workspace_id)

        # Backwards compatibility: accept legacy ``workdir`` kwarg.
        if "workdir" in kwargs:
            logger.warning(
                "DockerWorkspace parameter 'workdir' is deprecated, "
                "use 'host_workdir' instead.",
            )
            if host_workdir is None:
                host_workdir = kwargs.pop("workdir")

        # ── serializable config ─────────────────────────────────
        self.workdir = CONTAINER_WORKDIR
        self.base_image = base_image
        self.host_workdir = host_workdir
        self.node_version = node_version
        self.extra_pip: list[str] = list(extra_pip or [])
        self.gateway_port = gateway_port
        self.env: dict[str, str] = dict(env or {})
        self.instructions = instructions

        # ── seed-only ───────────────────────────────────────────
        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

        # ── runtime state ───────────────────────────────────────
        self._client: Any = None  # aiodocker.Docker
        self._container: Any = None
        self._port_mapping: dict[int, int] = {}
        self._image_tag: str = ""
        self._gateway: GatewayClient | None = None
        self._gateway_token: str = ""
        self._mcps: list[MCPClient] = []
        self._gateway_clients: dict[str, GatewayMCPClient] = {}
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()

    # ── lifecycle ───────────────────────────────────────────────

    async def initialize(self) -> None:
        """Build / reuse the image, start the container, launch the gateway.

        Steps:

        1. Build the workspace image (or reuse a tag-cache hit).
        2. Restore MCPs from ``<workdir>/.mcp`` if present, else seed
           from ``default_mcps``.
        3. Mint a fresh gateway bearer token (not persisted).
        4. Start the container with the gateway port mapped to a host
           port and ``workdir`` (if any) bind-mounted.
        5. Drop ``gateway.config.json`` into the container, launch the
           gateway via ``python -m agentscope.workspace._mcp_gateway``,
           and wait for ``/health`` to return 200.
        6. Pull the gateway-side MCP view back as
           :class:`GatewayMCPClient` instances.
        7. Persist ``.mcp`` and seed skills (only when ``workdir`` is
           set).

        Idempotent — calling on an already-alive workspace is a no-op.

        Raises:
            RuntimeError: If the image build fails, the gateway port
                fails to bind, or the gateway does not become healthy
                within 30 seconds.
        """
        if self.is_alive:
            return

        import aiodocker

        self._client = aiodocker.Docker()

        await self._build_or_reuse_image()

        self._mcps = await self._restore_or_seed_mcps()

        self._gateway_token = uuid.uuid4().hex

        await self._create_and_start_container()

        await self._write_gateway_config()
        await self._start_gateway_process()

        host_port = self._port_mapping[self.gateway_port]
        self._gateway = GatewayClient(
            base_url=f"http://127.0.0.1:{host_port}",
            token=self._gateway_token,
            timeout=30.0,
        )
        await self._wait_for_gateway()

        # Pull back the gateway-side MCP view as GatewayMCPClient instances.
        # The gateway loaded these from the config we just wrote, so the set
        # matches self._mcps name-for-name.
        self._gateway_clients = {
            c.name: c for c in await self._gateway.list_mcps()
        }

        if self.host_workdir is not None:
            await self._save_mcp_file()
            await self._seed_skills()

        self.is_alive = True

    async def reset(self) -> None:
        """Return the workspace to an empty state.

        Deregisters every MCP from the gateway (``DELETE /mcps/{name}``
        for each), clears the local handles, and wipes ``.mcp``,
        ``skills/``, ``sessions/``, and ``data/`` inside the container.
        The gateway process keeps running with no upstream MCPs.
        ``default_mcps`` / ``skill_paths`` are not re-seeded.
        """
        async with self._mcp_lock, self._skill_lock:
            for gw_client in list(self._gateway_clients.values()):
                try:
                    await gw_client.close()
                except Exception as e:
                    logger.warning(
                        "MCP %r close failed during reset: %s",
                        gw_client.name,
                        e,
                    )
            self._gateway_clients.clear()
            self._mcps = []

            paths = [
                CONTAINER_SESSIONS_DIR,
                CONTAINER_DATA_DIR,
                CONTAINER_SKILLS_DIR,
            ]
            await self._exec(
                "rm -rf " + " ".join(shlex.quote(p) for p in paths),
            )

            # Rewrite ``.mcp`` to an empty list so a future restart does
            # not fall back to ``default_mcps`` (which would only happen
            # if the file were missing).
            if self.host_workdir is not None:
                await self._save_mcp_file()

    async def close(self) -> None:
        """Stop and remove the container; release the aiodocker client.

        The cached image and the host ``workdir`` are intentionally
        left behind — a subsequent :meth:`initialize` reuses both.
        Errors during gateway/container teardown are swallowed so
        that ``close`` is always safe to call (e.g. from
        ``__aexit__``).
        """
        if self._gateway is not None:
            try:
                await self._gateway.aclose()
            except Exception:
                pass
            self._gateway = None
        self._gateway_clients.clear()

        if self._container is not None:
            # Linux native docker preserves container-side ownership on
            # bind-mounted host paths verbatim — files written by the
            # in-container root process land on host as root, so a
            # non-root host user (CI runner, IDE user) cannot remove
            # them. macOS Docker Desktop / Windows Docker remap uids
            # transparently, so this only matters on Linux. Best-effort:
            # exec failures are swallowed, and we still tear the
            # container down.
            if self.host_workdir is not None and sys.platform == "linux":
                try:
                    await self._exec(
                        f"chown -R {os.getuid()}:{os.getgid()} "
                        f"{shlex.quote(CONTAINER_WORKDIR)}",
                        timeout=10.0,
                    )
                except Exception:
                    pass
            try:
                await self._container.kill()
            except Exception:
                pass
            try:
                await self._container.delete(force=True)
            except Exception:
                pass
            self._container = None

        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                pass
            self._client = None

        self.is_alive = False

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace.

        The configured ``instructions`` template is formatted with the
        container-side ``{workdir}`` (i.e. ``/workspace``), since the
        agent always sees container-internal paths.
        """
        return self.instructions.format(workdir=CONTAINER_WORKDIR)

    # ── tool / MCP / skill discovery ────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        """Built-in tools exposed by the workspace itself.

        Always empty — every tool reaches the agent through an MCP
        server registered on the in-container gateway.
        """
        return []

    async def list_mcps(self) -> list[MCPClient]:
        """Return one :class:`GatewayMCPClient` per registered MCP.

        Each entry's ``name`` matches the upstream MCP server name and
        all of its protocol calls (connect / close / list_tools /
        get_tool / tool ``__call__``) are routed over HTTP to the
        in-container gateway.
        """
        return list(self._gateway_clients.values())

    async def list_skills(self) -> list[Skill]:
        """Enumerate skills by scanning ``skills/`` inside the container.

        For each ``SKILL.md`` found, parses the YAML front-matter and
        yields a :class:`Skill`.  Files missing a ``name`` or
        ``description`` field are skipped.

        Returns:
            Skills available to the agent.  Empty when the directory
            is missing or contains no parseable ``SKILL.md`` files.
        """
        import frontmatter as fm

        result = await self._exec(
            f"find {CONTAINER_SKILLS_DIR} -name SKILL.md "
            f"2>/dev/null || true",
        )
        if not result.ok():
            return []
        listing = result.stdout.decode(errors="replace").strip()
        if not listing:
            return []

        skills: list[Skill] = []
        for md_path in (line.strip() for line in listing.split("\n")):
            if not md_path:
                continue
            try:
                raw = await self._read(md_path)
                doc = fm.loads(raw.decode("utf-8"))
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

    # ── dynamic MCP management ──────────────────────────────────

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        """Register a new MCP server on the in-container gateway.

        Serialises the supplied client, registers it on the gateway
        (which spawns the upstream MCP session inside the container),
        and adds the corresponding :class:`GatewayMCPClient` handle to
        :meth:`list_mcps`. The change is persisted to ``.mcp`` when
        ``workdir`` is set.

        Args:
            mcp_client: An :class:`MCPClient` describing the upstream
                server (stdio / HTTP / SSE config).  Its
                ``model_dump()`` is what the gateway consumes, so any
                ``MCPClient`` subclass is accepted.

        Raises:
            ValueError: If an MCP with the same name is already
                registered in this workspace.
            RuntimeError: If the gateway rejects the registration
                (e.g. upstream command not found inside the
                container).
        """
        async with self._mcp_lock:
            if mcp_client.name in self._gateway_clients:
                raise ValueError(
                    f"MCP {mcp_client.name!r} already exists in workspace.",
                )
            spec = mcp_client.model_dump(mode="json")
            gw_client = self._gateway.make_client(spec)
            await gw_client.connect()
            self._mcps.append(mcp_client)
            self._gateway_clients[gw_client.name] = gw_client
            if self.host_workdir is not None:
                await self._save_mcp_file()

    async def remove_mcp(self, name: str) -> None:
        """Unregister an MCP server by name.

        Tells the gateway to close the upstream session and drops the
        :class:`GatewayMCPClient` handle from :meth:`list_mcps`. The
        change is persisted to ``.mcp`` when ``workdir`` is set.

        Args:
            name: The ``name`` field of the registered MCP. If no
                MCP by that name exists, a warning is logged and the
                call is a no-op (matching the silent behaviour of the
                local workspace).
        """
        async with self._mcp_lock:
            gw_client = self._gateway_clients.pop(name, None)
            if gw_client is None:
                logger.warning("MCP %r not found in workspace", name)
                return
            try:
                await gw_client.close()
            except Exception as e:
                logger.warning("MCP %r close failed: %s", name, e)
            self._mcps = [m for m in self._mcps if m.name != name]
            if self.host_workdir is not None:
                await self._save_mcp_file()

    # ── dynamic skill management ────────────────────────────────

    async def add_skill(self, skill_path: str) -> None:
        """Copy a local skill directory into ``skills/`` inside the container.

        The directory must contain a ``SKILL.md`` with ``name`` and
        ``description`` fields in its YAML front matter (validated
        host-side before any container I/O).  The directory is
        tarred and uploaded via ``put_archive``; a directory of the
        same basename already present in the container is rejected
        rather than overwritten.

        Args:
            skill_path: Absolute or relative path to a skill
                directory on the host filesystem.

        Raises:
            ValueError: If ``SKILL.md`` is missing, or a directory
                with the same basename already exists in the
                container's ``skills/``.
        """
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path!r}: SKILL.md not found",
            )

        async with self._skill_lock:
            await self._exec(f"mkdir -p {CONTAINER_SKILLS_DIR}")
            dir_name = os.path.basename(os.path.abspath(skill_path))

            # Refuse to overwrite an existing directory of the same name —
            # mirrors the conflict-rejection behaviour of ``mkdir`` here
            # rather than LocalWorkspace's full hash-dedup index.
            check = await self._exec(
                f"test -e "
                f"{shlex.quote(CONTAINER_SKILLS_DIR + '/' + dir_name)}",
            )
            if check.ok():
                raise ValueError(
                    f"Skill directory {dir_name!r} already exists in "
                    f"{CONTAINER_SKILLS_DIR}",
                )

            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tf:
                tf.add(skill_path, arcname=dir_name)
            await self._container.put_archive(
                CONTAINER_SKILLS_DIR,
                buf.getvalue(),
            )
            logger.info(
                "DockerWorkspace: added skill %r at %s/%s",
                dir_name,
                CONTAINER_SKILLS_DIR,
                dir_name,
            )

    async def remove_skill(self, name: str) -> None:
        """Delete a skill directory by its agent-facing name.

        Looks up the skill by the ``name`` field of its
        ``SKILL.md``, then ``rm -rf`` its directory inside the
        container.

        Args:
            name: The agent-facing skill name (the ``name`` value in
                the SKILL.md front matter, *not* the directory name).

        Raises:
            KeyError: If no skill with that ``name`` is found.
            RuntimeError: If the in-container ``rm -rf`` returns a
                non-zero exit code.
        """
        skills = await self.list_skills()
        target_dir: str | None = None
        for s in skills:
            if s.name == name:
                target_dir = s.dir
                break
        if target_dir is None:
            available = [s.name for s in skills]
            raise KeyError(
                f"Skill {name!r} not found. Available: {available}",
            )
        result = await self._exec(f"rm -rf {shlex.quote(target_dir)}")
        if not result.ok():
            raise RuntimeError(
                f"Failed to remove skill {name!r}: "
                f"{result.stderr.decode(errors='replace')}",
            )

    # ── offload ─────────────────────────────────────────────────

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Persist a batch of messages as JSONL inside the container.

        Each message is appended to
        ``sessions/<session_id>/context.jsonl``.  Inline base64
        :class:`DataBlock` payloads are extracted into the shared
        ``data/`` directory and rewritten as ``file://`` URL blocks
        before serialisation, keeping the JSONL line size bounded.

        Args:
            session_id (`str`):
                Session-scope key used to partition offloaded files
                (one subdirectory per session).
            msgs (`list[Msg]`):
                Messages to append.  Not mutated — a deep copy is
                used internally so the caller's blocks remain
                base64-inline.

        Returns:
            `str`:
                The container-side path of the JSONL file that
                received the new lines.
        """
        base = f"{CONTAINER_SESSIONS_DIR}/{session_id}"
        path = f"{base}/context.jsonl"

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
                        block = await self._offload_data_block(block)
                    content.append(block)
                msg.content = content
            lines.append(msg.model_dump_json())

        await self._exec(f"mkdir -p {shlex.quote(base)}")
        existing = b""
        try:
            existing = await self._read(path)
        except (FileNotFoundError, OSError):
            pass
        await self._write(
            path,
            existing + ("\n".join(lines) + "\n").encode("utf-8"),
        )
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Persist a single tool result as a flat text file.

        Writes ``sessions/<session_id>/tool_result-<id>.txt`` inside
        the container.  Text blocks are concatenated verbatim;
        :class:`DataBlock` items are emitted as
        ``<data url='…' name='…' media_type='…'/>`` placeholders,
        with inline base64 payloads first offloaded to ``data/``.

        Args:
            session_id (`str`):
                Session-scope key used to partition offloaded files.
            tool_result (`ToolResultBlock`):
                The tool result block to persist.

        Returns:
            `str`:
                The container-side path of the offloaded text file.
        """
        base = f"{CONTAINER_SESSIONS_DIR}/{session_id}"
        path = f"{base}/tool_result-{tool_result.id}.txt"

        parts: list[str] = []
        if isinstance(tool_result.output, str):
            parts.append(tool_result.output)
        else:
            for block in tool_result.output:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        d = await self._offload_data_block(block)
                        url = str(d.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await self._exec(f"mkdir -p {shlex.quote(base)}")
        await self._write(path, "".join(parts).encode("utf-8"))
        return path

    # ── internals: image build ──────────────────────────────────

    async def _build_or_reuse_image(self) -> None:
        """Build the workspace image, or reuse a tag-cache hit.

        The tag is a content hash of the rendered Dockerfile plus
        every file copied into the build context. ``self._image_tag``
        is populated unconditionally so that the container-creation
        step has a stable reference, even on cache hits.

        Raises:
            RuntimeError: If a build error message comes through the
                docker stream.
        """
        ctx_dir, tag, _ = prepare_build_context(
            base_image=self.base_image,
            gateway_home=GATEWAY_HOME,
            container_workdir=CONTAINER_WORKDIR,
            node_version=self.node_version,
            extra_pip=self.extra_pip,
        )
        self._image_tag = tag

        try:
            try:
                await self._client.images.inspect(tag)
                logger.info("DockerWorkspace: image cache hit %r", tag)
                return
            except Exception:
                pass

            logger.info("DockerWorkspace: building image %r", tag)
            # The Docker daemon's POST /build endpoint requires the
            # build context as a tar archive in the request body.
            # docker-py hides this behind a ``path=`` shortcut that
            # tars the directory for you; aiodocker does *not* — we
            # have to tar ``ctx_dir`` ourselves and hand it over via
            # ``fileobj``.  ``arcname="."`` puts every entry at the
            # tar root so the daemon finds ``./Dockerfile`` (and the
            # ``COPY`` source files) without an extra prefix.
            tar_buf = io.BytesIO()
            with tarfile.open(fileobj=tar_buf, mode="w") as tf:
                tf.add(str(ctx_dir), arcname=".")
            tar_buf.seek(0)
            # ``encoding="identity"`` tells aiodocker the body is a
            # plain (uncompressed) tar — without it, aiodocker would
            # gzip our already-tarred bytes and the daemon would
            # reject the malformed stream.
            stream = self._client.images.build(
                fileobj=tar_buf,
                encoding="identity",
                tag=tag,
                stream=True,
                rm=True,
            )
            # Buffer recent stream lines so that a failing RUN step's
            # stderr is included in the RuntimeError below — the
            # daemon's ``error`` chunk only carries a one-line summary
            # ("command returned non-zero code: 1") and the actual
            # diagnostic is in the preceding ``stream`` chunks.
            tail: list[str] = []
            tail_max = 200
            async for chunk in stream:
                if isinstance(chunk, dict):
                    if "stream" in chunk:
                        msg = str(chunk["stream"]).rstrip()
                        if msg:
                            logger.debug("[docker build] %s", msg)
                            tail.append(msg)
                            if len(tail) > tail_max:
                                del tail[: len(tail) - tail_max]
                    if "error" in chunk:
                        log = "\n".join(tail)
                        raise RuntimeError(
                            f"docker build failed: {chunk['error']}\n"
                            f"--- last {len(tail)} build log lines ---\n"
                            f"{log}",
                        )
        finally:
            shutil.rmtree(ctx_dir, ignore_errors=True)

    # ── internals: container lifecycle ──────────────────────────

    async def _create_and_start_container(self) -> None:
        """Create + start the workspace container.

        Wires up:

        * ``Cmd: ["sleep", "infinity"]`` so the container stays up
          even when the gateway is restarted.
        * ``ExposedPorts`` / ``PortBindings`` for ``gateway_port``
          (random host port, ``127.0.0.1`` only).
        * Optional bind mount ``host workdir → /workspace``.
        * ``agentscope.workspace.id`` label for later discovery.

        Resolves the assigned host port into ``self._port_mapping``
        and pre-creates the in-container persistence directories
        (``data/`` / ``skills/`` / ``sessions/``).

        Raises:
            RuntimeError: If the gateway port did not bind to any
                host port (typically a docker daemon issue).
        """
        config: dict[str, Any] = {
            "Image": self._image_tag,
            "Cmd": ["sleep", "infinity"],
            "WorkingDir": CONTAINER_WORKDIR,
            "Labels": {
                "agentscope.workspace": "true",
                "agentscope.workspace.id": self.workspace_id,
            },
            "ExposedPorts": {f"{self.gateway_port}/tcp": {}},
        }
        if self.env:
            config["Env"] = [f"{k}={v}" for k, v in self.env.items()]

        host_config: dict[str, Any] = {
            "PortBindings": {
                f"{self.gateway_port}/tcp": [
                    {"HostIp": "127.0.0.1", "HostPort": ""},
                ],
            },
        }
        if self.host_workdir is not None:
            os.makedirs(self.host_workdir, exist_ok=True)
            host_config["Binds"] = [
                f"{os.path.abspath(self.host_workdir)}:{CONTAINER_WORKDIR}:rw",
            ]
        config["HostConfig"] = host_config

        self._container = await self._client.containers.create_or_replace(
            name=f"as_ws_{self.workspace_id}",
            config=config,
        )
        await self._container.start()

        info = await self._container.show()
        ports_info = info.get("NetworkSettings", {}).get("Ports") or {}
        bindings = ports_info.get(f"{self.gateway_port}/tcp", []) or []
        if not bindings:
            raise RuntimeError(
                f"gateway port {self.gateway_port} did not bind to a "
                "host port",
            )
        self._port_mapping[self.gateway_port] = int(bindings[0]["HostPort"])

        # Ensure the in-container persistence dirs exist (also makes a
        # newly-bind-mounted host workdir agentscope-shaped on first use).
        await self._exec(
            "mkdir -p "
            f"{shlex.quote(CONTAINER_DATA_DIR)} "
            f"{shlex.quote(CONTAINER_SKILLS_DIR)} "
            f"{shlex.quote(CONTAINER_SESSIONS_DIR)}",
        )

    async def _restore_or_seed_mcps(self) -> list[MCPClient]:
        """Decide the MCP set to ship to the gateway on startup.

        * No ``workdir`` → return ``default_mcps`` (purely ephemeral).
        * ``workdir`` set, ``<workdir>/.mcp`` missing → return
          ``default_mcps`` and let the next ``_save_mcp_file`` write
          it.
        * ``<workdir>/.mcp`` present → :meth:`MCPClient.model_validate`
          each entry and return them.  A read / parse error is
          logged and the call falls back to ``default_mcps`` rather
          than crashing the whole workspace.

        Returns:
            The MCPClient instances to register on the gateway.
        """
        if self.host_workdir is None:
            return list(self.default_mcps)
        host_mcp = os.path.join(self.host_workdir, ".mcp")
        if not os.path.isfile(host_mcp):
            return list(self.default_mcps)
        try:
            with open(host_mcp, encoding="utf-8") as f:
                data = json.load(f)
            return [MCPClient.model_validate(m) for m in data]
        except Exception as e:
            logger.warning(
                "DockerWorkspace: failed to read %s, falling back to "
                "default_mcps: %s",
                host_mcp,
                e,
            )
            return list(self.default_mcps)

    async def _save_mcp_file(self) -> None:
        """Persist ``self._mcps`` to ``<workdir>/.mcp`` (host-side JSON).

        No-op when ``workdir`` is ``None``.  Failures are logged but
        not raised — losing the persistence file should not
        propagate as an MCP-add/remove error to the caller.
        """
        if self.host_workdir is None:
            return
        host_mcp = os.path.join(self.host_workdir, ".mcp")
        try:
            os.makedirs(self.host_workdir, exist_ok=True)
            with open(host_mcp, "w", encoding="utf-8") as f:
                json.dump(
                    [m.model_dump() for m in self._mcps],
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
        except Exception as e:
            logger.warning(
                "DockerWorkspace: failed to save %s: %s",
                host_mcp,
                e,
            )

    async def _write_gateway_config(self) -> None:
        """Drop the gateway's ``--config`` JSON into the container.

        The file at :data:`GATEWAY_CONFIG` carries the freshly minted
        bearer token plus the MCP server specs the gateway should
        bring up at start.  This is the *only* path the bearer token
        crosses — it never lands on host disk.
        """
        cfg = {
            "token": self._gateway_token,
            "servers": [m.model_dump(mode="json") for m in self._mcps],
        }
        await self._exec(f"mkdir -p {shlex.quote(GATEWAY_HOME)}")
        await self._write(
            GATEWAY_CONFIG,
            json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    async def _start_gateway_process(self) -> None:
        """Launch the gateway inside the container as a detached process.

        Runs ``nohup python <GATEWAY_SCRIPT> --config <GATEWAY_CONFIG>
        --port <gateway_port>`` from the baked-in venv. The script is
        invoked by path (not via ``python -m``) so Python does not
        auto-import ``agentscope.workspace.__init__`` and the heavy
        module graph it pulls in. stdout/stderr are redirected to
        :data:`GATEWAY_LOG` so :meth:`_wait_for_gateway` can dump the
        tail when startup fails.

        We do not block on this exec call; readiness is detected via
        the ``/health`` poll instead.
        """
        cmd = (
            f"nohup {shlex.quote(GATEWAY_VENV + '/bin/python')} -u "
            f"{shlex.quote(GATEWAY_SCRIPT)} "
            f"--config {shlex.quote(GATEWAY_CONFIG)} "
            f"--port {self.gateway_port} "
            f"> {shlex.quote(GATEWAY_LOG)} 2>&1 &"
        )
        # Detach: we don't await stream completion, just kick it off.
        await self._exec(cmd)

    async def _wait_for_gateway(self, timeout: float = 30.0) -> None:
        """Block until the gateway answers ``/health`` with 200.

        Uses an exponentially-backed-off poll capped at 1 s.  When
        the deadline expires, attempts to read the gateway log and
        surfaces the tail in the raised error so callers can see the
        actual startup failure.

        Args:
            timeout: Maximum seconds to wait for readiness.

        Raises:
            RuntimeError: If the gateway does not become healthy
                before the deadline.
        """
        assert self._gateway is not None
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if await self._gateway.health():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        # Last-ditch: dump the gateway log to help debug startup failures.
        try:
            log = await self._read(GATEWAY_LOG)
            tail = log[-2000:].decode(errors="replace")
        except Exception:
            tail = "<no gateway log available>"
        raise RuntimeError(
            f"gateway did not become healthy within {timeout}s. "
            f"Tail of {GATEWAY_LOG}:\n{tail}",
        )

    async def _seed_skills(self) -> None:
        """Copy ``self.skill_paths`` into ``skills/`` once, on first init.

        Skips seeding when (a) ``workdir`` is unset, (b) ``skill_paths``
        is empty, or (c) the host-side ``skills/`` directory already
        contains entries — meaning the user (or a prior init) is the
        source of truth and we should not append duplicates.

        Failures on individual paths are logged and skipped rather
        than raised, so that one bad skill cannot block startup.
        """
        if not self.skill_paths or self.host_workdir is None:
            return
        skills_host = os.path.join(self.host_workdir, "skills")
        # The bind mount lazily materialises ``<workdir>/skills`` on
        # host the first time the container writes into it, so on a
        # fresh workdir the host path may not exist yet — create it
        # so the next check has something to inspect.
        os.makedirs(skills_host, exist_ok=True)
        if os.listdir(skills_host):
            # already seeded (or user pre-populated) — leave as-is.
            return
        for path in self.skill_paths:
            try:
                await self.add_skill(path)
            except Exception as e:
                logger.warning(
                    "DockerWorkspace: skip skill %r: %s",
                    path,
                    e,
                )

    # ── internals: container I/O ────────────────────────────────

    async def _exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> _ExecResult:
        """Run ``sh -c <command>`` inside the container.

        The exec stream is consumed entirely so that ``stdout`` /
        ``stderr`` capture both stream channels in order.  The
        container's ``CONTAINER_WORKDIR`` is used as the working
        directory.

        Args:
            command: Shell command string. Caller is responsible for
                quoting via :func:`shlex.quote`.
            timeout: Maximum seconds to wait for completion.  ``None``
                waits indefinitely; on timeout an
                :class:`_ExecResult` with ``exit_code=-1`` and
                ``stderr=b"timed out"`` is returned (no exception).

        Returns:
            The captured exit code and IO streams.
        """

        async def _run() -> _ExecResult:
            exec_obj = await self._container.exec(
                cmd=["sh", "-c", command],
                workdir=CONTAINER_WORKDIR,
            )
            stdout: list[bytes] = []
            stderr: list[bytes] = []
            # ``exec_obj.start()`` returns aiodocker's ``Stream``
            # object — an async context manager wrapping a
            # persistent HTTP/1.1-Upgrade connection to the docker
            # daemon. Drain it with ``read_out()``: each call yields
            # a ``Message(stream=int, data=bytes)`` (channel 1 =
            # stdout, channel 2 = stderr), or ``None`` at EOF when
            # the exec process exits.  ``async with`` is required —
            # without it the underlying ``ClientResponse`` is leaked
            # and asyncio logs ``Unclosed response`` at GC time,
            # which on a closed event loop manifests as ``Event loop
            # is closed`` errors during pytest teardown.
            async with exec_obj.start() as stream:
                while True:
                    msg = await stream.read_out()
                    if msg is None:
                        break
                    if msg.stream == 1:
                        stdout.append(msg.data)
                    else:
                        stderr.append(msg.data)
            inspect = await exec_obj.inspect()
            code = inspect.get("ExitCode", -1)
            if code is None:
                code = -1
            return _ExecResult(
                exit_code=int(code),
                stdout=b"".join(stdout),
                stderr=b"".join(stderr),
            )

        if timeout is None:
            return await _run()
        try:
            return await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.TimeoutError:
            return _ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=b"timed out",
            )

    async def _read(self, path: str) -> bytes:
        """Fetch a file from the container as raw bytes.

        Uses ``get_archive`` (tarfile stream) and extracts the first
        regular member.  Compatible with the two stream shapes that
        aiodocker emits (sync ``dict`` payload or async chunk stream).

        Args:
            path: Absolute container-side path of the file to read.

        Returns:
            The file contents.

        Raises:
            FileNotFoundError: If the path does not exist in the
                container, or if the tar stream contains no regular
                file at that path.
        """

        from aiodocker import exceptions as aiodocker_exceptions

        try:
            # ``get_archive`` returns an already-parsed
            # :class:`tarfile.TarFile` (the daemon serves the file
            # entry as a tar stream, aiodocker drains it into memory
            # for us). Iterate members and return the first regular
            # file's bytes.
            tar = await self._container.get_archive(path)
        except aiodocker_exceptions.DockerError as exc:
            # The daemon answers with 404 + ``"Could not find the file
            # ... in container ..."`` when ``path`` is missing.
            # Translate that to ``FileNotFoundError`` so callers can
            # use the standard exception type instead of leaking the
            # aiodocker-specific class.
            if exc.status == 404:
                raise FileNotFoundError(
                    f"not found in container: {path}",
                ) from exc
            raise

        try:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f:
                        return f.read()
        finally:
            tar.close()
        raise FileNotFoundError(f"not found in container: {path}")

    async def _write(self, path: str, data: bytes) -> None:
        """Write raw bytes to a file inside the container.

        Creates the parent directory with ``mkdir -p`` first, then
        uploads a single-entry tar via ``put_archive``.  Overwrites
        an existing file at the same path.

        Args:
            path: Absolute container-side destination path.
            data: Raw file contents.
        """
        parent = posixpath.dirname(path) or "/"
        name = posixpath.basename(path)

        await self._exec(f"mkdir -p {shlex.quote(parent)}")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tf:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        await self._container.put_archive(parent, buf.getvalue())

    # ── internals: data offload ────────────────────────────────

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        """Persist a base64 :class:`DataBlock` under ``data/``.

        The decoded payload is stored at
        ``data/<sha256-of-original-base64>.<ext>``, where ``<ext>`` is
        guessed from the block's media type.  Hashing the *base64*
        text rather than the decoded bytes lets a second offload of
        the same block short-circuit (same key, same file).

        Args:
            block: An inline-base64 data block.

        Returns:
            A new :class:`DataBlock` whose source is a ``file://``
            URL pointing at the persisted file inside the container.
            Blocks already backed by a :class:`URLSource` are returned
            unchanged (nothing to persist).
        """
        if not isinstance(block.source, Base64Source):
            return block
        h = hashlib.sha256(block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        path = f"{CONTAINER_DATA_DIR}/{h}{ext}"
        await self._exec(f"mkdir -p {shlex.quote(CONTAINER_DATA_DIR)}")
        await self._write(path, base64.b64decode(block.source.data))
        return DataBlock(
            id=block.id,
            name=block.name,
            source=URLSource(
                url=AnyUrl(f"file://{path}"),
                media_type=block.source.media_type,
            ),
        )
