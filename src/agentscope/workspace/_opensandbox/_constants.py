# -*- coding: utf-8 -*-
"""Constants for :class:`OpenSandboxWorkspace`, mirroring the E2B and
K8s ``_constants`` modules.

Only defaults that cannot be derived on the base class live here:
image, timeouts, gateway port, sandbox metadata key, plus the two
sandbox-side anchors the workspace must set (``SANDBOX_WORKDIR`` and
``GATEWAY_HOME``). The bootstrap command sequence and every derived
path (venv, python, script, glob helper, log) live on the workspace /
base class, not here.
"""

#: Default OpenSandbox image. The slim Python image is small but still
#: has enough package-manager support for installing curl, certificates,
#: and ripgrep during bootstrap.
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
# short root-owned path. The data/skills/sessions subdirectories are
# created by the base class from ``workdir``.
SANDBOX_WORKDIR = "/workspace"

# Gateway home — the single sandbox-side anchor the workspace must set.
# The venv, entry script, glob helper, and log paths are all derived
# from it by the base class.
GATEWAY_HOME = "/root/.agentscope"

#: Sandbox metadata key used to map workspace_id to sandbox id. The
#: workspace filters ``list_sandbox_infos`` by this key on cache miss to
#: locate and resume an existing sandbox.
METADATA_WORKSPACE_ID_KEY = "agentscope.workspace.id"
