# -*- coding: utf-8 -*-
"""AppleContainerWorkspace — sandboxed workspace backed by Apple's
``container`` CLI.

Architecture
------------

Mirrors :class:`agentscope.workspace.E2BWorkspace` but swaps the E2B
SDK for the ``container`` CLI:

* **Lifecycle.** ``initialize()`` pulls the base image (if needed),
  creates and starts a container via ``container run -d``, then
  bootstraps the MCP gateway inside the container.
  ``close()`` calls ``container stop`` + ``container rm -f``.

* **Persistence.** Container filesystem state is the persistence
  layer — there is no host-side ``workdir`` parameter. Stopping the
  container discards state; volumes are not used by default.

* **Bootstrap.** First-time provisioning installs uv + a gateway venv
  + agentscope (``--no-deps``) and uploads the gateway script. The
  probe + install loop lives on :class:`SandboxedWorkspaceBase`; this
  subclass only supplies the container-specific shell commands via
  :meth:`_bootstrap_commands`. Bootstrap runs at most once per
  container lifetime.

* **MCP gateway.** Identical to Docker/E2B: a FastAPI process inside
  the container. All host-side calls drive the gateway through
  :class:`GatewayClient`, which runs an in-container ``python3 -c``
  shim via :meth:`AppleContainerBackend.exec_shell`.

.. note::

    Apple Container requires the system service to be running
    (``container system start``) and is available on macOS 26+
    with Apple silicon only.
"""

import asyncio
import json
import shlex

from ..._logging import logger
from ...mcp import MCPClient
from .._sandboxed_base import SandboxedWorkspaceBase
from .._utils import _GATEWAY_BASE_REQUIREMENTS
from ._applecontainer_backend import AppleContainerBackend
from ._constants import (
    CONTAINER_WORKDIR,
    DEFAULT_BASE_IMAGE,
    DEFAULT_CPUS,
    DEFAULT_GATEWAY_PORT,
    DEFAULT_MEMORY,
    GATEWAY_HOME,
)

_DEFAULT_INSTRUCTIONS = """<workspace>
You have an Apple-Container-based workspace. All tool calls execute
**inside the container** at ``{workdir}``.

Layout:

```
{workdir}
├── data/        # offloaded multimodal files
├── skills/      # reusable skills
└── sessions/    # session context and tool results
```
</workspace>"""


# ── the workspace ──────────────────────────────────────────────────


