# -*- coding: utf-8 -*-
"""Bootstrap helpers for :class:`E2BWorkspace` first-time provisioning.

Unlike :class:`DockerWorkspace`, E2B has no image-build phase: it
attaches to a pre-built template (``base`` by default). The first
time we create a sandbox for a given ``workspace_id``, we run a
sequence of shell commands to install ``uv``, the gateway venv, the
gateway base requirements, and the ``agentscope`` package. After this
the gateway script is uploaded into the sandbox.

These artefacts persist across ``sandbox.pause()`` / ``resume`` so
the bootstrap cost is paid exactly once per sandbox lifetime.

Two install modes mirror Docker:

* **released** — ``agentscope`` is in site-packages on the host;
  install the same version from PyPI inside the sandbox.
* **dev** — running from a source checkout; tar the project tree on
  the host, upload it as a single blob, untar inside the sandbox, and
  ``uv pip install --no-deps`` it.

``--no-deps`` is mandatory: the gateway only imports
``agentscope.mcp.MCPClient`` whose transitive needs are
``mcp / pydantic / httpx`` (already installed via the gateway base
requirements). Pulling agentscope's full dep tree drags in heavy /
Rust-built packages (ripgrep, tree_sitter, opentelemetry, openai,
anthropic, dashscope) for which the sandbox base image typically has
no compiler, exactly the same trap we hit on Docker.
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

#: Default E2B template. Matches the SDK's ``base`` template — has
#: Ubuntu + python3 + curl out of the box, which is everything the
#: bootstrap needs.
DEFAULT_TEMPLATE = "base"

#: Default keep-alive timeout in seconds for newly-created sandboxes.
DEFAULT_TIMEOUT = 300

#: Default port the in-sandbox gateway listens on.
DEFAULT_GATEWAY_PORT = 5600

#: Sandbox-side runtime user (E2B base image runs as ``user``, not
#: ``root``). All the per-workspace paths sit under its ``$HOME``.
SANDBOX_USER_HOME = "/home/user"

# Workspace-side persistent layout — mirrors the DockerWorkspace one.
SANDBOX_WORKDIR = f"{SANDBOX_USER_HOME}/workspace"
SANDBOX_DATA_DIR = f"{SANDBOX_WORKDIR}/data"
SANDBOX_SKILLS_DIR = f"{SANDBOX_WORKDIR}/skills"
SANDBOX_SESSIONS_DIR = f"{SANDBOX_WORKDIR}/sessions"
SANDBOX_MCP_FILE = f"{SANDBOX_WORKDIR}/.mcp"

# Gateway home — venv, script, config, logs.
GATEWAY_HOME = f"{SANDBOX_USER_HOME}/.agentscope"
GATEWAY_VENV = f"{GATEWAY_HOME}/.venv"
GATEWAY_VENV_PY = f"{GATEWAY_VENV}/bin/python"
GATEWAY_SCRIPT = f"{GATEWAY_HOME}/_mcp_gateway_app.py"
GATEWAY_CONFIG = f"{GATEWAY_HOME}/gateway.config.json"
GATEWAY_LOG = f"{GATEWAY_HOME}/gateway.log"

# uv binary lives under the user's local bin since we cannot write to
# /usr/local/bin without sudo on the default E2B image.
UV_BIN = f"{SANDBOX_USER_HOME}/.local/bin/uv"

#: Sandbox metadata key used to map workspace_id → sandbox_id. The
#: manager filters ``list_sandboxes`` by this key on cache miss to
#: locate (and resume) an existing sandbox.
METADATA_WORKSPACE_ID_KEY = "agentscope.workspace.id"

#: Tarball drop point for dev-mode ``agentscope`` source uploads.
DEV_SRC_TAR = "/tmp/agentscope_src.tar"
DEV_SRC_DIR = "/tmp/agentscope_src"

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
    extra_pip: list[str] | None = None,
    install_agentscope_cmd: str,
) -> list[str]:
    """Return the shell commands to provision a fresh E2B sandbox.

    Args:
        extra_pip: Extra Python packages to install into the gateway
            venv alongside the base requirements.
        install_agentscope_cmd: Shell command that installs
            ``agentscope`` into the gateway venv. Built per install
            mode by :func:`render_install_agentscope_cmd` (released)
            or :func:`render_install_agentscope_cmd_dev` (dev).

    Returns:
        A list of shell command strings, to be executed in order.
        Each must exit 0; a non-zero exit aborts the bootstrap.
    """
    pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(extra_pip or [])
    pip_args = " ".join(pip_pkgs)

    return [
        # 1. Persistent layout. mkdir -p is cheap on resume too — keeps
        #    the command idempotent in case bootstrap is re-run.
        f"mkdir -p {SANDBOX_DATA_DIR} {SANDBOX_SKILLS_DIR} "
        f"{SANDBOX_SESSIONS_DIR} {GATEWAY_HOME}",
        # 2. Astral uv — same shell installer as Docker. The base E2B
        #    image already ships ``curl``; we land uv at
        #    ``$HOME/.local/bin`` since the sandbox user has no sudo by
        #    default. ``INSTALLER_NO_MODIFY_PATH=1`` suppresses shell
        #    rc edits — we always invoke uv by full path here anyway.
        f"curl -LsSf https://astral.sh/uv/install.sh "
        f"| env UV_INSTALL_DIR={SANDBOX_USER_HOME}/.local/bin "
        f"INSTALLER_NO_MODIFY_PATH=1 sh",
        # 3. Gateway venv + base requirements.
        f"{UV_BIN} venv {GATEWAY_VENV}",
        f"{UV_BIN} pip install --python {GATEWAY_VENV_PY} {pip_args}",
        # 4. agentscope itself (mode-dependent).
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
        "E2BWorkspace: bootstrapping sandbox for workspace_id=%r (mode=%s)",
        workspace_id,
        mode,
    )
