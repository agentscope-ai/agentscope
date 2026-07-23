# -*- coding: utf-8 -*-
"""Constants for Bubblewrap-backed workspaces."""

DEFAULT_GATEWAY_PORT = None

SANDBOX_WORKDIR = "/workspace"
SANDBOX_TMPDIR = "/tmp"
SANDBOX_CACHE_DIR = f"{SANDBOX_TMPDIR}/.agentscope-cache"
GATEWAY_HOME = f"{SANDBOX_WORKDIR}/.agentscope"

BWRAP_SMOKE_PROBE_ARGV = [
    "bwrap",
    "--die-with-parent",
    "--new-session",
    "--unshare-all",
    "--share-net",
    "--ro-bind",
    "/",
    "/",
    "--proc",
    "/proc",
    "--dev",
    "/dev",
    "--",
    "true",
]
