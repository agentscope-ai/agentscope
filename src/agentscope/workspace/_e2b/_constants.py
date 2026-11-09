# -*- coding: utf-8 -*-
"""E2B-specific constants for :class:`E2BWorkspace`.

Path layout (venv, script, log, helper) is derived on the base class
from ``_gateway_home``. This module only carries defaults that cannot
be derived: template, timeouts, port, sandbox user, metadata key.
"""

#: Default E2B template. Matches the SDK's ``base`` template — has
#: Ubuntu + python3 + curl out of the box.
DEFAULT_TEMPLATE = "base"

#: Default keep-alive timeout in seconds for newly-created sandboxes.
DEFAULT_TIMEOUT = 300

#: Default port the in-sandbox gateway listens on.
DEFAULT_GATEWAY_PORT = 5600

#: Sandbox-side runtime user (E2B ``base`` runs as ``user``, not root).
SANDBOX_USER_HOME = "/home/user"

#: Sandbox-side workdir and gateway home — derived once here so the
#: E2B workspace and any hypothetical siblings share the layout.
SANDBOX_WORKDIR = f"{SANDBOX_USER_HOME}/workspace"
GATEWAY_HOME = f"{SANDBOX_USER_HOME}/.agentscope"

#: Sandbox metadata key used to map workspace_id → sandbox_id.
METADATA_WORKSPACE_ID_KEY = "agentscope.workspace.id"
