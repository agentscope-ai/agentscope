# -*- coding: utf-8 -*-
"""Bootstrap helpers for :class:`DaytonaWorkspace` first-time setup.

Daytona workspaces are provisioned at runtime, like E2B. Before calling
these helpers, :class:`DaytonaWorkspace` asks the SDK for the sandbox's
real workdir and user home, then passes those paths in. This module
therefore does not assume a fixed OS user, root home, or
``/home/daytona`` layout.

The bootstrap sequence installs only what the AgentScope runtime needs
inside the sandbox: ripgrep for builtin search tools, uv, a gateway
virtualenv, AgentScope itself, and optional user-provided Python
packages for gateway-side MCP/tool execution.
"""

import io
import tarfile

from ..._logging import logger
from .._utils import (
    _GATEWAY_BASE_REQUIREMENTS,
    _agentscope_source_root,
    _is_source_ignored,
)

# ── shared constants ───────────────────────────────────────────────

DEFAULT_TIMEOUT = 60
DEFAULT_GATEWAY_PORT = 5600
DEFAULT_SWEEP_INTERVAL = 300.0

METADATA_WORKSPACE_ID_KEY = "agentscope.workspace.id"

DATA_DIR_NAME = "data"
SKILLS_DIR_NAME = "skills"
SESSIONS_DIR_NAME = "sessions"
MCP_FILE_NAME = ".mcp"

GATEWAY_HOME_NAME = ".agentscope"
GATEWAY_VENV_NAME = ".venv"
GATEWAY_SCRIPT_NAME = "_mcp_gateway_app.py"
GLOB_HELPER_NAME = "_glob_helper.py"
GATEWAY_CONFIG_NAME = "gateway.config.json"
GATEWAY_LOG_NAME = "gateway.log"
DEV_SRC_TAR_NAME = "agentscope_src.tar"
DEV_SRC_DIR_NAME = "agentscope_src"

DAYTONA_PREVIEW_TOKEN_HEADER = "x-daytona-preview-token"


# ── source tarball (dev mode only) ─────────────────────────────────


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """``tarfile.add`` filter used for dev-mode source upload."""
    if info.name == ".":
        return info
    name = info.name.split("/")[-1]
    if _is_source_ignored(name):
        return None
    return info


def build_source_tarball() -> bytes:
    """Tar up the AgentScope source tree for dev-mode upload."""
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
    """
    pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(extra_pip or [])
    pip_args = " ".join(pip_pkgs)

    return [
        f"mkdir -p {workdir} {data_dir} {skills_dir} "
        f"{sessions_dir} {gateway_home}",
        "sudo apt-get update -qq "
        "&& sudo apt-get install -y --no-install-recommends ripgrep "
        "&& sudo rm -rf /var/lib/apt/lists/*",
        f"curl -LsSf https://astral.sh/uv/install.sh "
        f"| env UV_INSTALL_DIR={user_home}/.local/bin "
        f"INSTALLER_NO_MODIFY_PATH=1 sh",
        f"{uv_bin} venv {gateway_venv}",
        f"{uv_bin} pip install --python {gateway_venv_py} {pip_args}",
        install_agentscope_cmd,
    ]


def render_install_agentscope_cmd_released(
    *,
    uv_bin: str,
    gateway_venv_py: str,
    version: str,
) -> str:
    """Released-mode install: pin the same AgentScope version as host."""
    return (
        f"{uv_bin} pip install --python {gateway_venv_py} "
        f"--no-deps 'agentscope=={version}'"
    )


def render_install_agentscope_cmd_dev(
    *,
    uv_bin: str,
    gateway_venv_py: str,
    dev_src_tar: str,
    dev_src_dir: str,
) -> str:
    """Dev-mode install from an uploaded source tarball."""
    return (
        f"mkdir -p {dev_src_dir} && "
        f"tar -xf {dev_src_tar} -C {dev_src_dir} && "
        f"{uv_bin} pip install --python {gateway_venv_py} "
        f"--no-deps {dev_src_dir} && "
        f"rm -rf {dev_src_tar} {dev_src_dir}"
    )


def log_bootstrap_attempt(workspace_id: str, mode: str) -> None:
    """Single info-level log so first-time bootstraps are easy to spot."""
    logger.info(
        "DaytonaWorkspace: bootstrapping sandbox for workspace_id=%r "
        "(mode=%s)",
        workspace_id,
        mode,
    )
