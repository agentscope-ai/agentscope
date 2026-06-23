# -*- coding: utf-8 -*-
"""Bootstrap helpers for :class:`K8sWorkspace` first-time provisioning.

Like :class:`E2BWorkspace`, K8s has no image-build phase: it uses
a stock base image (``python:3.11-slim`` by default) and provisions
via shell commands the first time a Pod is started for a workspace.

Unlike E2B's ``base`` template (Ubuntu with python3 + curl), the
``python:3.11-slim`` image is Debian-minimal and lacks ``curl`` and
``ca-certificates``. The bootstrap sequence therefore installs these
system dependencies first — mirroring the Docker Dockerfile.template
apt-get step (lines 14-18).

Two install modes mirror Docker and E2B:

* **released** — ``agentscope`` is in site-packages on the host;
  install the same version from PyPI inside the Pod.
* **dev** — running from a source checkout; tar the project tree on
  the host, upload it via ``K8sBackend.write_file``, untar inside
  the Pod, and ``uv pip install --no-deps`` it.

``--no-deps`` is mandatory: see :mod:`_e2b._bootstrap` for rationale.
"""

import re

from ..._logging import logger
from .._utils import _GATEWAY_BASE_REQUIREMENTS

# ── shared constants ───────────────────────────────────────────────

#: Default base image. Debian-slim with Python 3.11, which provides
#: ``sh`` (dash), ``tar``, ``base64`` (coreutils) out of the box.
#: Missing: ``curl``, ``ca-certificates`` — installed in bootstrap.
DEFAULT_IMAGE = "python:3.11-slim"

#: Default port the in-Pod gateway listens on.
DEFAULT_GATEWAY_PORT = 5600

#: Minimum system packages the bootstrap apt-installs before anything
#: else. ``curl`` + ``ca-certificates`` are needed for the uv
#: installer; ``ripgrep`` is needed by the builtin Grep tool.
#: This list mirrors Docker's Dockerfile.template apt-get step.
SYSTEM_DEPS = ("curl", "ca-certificates", "ripgrep")

# Pod-side persistent layout — mirrors Docker and E2B.
POD_WORKDIR = "/workspace"
POD_DATA_DIR = f"{POD_WORKDIR}/data"
POD_SKILLS_DIR = f"{POD_WORKDIR}/skills"
POD_SESSIONS_DIR = f"{POD_WORKDIR}/sessions"
POD_MCP_FILE = f"{POD_WORKDIR}/.mcp"

# Gateway home — venv, script, config, logs.
GATEWAY_HOME = "/root/.agentscope"
GATEWAY_VENV = f"{GATEWAY_HOME}/.venv"
GATEWAY_VENV_PY = f"{GATEWAY_VENV}/bin/python"
GATEWAY_SCRIPT = f"{GATEWAY_HOME}/_mcp_gateway_app.py"
GLOB_HELPER_SCRIPT = f"{GATEWAY_HOME}/_glob_helper.py"
GATEWAY_CONFIG = f"{GATEWAY_HOME}/gateway.config.json"
GATEWAY_LOG = f"{GATEWAY_HOME}/gateway.log"

#: uv binary lands in /usr/local/bin (root-writable on slim images).
UV_BIN = "/usr/local/bin/uv"

#: Tarball drop point for dev-mode agentscope source uploads.
DEV_SRC_TAR = f"{GATEWAY_HOME}/agentscope_src.tar"
DEV_SRC_DIR = f"{GATEWAY_HOME}/agentscope_src"


# ── K8s name sanitisation ─────────────────────────────────────────

_K8S_UNSAFE_RE = re.compile(r"[^a-z0-9-]")


def _k8s_safe_name(workspace_id: str, prefix: str = "as-ws-") -> str:
    """Produce an RFC-1123 compliant K8s resource name.

    Rules: lowercase alphanumeric + hyphens, max 63 characters,
    must not end with a hyphen.

    Args:
        workspace_id (`str`):
            Raw workspace identifier.
        prefix (`str`):
            Name prefix (e.g. ``"as-ws-"``).

    Returns:
        `str`:
            A sanitised name safe for K8s Pod / PVC / label values.
    """
    name = prefix + _K8S_UNSAFE_RE.sub("-", workspace_id.lower())
    return name[:63].rstrip("-")


# ── bootstrap command sequence ─────────────────────────────────────


def bootstrap_commands(
    *,
    extra_pip: list[str] | None = None,
    install_agentscope_cmd: str,
) -> list[str]:
    """Return shell commands to provision a fresh K8s Pod.

    Args:
        extra_pip (`list[str] | None`):
            Extra Python packages to install into the gateway venv
            alongside the base requirements.
        install_agentscope_cmd (`str`):
            Shell command that installs ``agentscope`` into the
            gateway venv. Built per install mode.

    Returns:
        `list[str]`:
            Shell command strings to execute in order.
    """
    pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(extra_pip or [])
    pip_args = " ".join(pip_pkgs)
    sys_deps = " ".join(SYSTEM_DEPS)

    return [
        # 1. Persistent layout.
        f"mkdir -p {POD_DATA_DIR} {POD_SKILLS_DIR} "
        f"{POD_SESSIONS_DIR} {GATEWAY_HOME}",
        # 2. System dependencies (curl, ca-certificates, ripgrep).
        # Mirrors Docker Dockerfile.template apt-get step. The slim
        # image runs as root so no sudo needed.
        f"apt-get update -qq "
        f"&& apt-get install -y --no-install-recommends {sys_deps} "
        f"&& rm -rf /var/lib/apt/lists/*",
        # 3. Astral uv — same shell installer as Docker and E2B.
        "curl -LsSf https://astral.sh/uv/install.sh "
        "| env UV_INSTALL_DIR=/usr/local/bin "
        "INSTALLER_NO_MODIFY_PATH=1 sh",
        # 4. Gateway venv + base requirements.
        f"{UV_BIN} venv {GATEWAY_VENV}",
        f"{UV_BIN} pip install --python {GATEWAY_VENV_PY} {pip_args}",
        # 5. agentscope itself (mode-dependent).
        install_agentscope_cmd,
    ]


def render_install_agentscope_cmd_released(version: str) -> str:
    """Released-mode install: pin the same version as the host."""
    return (
        f"{UV_BIN} pip install --python {GATEWAY_VENV_PY} "
        f"--no-deps 'agentscope=={version}'"
    )


def render_install_agentscope_cmd_dev() -> str:
    """Dev-mode install: untar the uploaded tarball and pip install."""
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
        "K8sWorkspace: bootstrapping Pod for workspace_id=%r (mode=%s)",
        workspace_id,
        mode,
    )
