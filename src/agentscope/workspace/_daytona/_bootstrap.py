# -*- coding: utf-8 -*-
"""Bootstrap helpers for :class:`DaytonaWorkspace` first-time setup.

Like E2B, Daytona has no local Docker image-build phase in AgentScope:
we create or attach to a provider sandbox, then run a short shell
bootstrap sequence in that sandbox. Before calling these helpers,
:class:`DaytonaWorkspace` asks the SDK for the sandbox's real workdir
and user home, then passes those paths in. This module therefore does
not assume a fixed OS user, root home, or ``/home/daytona`` layout.

The sequence installs only what the AgentScope runtime needs inside the
sandbox: ripgrep for builtin search tools, uv, a gateway virtualenv,
gateway base requirements, AgentScope itself, and optional
user-provided Python packages for gateway-side MCP/tool execution.

Two install modes mirror Docker and E2B:

* **released** — ``agentscope`` is in site-packages on the host;
  install the same version from PyPI inside the sandbox.
* **dev** — running from a source checkout; tar the project tree on
  the host, upload it as a single blob, untar inside the sandbox, and
  ``uv pip install --no-deps`` it.

``--no-deps`` is mandatory for the same reason as E2B: the gateway only
imports ``agentscope.mcp.MCPClient`` whose transitive needs are covered
by the gateway base requirements. Pulling AgentScope's full dependency
tree would make first bootstrap slower and more fragile in minimal
snapshots.
"""

import io
import shlex
import tarfile

from ..._logging import logger
from .._utils import (
    _GATEWAY_BASE_REQUIREMENTS,
    _agentscope_source_root,
    _is_source_ignored,
)

# ── shared constants ───────────────────────────────────────────────

#: Default Daytona SDK operation timeout, in seconds.
DEFAULT_TIMEOUT = 60

#: Default port the in-sandbox gateway listens on.
DEFAULT_GATEWAY_PORT = 5600

#: Default interval for the manager-side TTL sweeper.
DEFAULT_SWEEP_INTERVAL = 300.0

#: Sandbox label key used to map workspace_id -> Daytona sandbox.
METADATA_WORKSPACE_ID_KEY = "agentscope.workspace.id"

# Workspace-side persistent layout names. The actual absolute paths are
# derived from Daytona SDK path APIs at runtime.
DATA_DIR_NAME = "data"
SKILLS_DIR_NAME = "skills"
SESSIONS_DIR_NAME = "sessions"
MCP_FILE_NAME = ".mcp"

# Gateway runtime names under the SDK-reported user home.
GATEWAY_HOME_NAME = ".agentscope"
GATEWAY_VENV_NAME = ".venv"
GATEWAY_SCRIPT_NAME = "_mcp_gateway_app.py"
GLOB_HELPER_NAME = "_glob_helper.py"
GATEWAY_CONFIG_NAME = "gateway.config.json"
GATEWAY_LOG_NAME = "gateway.log"

# Dev-mode source upload names under the gateway home.
DEV_SRC_TAR_NAME = "agentscope_src.tar"
DEV_SRC_DIR_NAME = "agentscope_src"

# Daytona preview proxy header. This is separate from AgentScope's
# gateway bearer token.
DAYTONA_PREVIEW_TOKEN_HEADER = "x-daytona-preview-token"


# ── source tarball (dev mode only) ─────────────────────────────────


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """``tarfile.add`` filter — skip caches, hidden files and heavy dirs.

    Returning ``None`` for an entry tells :meth:`tarfile.add` to also
    *stop recursing* into it, so the root archive entry (``arcname="."``)
    must be passed through unchanged — otherwise the entire tree is
    excluded and the resulting tarball is effectively empty.
    """
    if info.name == ".":
        return info
    name = info.name.split("/")[-1]
    if _is_source_ignored(name):
        return None
    return info


def build_source_tarball() -> bytes:
    """Tar up the agentscope source tree for dev-mode upload.

    Returns the tar bytes (uncompressed); the sandbox will untar with
    ``tar -xf`` (no ``-z``).
    """
    root = _agentscope_source_root()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        tf.add(str(root), arcname=".", filter=_tar_filter)
    return buf.getvalue()


# ── bootstrap command sequence ─────────────────────────────────────


