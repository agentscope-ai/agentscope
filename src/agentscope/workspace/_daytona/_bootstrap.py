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

``--no-deps`` is mandatory for the same reason as E2B: the gateway only
imports ``agentscope.mcp.MCPClient`` whose transitive needs are covered
by the gateway base requirements. Pulling AgentScope's full dependency
tree would make first bootstrap slower and more fragile in minimal
snapshots.
"""

import shlex

from .._utils import _GATEWAY_BASE_REQUIREMENTS

# ── shared constants ───────────────────────────────────────────────

#: Default Daytona SDK operation timeout, in seconds.
DEFAULT_TIMEOUT = 60

#: Default port the in-sandbox gateway listens on.
DEFAULT_GATEWAY_PORT = 5600

#: Default interval for the manager-side TTL sweeper.
DEFAULT_SWEEP_INTERVAL = 300.0

#: Sandbox label key used to map workspace_id -> Daytona sandbox.
METADATA_WORKSPACE_ID_KEY = "agentscope.workspace.id"

# Gateway runtime home under the SDK-reported user home. The shared
# sandbox base derives venv, gateway script, log, and glob-helper paths
# from this anchor.
GATEWAY_HOME_NAME = ".agentscope"


# ── bootstrap command sequence ─────────────────────────────────────


def bootstrap_commands(
    *,
    user_home: str,
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
        user_home: SDK-reported sandbox user home.
        gateway_venv: Gateway virtualenv path.
        gateway_venv_py: Python executable inside the gateway venv.
        uv_bin: Full path to the uv executable after installation.
        extra_pip: Extra Python packages to install into the gateway
            venv alongside the base requirements.
        install_agentscope_cmd: Shell command that installs
            ``agentscope`` into the gateway venv.

    Returns:
        A list of shell command strings, to be executed in order.
        Each must exit 0; a non-zero exit aborts the bootstrap.
    """
    pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(extra_pip or [])
    pip_args = " ".join(shlex.quote(pkg) for pkg in pip_pkgs)
    uv_install_dir = f"{user_home}/.local/bin"

    return [
        # 1. Install ripgrep for the Grep builtin tool. Daytona
        #    snapshots may run as non-root users, so use sudo here and
        #    avoid assuming the SDK-selected OS user is root.
        "sudo apt-get update -qq "
        "&& sudo apt-get install -y --no-install-recommends ripgrep "
        "&& sudo rm -rf /var/lib/apt/lists/*",
        # 2. Astral uv — same shell installer as Docker/E2B. The SDK
        #    reported user home is used as the install root so we do
        #    not assume /home/daytona or any fixed OS user.
        f"curl -LsSf https://astral.sh/uv/install.sh "
        f"| env UV_INSTALL_DIR={shlex.quote(uv_install_dir)} "
        f"INSTALLER_NO_MODIFY_PATH=1 sh",
        # 3. Gateway venv + base requirements.
        f"{shlex.quote(uv_bin)} venv {shlex.quote(gateway_venv)}",
        f"{shlex.quote(uv_bin)} pip install --python "
        f"{shlex.quote(gateway_venv_py)} {pip_args}",
        # 4. agentscope itself.
        install_agentscope_cmd,
    ]
