# -*- coding: utf-8 -*-
"""Bootstrap helpers for :class:`K8SWorkspace` first-time provisioning.

Mirrors :mod:`agentscope.workspace._e2b._bootstrap` but targets a
Kubernetes Pod environment. Pods typically run as root, so ``uv`` is
installed to ``/usr/local/bin`` (no user-home workaround needed).

Two install modes (identical to E2B / Docker):

* **released** — ``agentscope`` lives in site-packages on the host;
  install the same version from PyPI inside the Pod.
* **dev** — running from a source checkout; tar the project tree on
  the host, upload it via exec+base64, untar inside the Pod, and
  ``uv pip install --no-deps`` it.
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

#: Default container image.
DEFAULT_IMAGE = "python:3.11-slim"

#: Default Kubernetes namespace.
DEFAULT_NAMESPACE = "default"

#: Default gateway port inside the Pod.
DEFAULT_GATEWAY_PORT = 5600

# Pod-side persistent layout.
POD_WORKDIR = "/workspace"
POD_DATA_DIR = f"{POD_WORKDIR}/data"
POD_SKILLS_DIR = f"{POD_WORKDIR}/skills"
POD_SESSIONS_DIR = f"{POD_WORKDIR}/sessions"
POD_MCP_FILE = f"{POD_WORKDIR}/.mcp"

# Gateway home — venv, script, config, logs.
GATEWAY_HOME = "/opt/agentscope"
GATEWAY_VENV = f"{GATEWAY_HOME}/.venv"
GATEWAY_VENV_PY = f"{GATEWAY_VENV}/bin/python"
GATEWAY_SCRIPT = f"{GATEWAY_HOME}/_mcp_gateway_app.py"
GATEWAY_CONFIG = f"{GATEWAY_HOME}/gateway.config.json"
GATEWAY_LOG = f"{GATEWAY_HOME}/gateway.log"

# uv binary — Pods run as root, so /usr/local/bin is writable.
UV_BIN = "/usr/local/bin/uv"

# Label keys for Pod discovery.
LABEL_WORKSPACE = "agentscope.workspace"
LABEL_WORKSPACE_ID = "agentscope.workspace.id"

#: Tarball drop point for dev-mode source uploads.
DEV_SRC_TAR = "/tmp/agentscope_src.tar"
DEV_SRC_DIR = "/tmp/agentscope_src"


# ── source tarball (dev mode only) ─────────────────────────────────


def _tar_filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
    """``tarfile.add`` filter — skip caches, hidden files and heavy dirs."""
    if info.name == ".":
        return info
    name = info.name.split("/")[-1]
    if _is_source_ignored(name):
        return None
    return info


def build_source_tarball() -> bytes:
    """Tar up the agentscope source tree for dev-mode upload."""
    root = _agentscope_source_root()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        tf.add(str(root), arcname=".", filter=_tar_filter)
    return buf.getvalue()


# ── bootstrap command sequence ─────────────────────────────────────


def bootstrap_commands(
    *,
    extra_pip: list[str] | None = None,
    install_agentscope_cmd: str,
) -> list[str]:
    """Return the shell commands to provision a fresh K8S Pod.

    Args:
        extra_pip: Extra Python packages to install into the gateway
            venv alongside the base requirements.
        install_agentscope_cmd: Shell command that installs
            ``agentscope`` into the gateway venv.

    Returns:
        A list of shell command strings, to be executed in order.
    """
    pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(extra_pip or [])
    pip_args = " ".join(pip_pkgs)

    return [
        f"mkdir -p {POD_DATA_DIR} {POD_SKILLS_DIR} "
        f"{POD_SESSIONS_DIR} {GATEWAY_HOME}",
        "curl -LsSf https://astral.sh/uv/install.sh "
        f"| env UV_INSTALL_DIR=/usr/local/bin "
        f"INSTALLER_NO_MODIFY_PATH=1 sh",
        f"{UV_BIN} venv {GATEWAY_VENV}",
        f"{UV_BIN} pip install --python {GATEWAY_VENV_PY} {pip_args}",
        install_agentscope_cmd,
    ]


def render_install_agentscope_cmd_released(version: str) -> str:
    """Released-mode install: pin the same version as the host."""
    return (
        f"{UV_BIN} pip install --python {GATEWAY_VENV_PY} "
        f"--no-deps 'agentscope=={version}'"
    )


def render_install_agentscope_cmd_dev() -> str:
    """Dev-mode install: untar the uploaded tarball and ``pip install``."""
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
        "K8SWorkspace: bootstrapping pod for workspace_id=%r (mode=%s)",
        workspace_id,
        mode,
    )