class AppleContainerWorkspace(SandboxedWorkspaceBase):
    """Workspace backed by Apple's ``container`` CLI.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.

    Requires:
    - macOS 26+ with Apple silicon
    - ``container`` CLI installed and ``container system start`` running
    """

    _gateway_home = GATEWAY_HOME
    _bootstrap_cmd_timeout = 600.0

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        base_image: str = DEFAULT_BASE_IMAGE,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        cpus: int = DEFAULT_CPUS,
        memory: str = DEFAULT_MEMORY,
        env: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Construct an :class:`AppleContainerWorkspace`.

        The container is *not* started here — call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Stable identifier; also used as the container name
                suffix.
            base_image (`str`, defaults to `DEFAULT_BASE_IMAGE`):
                OCI image to run. Must provide ``python3``. Ubuntu/
                Debian-based images are preferred for apt-get
                bootstrap.
            gateway_port (`int`, defaults to `DEFAULT_GATEWAY_PORT`):
                TCP port the in-container gateway listens on.
            cpus (`int`, defaults to `DEFAULT_CPUS`):
                Number of virtual CPUs allocated to the container.
            memory (`str`, defaults to `DEFAULT_MEMORY`):
                Memory allocation (e.g. ``"2G"``, ``"512M"``).
            env (`dict[str, str] | None`, optional):
                Environment variables set inside the container.
            extra_pip (`list[str] | None`, optional):
                Extra Python packages installed into the gateway venv
                during bootstrap.
            instructions (`str`, defaults to `_DEFAULT_INSTRUCTIONS`):
                System-prompt fragment template (supports ``{workdir}``).
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs registered on first init when no persisted
                ``.mcp`` exists.
            skill_paths (`list[str] | None`, optional):
                Local skill dirs seeded into ``skills/`` on first init.
        """
        super().__init__(
            workspace_id=workspace_id,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )

        # ── serializable config ─────────────────────────────────
        self.workdir = CONTAINER_WORKDIR
        self.base_image = base_image
        self.gateway_port = gateway_port
        self.cpus = cpus
        self.memory = memory
        self.env: dict[str, str] = dict(env or {})
        self.extra_pip: list[str] = list(extra_pip or [])
        self.instructions = instructions

        # ── runtime state ───────────────────────────────────────
        self._backend: AppleContainerBackend | None = None
        self._container_name: str = f"as_ws_{self.workspace_id}"

    # ── lifecycle hooks ─────────────────────────────────────────

    async def _provision_backend(self) -> None:
        """Pull the base image (if needed), create and start the
        container, and bind the backend.

        Steps:
        1. Verify ``container`` CLI is available.
        2. Pull the base image if not already present locally.
        3. Create and start the container via ``container run -d``.
        4. Bind :class:`AppleContainerBackend`.
        """
        # 1. Verify CLI availability.
        await self._check_cli()

        # 2. Pull the image if needed.
        await self._pull_image_if_needed()

        # 3. Check for an existing container with the same name.
        existing_id = await self._find_existing_container()
        if existing_id is not None:
            logger.info(
                "AppleContainerWorkspace: reattaching to existing "
                "container %r (workspace_id=%r)",
                existing_id,
                self.workspace_id,
            )
            # Ensure the container is running.
            await self._start_container_if_stopped()
        else:
            await self._create_and_start_container()

        # 4. Bind the backend.
        self._backend = AppleContainerBackend(
            container_id=self._container_name,
            workdir=CONTAINER_WORKDIR,
        )

    async def _teardown_backend(self) -> None:
        """Stop and remove the container. Errors are swallowed."""
        if self._backend is None:
            return
        try:
            process = await asyncio.create_subprocess_exec(
                "container",
                "stop",
                self._container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
        except Exception as e:
            logger.warning(
                "AppleContainerWorkspace: stop failed: %s",
                e,
            )
        try:
            process = await asyncio.create_subprocess_exec(
                "container",
                "rm",
                "-f",
                self._container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
        except Exception as e:
            logger.warning(
                "AppleContainerWorkspace: rm failed: %s",
                e,
            )
        self._backend = None

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment, formatted with ``{workdir}``."""
        return self.instructions.format(workdir=CONTAINER_WORKDIR)

    # ── internals: CLI check ────────────────────────────────────

    async def _check_cli(self) -> None:
        """Verify the ``container`` CLI is installed and responsive.

        Runs ``container system version --format json`` and parses the
        output to confirm the CLI is available.

        Raises:
            RuntimeError: If the CLI is not installed or the system
                service is not running.
        """
        process = await asyncio.create_subprocess_exec(
            "container",
            "system",
            "version",
            "--format",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                "Apple Container CLI is not available. "
                "Ensure it is installed and running: "
                "`container system start`.\n"
                f"stderr: {stderr.decode(errors='replace')}",
            )
        logger.info(
            "AppleContainerWorkspace: CLI OK — %s",
            stdout.decode(errors="replace").strip(),
        )

    # ── internals: image management ─────────────────────────────

    async def _pull_image_if_needed(self) -> None:
        """Pull *base_image* if it is not already present locally.

        Uses ``container image list --format json`` to check for the
        image.
        """
        # Check if the image is already available locally.
        process = await asyncio.create_subprocess_exec(
            "container",
            "image",
            "list",
            "--format",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            try:
                images = json.loads(stdout.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                images = []
            for img in images:
                if (
                    isinstance(img, dict)
                    and img.get("name") == self.base_image
                ):
                    logger.info(
                        "AppleContainerWorkspace: image %r already present",
                        self.base_image,
                    )
                    return

        # Pull the image.
        logger.info(
            "AppleContainerWorkspace: pulling image %r ...",
            self.base_image,
        )
        process = await asyncio.create_subprocess_exec(
            "container",
            "image",
            "pull",
            self.base_image,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"Failed to pull image {self.base_image!r}: "
                f"{stderr.decode(errors='replace')}",
            )
        logger.info(
            "AppleContainerWorkspace: image %r pulled successfully",
            self.base_image,
        )

    # ── internals: container lifecycle ──────────────────────────

    async def _find_existing_container(self) -> str | None:
        """Find a container by name via ``container list --format json``.

        Returns:
            `str | None`:
                The container ID if found, ``None`` otherwise.
        """
        process = await asyncio.create_subprocess_exec(
            "container",
            "list",
            "--all",
            "--format",
            "json",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode != 0:
            return None
        try:
            containers = json.loads(stdout.decode())
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        for c in containers:
            if isinstance(c, dict) and c.get("name") == self._container_name:
                return c.get("id")
        return None

    async def _start_container_if_stopped(self) -> None:
        """Start the container if it is not already running."""
        # Check if the container is already running.
        process = await asyncio.create_subprocess_exec(
            "container",
            "inspect",
            self._container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await process.communicate()
        if process.returncode == 0:
            try:
                info = json.loads(stdout.decode())
            except (json.JSONDecodeError, UnicodeDecodeError):
                info = {}
            if isinstance(info, dict) and info.get(
                "status",
                "",
            ) in ("running",):
                logger.info(
                    "AppleContainerWorkspace: container %r already running",
                    self._container_name,
                )
                return

        # Start the container.
        logger.info(
            "AppleContainerWorkspace: starting container %r ...",
            self._container_name,
        )
        process = await asyncio.create_subprocess_exec(
            "container",
            "start",
            self._container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"Failed to start container {self._container_name!r}: "
                f"{stderr.decode(errors='replace')}",
            )

    async def _create_and_start_container(self) -> None:
        """Create and start the container via ``container run -d``.

        The container runs ``sleep infinity`` to stay alive while the
        gateway is managed independently.
        """
        run_cmd: list[str] = [
            "container",
            "run",
            "-d",
            "--name",
            self._container_name,
            "--cpus",
            str(self.cpus),
            "--memory",
            self.memory,
        ]

        # Environment variables.
        for key, value in self.env.items():
            run_cmd.extend(["--env", f"{key}={value}"])

        # Image and init command.
        run_cmd.extend(
            [
                self.base_image,
                "sleep",
                "infinity",
            ],
        )

        logger.info(
            "AppleContainerWorkspace: creating container %r ...",
            self._container_name,
        )
        process = await asyncio.create_subprocess_exec(
            *run_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()
        if process.returncode != 0:
            raise RuntimeError(
                f"Failed to create container {self._container_name!r}: "
                f"stderr: {stderr.decode(errors='replace')}\n"
                f"stdout: {stdout.decode(errors='replace')}",
            )
        logger.info(
            "AppleContainerWorkspace: container %r created (id=%s)",
            self._container_name,
            stdout.decode(errors="replace").strip(),
        )

    # ── internals: bootstrap ────────────────────────────────────

    def _bootstrap_commands(self) -> list[str]:
        """Shell commands that provision this container once.

        Only runs when the gateway script is missing (fresh container
        or prior interrupted bootstrap). Every step is idempotent.

        ``--no-deps`` on agentscope is mandatory: the gateway only
        imports :class:`agentscope.mcp.MCPClient` whose transitive
        needs (``mcp / pydantic / httpx``) are already installed via
        the gateway base requirements.
        """
        pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(self.extra_pip)
        pip_args = " ".join(shlex.quote(p) for p in pip_pkgs)

        return [
            # 1. System deps + uv installer.
            "apt-get update -qq "
            "&& apt-get install -y --no-install-recommends "
            "curl ripgrep "
            "&& rm -rf /var/lib/apt/lists/*",
            "curl -LsSf https://astral.sh/uv/install.sh "
            "| env UV_INSTALL_DIR=/usr/local/bin "
            "INSTALLER_NO_MODIFY_PATH=1 sh",
            # 2. Gateway venv + base requirements + agentscope.
            f"uv venv {self._gateway_venv}",
            f"uv pip install --python {self._gateway_python} {pip_args}",
            f"uv pip install --python {self._gateway_python} "
            f"--no-deps 'agentscope'",
        ]
