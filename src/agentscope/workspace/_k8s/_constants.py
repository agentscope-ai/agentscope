# -*- coding: utf-8 -*-
"""K8s-specific constants for :class:`K8sWorkspace`.

Path layout (venv, script, log, helper) is derived on the base class
from ``_gateway_home``. This module only carries defaults that cannot
be derived: image, port, apt deps, workdir/gateway_home, and the
K8s-name sanitiser.
"""

import hashlib
import re

#: Default base image. Debian-slim with Python 3.11 — provides
#: ``sh`` (dash), ``tar``, ``base64`` (coreutils) out of the box.
#: Missing ``curl`` and ``ca-certificates`` — installed in bootstrap.
DEFAULT_IMAGE = "python:3.11-slim"

#: Default port the in-Pod gateway listens on.
DEFAULT_GATEWAY_PORT = 5600

#: apt packages the bootstrap installs before uv.
#: ``curl`` + ``ca-certificates`` are needed for the uv installer;
#: ``ripgrep`` is needed by the builtin Grep tool.
SYSTEM_DEPS = ("curl", "ca-certificates", "ripgrep")

#: Pod-side persistent layout — matches Docker/E2B semantics.
POD_WORKDIR = "/workspace"
GATEWAY_HOME = "/root/.agentscope"


# ── K8s name sanitisation ─────────────────────────────────────────

_K8S_UNSAFE_RE = re.compile(r"[^a-z0-9-]")


def _k8s_safe_name(workspace_id: str, prefix: str = "as-ws-") -> str:
    """Produce an RFC-1123 compliant K8s resource name.

    Rules: lowercase alphanumeric + hyphens, max 63 characters,
    must not end with a hyphen.
    """
    lowered = workspace_id.lower()
    sanitized = _K8S_UNSAFE_RE.sub("-", lowered)
    name = prefix + sanitized
    # Replacing unsafe characters and truncating are lossy: distinct ids
    # (long ids sharing a prefix, or ids differing only in characters
    # replaced by ``-``) would otherwise collapse onto the same Pod name.
    # When information is lost, append a digest of the original id so
    # every workspace keeps its own Pod.
    if sanitized != lowered or len(name) > 63:
        suffix = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:8]
        max_head = 63 - len(suffix) - 1
        head = name[:max_head].rstrip("-")
        name = f"{head}-{suffix}"
    return name[:63].rstrip("-")
