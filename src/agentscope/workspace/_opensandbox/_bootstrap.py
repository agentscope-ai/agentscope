# -*- coding: utf-8 -*-
"""Bootstrap helpers for :class:`OpenSandboxWorkspace` provisioning.

OpenSandbox has no AgentScope-specific image-build phase: it starts a
generic sandbox image (``python:3.11-slim`` by default) and we perform
the first-time runtime setup from inside the sandbox. The command
sequence installs system tools, ``uv``, the gateway venv, gateway base
requirements, and the ``agentscope`` package. After that the workspace
uploads the gateway and glob helper scripts.

These artefacts live on the sandbox filesystem, so they survive
``pause`` / ``resume`` and the bootstrap cost is paid once per sandbox
lifetime. Re-running the sequence is still safe: all directory creation,
venv creation, and installs are idempotent enough for an interrupted
first boot to recover.

Two install modes mirror Docker and E2B:

* **released**: ``agentscope`` is installed on the host from a package;
  install the same version from PyPI inside the gateway venv.
* **dev**: running from a source checkout; tar the local project tree,
  upload it as a single blob, untar inside the sandbox, and install it
  with ``uv pip install --no-deps``.

``--no-deps`` is intentional: the gateway imports only the lightweight
MCP-facing subset. The gateway base requirements already include the
needed runtime dependencies, while pulling the full AgentScope
dependency tree would make cold bootstrap slower and more brittle on a
minimal sandbox image.
"""

import io
import tarfile

from ..._logging import logger
from .._utils import (
    _GATEWAY_BASE_REQUIREMENTS,
    _agentscope_source_root,
    _is_source_ignored,
)

#: Default OpenSandbox image. The slim Python image is small but still
#: has enough package-manager support for installing curl, certificates,
#: ripgrep, and procps during bootstrap.
DEFAULT_IMAGE = "python:3.11-slim"

#: Default keep-alive timeout in seconds for newly-created sandboxes.
DEFAULT_TIMEOUT = 300

#: Per-command timeout for first-time bootstrap shell commands.
BOOTSTRAP_COMMAND_TIMEOUT = 600.0

#: Default OpenSandbox SDK HTTP timeout in seconds. Bootstrap commands
#: can legitimately stream for several minutes, so this must not be
#: shorter than :data:`BOOTSTRAP_COMMAND_TIMEOUT`.
DEFAULT_REQUEST_TIMEOUT = BOOTSTRAP_COMMAND_TIMEOUT

#: Default port the in-sandbox gateway listens on.
DEFAULT_GATEWAY_PORT = 5600

# Workspace-side persistent layout. OpenSandbox's default Docker
# runtime runs the image as root, so the workspace itself can live at a
# short root-owned path.
SANDBOX_WORKDIR = "/workspace"
SANDBOX_DATA_DIR = f"{SANDBOX_WORKDIR}/data"
SANDBOX_SKILLS_DIR = f"{SANDBOX_WORKDIR}/skills"
SANDBOX_SESSIONS_DIR = f"{SANDBOX_WORKDIR}/sessions"
SANDBOX_MCP_FILE = f"{SANDBOX_WORKDIR}/.mcp"

# Gateway home: venv, script, config, logs, and PID file.
GATEWAY_HOME = "/root/.agentscope"
GATEWAY_VENV = f"{GATEWAY_HOME}/.venv"
GATEWAY_VENV_PY = f"{GATEWAY_VENV}/bin/python"
GATEWAY_SCRIPT = f"{GATEWAY_HOME}/_mcp_gateway_app.py"
# Standalone glob helper script used by the builtin Glob tool.
GLOB_HELPER_SCRIPT = f"{GATEWAY_HOME}/_glob_helper.py"
GATEWAY_CONFIG = f"{GATEWAY_HOME}/gateway.config.json"
GATEWAY_LOG = f"{GATEWAY_HOME}/gateway.log"
GATEWAY_PID = f"{GATEWAY_HOME}/gateway.pid"

# uv binary lives under root's local bin because the default image runs
# as root and the installer can write there without sudo.
UV_BIN = "/root/.local/bin/uv"

#: Sandbox metadata key used to map workspace_id to sandbox id. The
#: workspace filters ``list_sandbox_infos`` by this key on cache miss to
#: locate and resume an existing sandbox.
METADATA_WORKSPACE_ID_KEY = "agentscope.workspace.id"

