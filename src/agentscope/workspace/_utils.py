# -*- coding: utf-8 -*-
"""Host-side helpers shared by workspace implementations.

Constants for the standard workspace layout, plus pure functions for
detecting the local ``agentscope`` version and reading scripts bundled
with the package. No Docker / E2B SDK dependency lives here.

This module is internal to ``agentscope.workspace``. Public-sounding
constants are shared within the package, not exported as user-facing
API.
"""

import importlib.resources as _res

# ── shared constants ───────────────────────────────────────────────

#: Standard workspace-relative directory for offloaded multimodal data.
DEFAULT_DATA_DIR = "data"

#: Standard workspace-relative directory for reusable skills.
DEFAULT_SKILLS_DIR = "skills"

#: Standard workspace-relative directory for session context and results.
DEFAULT_SESSIONS_DIR = "sessions"

#: Standard workspace-relative file for persisted MCP registrations.
DEFAULT_MCP_FILE = ".mcp"

DEFAULT_GATEWAY_VENV = ".venv"
DEFAULT_GATEWAY_LOG = "gateway.log"
DEFAULT_GATEWAY_SCRIPT = "_mcp_gateway_app.py"
DEFAULT_GLOB_HELPER_SCRIPT = "_glob_helper.py"

#: Minimum Python packages the gateway script needs at runtime.
#: Both Docker (image build) and E2B (sandbox bootstrap) install this
#: same tuple into the gateway venv before adding ``agentscope`` itself.
_GATEWAY_BASE_REQUIREMENTS: tuple[str, ...] = (
    "mcp",
    "uvicorn",
    "fastapi",
)


# ── gateway script ─────────────────────────────────────────────────


def _read_gateway_script_bytes() -> bytes:
    """Read the standalone gateway script as bytes via ``importlib.resources``.

    The script ships at
    ``agentscope/workspace/_mcp_gateway/_mcp_gateway_app.py``. Both
    backends copy it to a fixed in-container / in-sandbox path so the
    launch command can invoke it directly, avoiding ``python -m`` and
    the heavy ``agentscope.workspace.__init__`` import graph.
    """
    return (
        _res.files("agentscope.workspace._mcp_gateway")
        .joinpath("_mcp_gateway_app.py")
        .read_bytes()
    )


# ── builtin tool helper scripts ───────────────────────────────────


def _read_glob_helper_bytes() -> bytes:
    """Read the standalone glob helper script as bytes.

    The script ships at
    ``agentscope/tool/_builtin/_scripts/_glob_helper.py``. Both Docker
    and E2B backends copy it into the workspace so the :class:`Glob`
    tool can invoke it uniformly via ``exec_shell``.

    Returns:
        `bytes`:
            The raw contents of the ``_glob_helper.py`` script.
    """
    return (
        _res.files("agentscope.tool._builtin._scripts")
        .joinpath("_glob_helper.py")
        .read_bytes()
    )
