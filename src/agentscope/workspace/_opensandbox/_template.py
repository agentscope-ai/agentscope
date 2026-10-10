# -*- coding: utf-8 -*-
"""Versioned contract for an empty, prepared FastSandbox workspace."""

import hashlib

from .._utils import _read_gateway_script_bytes, _read_glob_helper_bytes
from ._constants import GATEWAY_HOME, SANDBOX_WORKDIR

PREPARED_STATE_FILE = f"{GATEWAY_HOME}/template-state.json"


def prepared_template_state(gateway_port: int) -> dict:
    """Describe the layout and scripts required to reuse a fresh gateway.

    Version 1 promises an empty MCP registry and an empty workspace with
    ``data``, ``skills/.seed``, and ``sessions`` directories. The gateway
    must already be running; callers still perform a live health check.
    """
    return {
        "schema_version": 1,
        "workdir": SANDBOX_WORKDIR,
        "gateway_home": GATEWAY_HOME,
        "gateway_port": gateway_port,
        "gateway_script_sha256": hashlib.sha256(
            _read_gateway_script_bytes(),
        ).hexdigest(),
        "glob_helper_sha256": hashlib.sha256(
            _read_glob_helper_bytes(),
        ).hexdigest(),
    }