#: Tarball drop point for dev-mode ``agentscope`` source uploads.
#: Lives under ``GATEWAY_HOME`` so bootstrap artefacts stay together and
#: can be removed with the dev source directory after install.
DEV_SRC_TAR = f"{GATEWAY_HOME}/agentscope_src.tar"
DEV_SRC_DIR = f"{GATEWAY_HOME}/agentscope_src"


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """Filter ignored paths while preserving the archive root entry.

    Returning ``None`` for an entry tells :meth:`tarfile.add` to also
    stop recursing into it, so the root archive entry (``arcname="."``)
    must pass through unchanged; otherwise the entire source tree would
    be excluded.
    """
    if info.name == ".":
        return info
    name = info.name.split("/")[-1]
    if _is_source_ignored(name):
        return None
    return info


def build_source_tarball() -> bytes:
    """Tar the local source tree for dev-mode upload.

    Returns uncompressed tar bytes; the sandbox extracts them with
    ``tar -xf``.
    """
    root = _agentscope_source_root()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        tf.add(str(root), arcname=".", filter=_tar_filter)
    return buf.getvalue()


def bootstrap_commands(
    *,
    extra_pip: list[str] | None = None,
    install_agentscope_cmd: str,
) -> list[str]:
    """Return the provisioning shell command sequence.

    Args:
        extra_pip: Extra Python packages to install into the gateway
            venv alongside the base requirements.
        install_agentscope_cmd: Shell command that installs
            ``agentscope`` into the gateway venv. Built per install
            mode by :func:`render_install_agentscope_cmd_released` or
            :func:`render_install_agentscope_cmd_dev`.

    Returns:
        A list of shell command strings, to be executed in order. Each
        must exit 0; a non-zero exit aborts bootstrap.
    """
    pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(extra_pip or [])
    pip_args = " ".join(pip_pkgs)
    return [
        # 1. Persistent layout. mkdir -p is cheap on resume too, which
        # keeps interrupted bootstrap recovery simple.
        f"mkdir -p {SANDBOX_DATA_DIR} {SANDBOX_SKILLS_DIR} "
        f"{SANDBOX_SESSIONS_DIR} {GATEWAY_HOME}",
        # 2. System packages used by bootstrap and builtin tools. The
        # default image runs as root, so no sudo is needed here.
        "apt-get update -qq "
        "&& apt-get install -y --no-install-recommends curl "
        "ca-certificates procps ripgrep "
        "&& rm -rf /var/lib/apt/lists/*",
        # 3. Astral uv. INSTALLER_NO_MODIFY_PATH=1 suppresses shell rc
        # edits; AgentScope always invokes uv by full path.
        "curl -LsSf https://astral.sh/uv/install.sh "
        "| env UV_INSTALL_DIR=/root/.local/bin "
        "INSTALLER_NO_MODIFY_PATH=1 sh",
        # 4. Gateway venv and base requirements.
        f"{UV_BIN} venv {GATEWAY_VENV}",
        f"{UV_BIN} pip install --python {GATEWAY_VENV_PY} {pip_args}",
        # 5. AgentScope itself, selected by released/dev mode.
        install_agentscope_cmd,
    ]


def render_install_agentscope_cmd_released(version: str) -> str:
    """Released-mode install: pin the same version as the host."""
    return (
        f"{UV_BIN} pip install --python {GATEWAY_VENV_PY} "
        f"--no-deps 'agentscope=={version}'"
    )


def render_install_agentscope_cmd_dev() -> str:
    """Dev-mode install: untar the uploaded tarball and ``pip install``.

    Caller is responsible for uploading the source tarball to
    :data:`DEV_SRC_TAR` before running this command.
    """
    return (
        f"mkdir -p {DEV_SRC_DIR} && "
        f"tar -xf {DEV_SRC_TAR} -C {DEV_SRC_DIR} && "
        f"{UV_BIN} pip install --python {GATEWAY_VENV_PY} "
        f"--no-deps {DEV_SRC_DIR} && "
        f"rm -rf {DEV_SRC_TAR} {DEV_SRC_DIR}"
    )


def log_bootstrap_attempt(workspace_id: str, mode: str) -> None:
    """Single info-level log so first-time bootstraps are easy to spot."""
    logger.info(
        "OpenSandboxWorkspace: bootstrapping sandbox for workspace_id=%r "
        "(mode=%s)",
        workspace_id,
        mode,
    )