def bootstrap_commands(
    *,
    workdir: str,
    data_dir: str,
    skills_dir: str,
    sessions_dir: str,
    user_home: str,
    gateway_home: str,
    gateway_venv: str,
    gateway_venv_py: str,
    uv_bin: str,
    extra_pip: list[str] | None,
    install_agentscope_cmd: str,
) -> list[str]:
    """Return shell commands that provision a fresh Daytona sandbox.

    Commands are returned as strings because provider backends execute
    them through ``["sh", "-c", command]``. Callers provide all paths
    after SDK path discovery so this sequence remains independent from
    Daytona snapshot defaults.

    Args:
        workdir: SDK-reported sandbox workdir where workspace state
            should be stored.
        data_dir: Directory for offloaded multimodal payloads.
        skills_dir: Directory for reusable skills.
        sessions_dir: Directory for session context and tool results.
        user_home: SDK-reported sandbox user home.
        gateway_home: Directory that stores gateway runtime files.
        gateway_venv: Gateway virtualenv path.
        gateway_venv_py: Python executable inside the gateway venv.
        uv_bin: Full path to the uv executable after installation.
        extra_pip: Extra Python packages to install into the gateway
            venv alongside the base requirements.
        install_agentscope_cmd: Shell command that installs
            ``agentscope`` into the gateway venv. Built per install
            mode by :func:`render_install_agentscope_cmd_released`
            (released) or :func:`render_install_agentscope_cmd_dev`
            (dev).

    Returns:
        A list of shell command strings, to be executed in order.
        Each must exit 0; a non-zero exit aborts the bootstrap.
    """
    pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(extra_pip or [])
    pip_args = " ".join(shlex.quote(pkg) for pkg in pip_pkgs)
    uv_install_dir = f"{user_home}/.local/bin"

    return [
        # 1. Persistent layout. mkdir -p is cheap on resume too —
        #    keeps the command idempotent in case bootstrap is re-run.
        f"mkdir -p {shlex.quote(workdir)} {shlex.quote(data_dir)} "
        f"{shlex.quote(skills_dir)} {shlex.quote(sessions_dir)} "
        f"{shlex.quote(gateway_home)}",
        # 2. Install ripgrep for the Grep builtin tool. Daytona
        #    snapshots may run as non-root users, so use sudo here and
        #    avoid assuming the SDK-selected OS user is root.
        "sudo apt-get update -qq "
        "&& sudo apt-get install -y --no-install-recommends ripgrep "
        "&& sudo rm -rf /var/lib/apt/lists/*",
        # 3. Astral uv — same shell installer as Docker/E2B. The SDK
        #    reported user home is used as the install root so we do
        #    not assume /home/daytona or any fixed OS user.
        f"curl -LsSf https://astral.sh/uv/install.sh "
        f"| env UV_INSTALL_DIR={shlex.quote(uv_install_dir)} "
        f"INSTALLER_NO_MODIFY_PATH=1 sh",
        # 4. Gateway venv + base requirements.
        f"{shlex.quote(uv_bin)} venv {shlex.quote(gateway_venv)}",
        f"{shlex.quote(uv_bin)} pip install --python "
        f"{shlex.quote(gateway_venv_py)} {pip_args}",
        # 5. agentscope itself (mode-dependent).
        install_agentscope_cmd,
    ]


def render_install_agentscope_cmd_released(
    *,
    uv_bin: str,
    gateway_venv_py: str,
    version: str,
) -> str:
    """Released-mode install: pin the same version as the host."""
    return (
        f"{shlex.quote(uv_bin)} pip install --python "
        f"{shlex.quote(gateway_venv_py)} "
        f"--no-deps {shlex.quote(f'agentscope=={version}')}"
    )


def render_install_agentscope_cmd_dev(
    *,
    uv_bin: str,
    gateway_venv_py: str,
    dev_src_tar: str,
    dev_src_dir: str,
) -> str:
    """Dev-mode install: untar the uploaded tarball and ``pip install``.

    Caller is responsible for uploading the source tarball to
    ``dev_src_tar`` before running this command.
    """
    return (
        f"mkdir -p {shlex.quote(dev_src_dir)} && "
        f"tar -xf {shlex.quote(dev_src_tar)} "
        f"-C {shlex.quote(dev_src_dir)} && "
        f"{shlex.quote(uv_bin)} pip install --python "
        f"{shlex.quote(gateway_venv_py)} "
        f"--no-deps {shlex.quote(dev_src_dir)} && "
        f"rm -rf {shlex.quote(dev_src_tar)} {shlex.quote(dev_src_dir)}"
    )


def log_bootstrap_attempt(workspace_id: str, mode: str) -> None:
    """Single info-level log so first-time bootstraps are easy to spot."""
    logger.info(
        "DaytonaWorkspace: bootstrapping sandbox for workspace_id=%r "
        "(mode=%s)",
        workspace_id,
        mode,
    )
