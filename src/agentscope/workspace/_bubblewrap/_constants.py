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
    # The probe is the availability gate for the whole backend, so it
    # must exercise the options every sandboxed command passes.
    # ``--clearenv`` needs bwrap >= 0.5.0 (Ubuntu 20.04 ships 0.4.0);
    # a build without it must fail this probe — and with it the clear
    # startup error — instead of passing the probe and then failing
    # every exec/read/write with "Unknown option --clearenv".
    # ``PATH`` is set explicitly because ``--clearenv`` empties the
    # environment the probe's command resolves against, matching how
    # real commands always reconstruct the environment after clearing.
    "--clearenv",
    "--setenv",
    "PATH",
    "/usr/bin:/bin",
    "--",
    "true",
]
