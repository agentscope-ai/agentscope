# -*- coding: utf-8 -*-
"""Constants for :class:`DaytonaWorkspace`, mirroring the E2B and K8s
``_constants`` modules.

Only defaults that cannot be derived on the shared sandbox base live
here: SDK operation timeout, gateway port, sweeper interval, the
sandbox label key used for reattachment, and the gateway home anchor
name. Every derived path (venv, python, script, glob helper, log) and
the bootstrap command sequence live on the workspace / base class, not
here.
"""

# ── shared constants ───────────────────────────────────────────────

#: Default Daytona SDK operation timeout, in seconds. Bootstrap runs
#: ``apt install``, the uv installer, and Python dependency installs, so
#: the default must be generous enough to cover a cold first start.
DEFAULT_TIMEOUT = 300

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
