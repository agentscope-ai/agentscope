# -*- coding: utf-8 -*-
"""DockerWorkspace — sandboxed workspace backed by a Docker container.

* Container lifecycle (build + run + stop) via **aiodocker**.
* MCP servers run inside the container behind a FastAPI gateway
  (see :mod:`agentscope.workspace._mcp_gateway`); the host reaches it
  through :class:`GatewayClient` via ``backend.exec_shell``.
* Optional bind-mounted host ``workdir`` makes the workspace
  persistent — ``.mcp``, ``skills/``, ``sessions/``, ``data/`` survive
  restarts. Without ``workdir`` the container is ephemeral.
* Image is content-hashed by Dockerfile + COPY payloads
  (see :mod:`._make_dockerfile`); a cache hit skips the build.

On each :meth:`initialize`, MCPs are restored from ``<workdir>/.mcp``
(or seeded from ``default_mcps``); ``add_mcp`` / ``remove_mcp`` rewrite
the file. The gateway reads ``.mcp`` directly as its config.
"""

import io
import os
import shutil
import sys
import tarfile
from typing import Any

from ..._logging import logger
from ...mcp import MCPClient
from .._sandboxed_base import SandboxedWorkspaceBase
from ._docker_backend import DockerBackend
from ._make_dockerfile import (
    CONTAINER_WORKDIR,
    DEFAULT_BASE_IMAGE,
    DEFAULT_GATEWAY_PORT,
    GATEWAY_HOME,
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


# ── the workspace ──────────────────────────────────────────────────


class DockerWorkspace(SandboxedWorkspaceBase):
    """Workspace backed by a Docker container.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.
    """

    _gateway_home = GATEWAY_HOME

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

        The workspace is *not* started here — call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Existing workspace identifier to adopt; ``None`` mints
                a fresh UUID.
            base_image (`str`, defaults to `DEFAULT_BASE_IMAGE`):
                Base Docker image. Must provide ``python3`` in
                ``$PATH``.
            host_workdir (`str | None`, optional):
                Host directory bind-mounted to ``/workspace``. ``None``
                makes the workspace ephemeral.
            node_version (`str | None`, optional):
                Major Node.js version to bake into the image.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                at image-build time.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the gateway listens on inside the container
                (no host port mapping).
            env (`dict[str, str] | None`, optional):
                Environment variables to set inside the container.
            instructions (`str`, defaults to `_DEFAULT_INSTRUCTIONS`):
                System-prompt fragment template; supports ``{workdir}``.
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs registered on first init when no persisted
                ``.mcp`` exists.
            skill_paths (`list[str] | None`, optional):
                Local skill dirs seeded into ``skills/`` on first init.
        """
        # Backwards compatibility: accept legacy ``workdir`` kwarg.
        if "workdir" in kwargs:
            logger.warning(
                "DockerWorkspace parameter 'workdir' is deprecated, "
                "use 'host_workdir' instead.",
            )
            if host_workdir is None:
                host_workdir = kwargs.pop("workdir")

        super().__init__(
            workspace_id=workspace_id,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )

        # ── serializable config ─────────────────────────────────
        self.workdir = CONTAINER_WORKDIR
        self.base_image = base_image
        self.host_workdir = host_workdir
        self.node_version = node_version
        self.extra_pip: list[str] = list(extra_pip or [])
        self.gateway_port = gateway_port
        self.env: dict[str, str] = dict(env or {})
        self.instructions = instructions

        # ── runtime state (Docker-only) ─────────────────────────
        self._client: Any = None  # aiodocker.Docker
        self._container: Any = None
        self._backend: DockerBackend | None = None
        self._image_tag: str = ""

    @property
    def is_persistent(self) -> bool:
        """``True`` iff a host bind-mount preserves the workspace."""
        return self.host_workdir is not None

    # ── lifecycle hooks ─────────────────────────────────────────

    async def _provision_backend(self) -> None:
        """Build / reuse the image and start the container."""
        import aiodocker

        self._client = aiodocker.Docker()
        await self._build_or_reuse_image()
        await self._create_and_start_container()

    async def _teardown_backend(self) -> None:
        """Stop and remove the container; release the aiodocker client.

        Errors are swallowed so teardown is always safe.
        """
        if self._container is not None:
            # On Linux native docker, the in-container root process
            # writes bind-mount files with container-side ownership;
            # restore ownership to the host user so they can be
            # removed. macOS/Windows Docker remap uids transparently.
            if (
                self.host_workdir is not None
                and sys.platform == "linux"
                and self._backend is not None
            ):
                try:
                    await self._backend.exec_shell(
                        [
                            "chown",
                            "-R",
                            f"{os.getuid()}:{os.getgid()}",
                            CONTAINER_WORKDIR,
                        ],
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

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment, formatted with the
        container-side ``{workdir}``."""
        return self.instructions.format(workdir=CONTAINER_WORKDIR)

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
            logger.info("DockerWorkspace: finish building image %r", tag)
            shutil.rmtree(ctx_dir, ignore_errors=True)

    # ── internals: container lifecycle ──────────────────────────

    async def _create_and_start_container(self) -> None:
        """Create + start the workspace container.

        * ``Cmd: ["sleep", "infinity"]`` keeps it up across gateway
          restarts.
        * Optional bind mount ``host workdir → /workspace``.
        * The gateway port stays internal — calls run from inside the
          sandbox via :class:`GatewayClient`, so no host port mapping.
        """
        config: dict[str, Any] = {
            "Image": self._image_tag,
            "Cmd": ["sleep", "infinity"],
            "WorkingDir": CONTAINER_WORKDIR,
            "Labels": {
                "agentscope.workspace": "true",
                "agentscope.workspace.id": self.workspace_id,
            },
        }
        if self.env:
            config["Env"] = [f"{k}={v}" for k, v in self.env.items()]

        host_config: dict[str, Any] = {}
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

        # Create the backend now that the container is running. All
        # subsequent container I/O in this workspace goes through it.
        self._backend = DockerBackend(self._container, CONTAINER_WORKDIR)
