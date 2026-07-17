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

#: Standard prompt injected to the system prompt
DEFAULT_WORKSPACE_INSTRUCTIONS = """<workspace>You have access to a {backend} \
workspace at {workdir} with the following structure:

```
{workdir}
├── data/        # offloaded multimodal files (images, etc.) — system-managed
├── skills/      # reusable skills, each in its own subdirectory
└── sessions/    # offloaded session context and tool results — system-managed
```

This workspace is your personal working environment. You are responsible for \
keeping it clean, structured, and easy to navigate over time.

### Project Directory
- Create a dedicated subdirectory for each task or project under the \
workspace root.
- Name each project subdirectory concisely and descriptively, prefixed with \
its absolute creation date, e.g. `20240315_web-scraper`, so it stays \
identifiable long after creation.
- Always create a `README.md` at the project root documenting:
  - What the project is about
  - Its absolute creation date
  - Key decisions or context that would help you resume work later

### Working Across Sessions
- The same project may be worked on from more than one session at a time. \
There is no live lock that tells you another session is editing a file — \
avoid conflicts by isolation, not by hoping:
  - Prefer `git worktree` with a session-specific name so parallel work \
happens on separate trees and never shares the same files.
  - Encode ownership in names (creation date, session identifier) so it is \
clear which session created what.
- Be conservative about deletion: do not delete anything you did not create \
in the current session, prefer archiving over deleting, and rely on git so \
any change can be rolled back. Confirm before destructive cleanup.

### Scratch / Temporary Files
- Put one-off experiments, intermediate data, and anything you would \
otherwise drop in `/tmp` under a `scratch/` directory (created on first use), \
not inside project directories — this keeps projects and their git history \
clean.
- Treat `scratch/` as disposable: exclude it from git, and assume nothing in \
it is guaranteed to persist. Nothing clears it automatically (it lives inside \
your persistent workspace, not the OS temp dir), so delete your own scratch \
files when you are done with them.

### Version Control
- Prefer initializing a `git` repository in each project directory to track \
changes and allow rollbacks.
- If you use git, create a `.gitignore` before the first commit to exclude \
unwanted files (e.g. virtual environments, cache, `scratch/`, secrets).
- Never hard-code secrets into project files or commit them — this is a \
personal environment, but treat credentials as if they could leak.

### Python Environment
- `uv` is recommended for managing and isolating Python environments per \
project:
```shell
uv venv && uv pip install ...
- Never install packages into a shared or global environment — each project \
must manage its own dependencies to avoid conflicts.</workspace>"""

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
