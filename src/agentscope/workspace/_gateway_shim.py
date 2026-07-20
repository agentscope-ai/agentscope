# -*- coding: utf-8 -*-
"""Tiny Python script that runs inside the sandbox to relay a single
HTTP request to the gateway, plus the host-side constants that drive it
through :meth:`BackendBase.exec_shell`.

Flow: host spawns ``python3 -c <SHIM_SCRIPT> ...`` via ``exec_shell``;
the shim calls the gateway's loopback port using ``urllib.request`` and
emits one JSON envelope on stdout::

    {
        "status": <int>,                  # HTTP status, or -1 on error
        "body":   "<base64-of-bytes>",    # inline when small
        "body_file": "<sandbox-path>",    # spilled when > inline_limit
        "error":  "<short message>"       # only when status == -1
    }

The gateway listens only on the sandbox's loopback; the host has no
network reachability to it. The shim relies on ``python3`` (which the
gateway venv already needs) rather than ``curl`` because we cannot
assume ``curl`` on every backend image.
"""

from __future__ import annotations

# Bodies larger than this spill to a tempfile so we don't accumulate
# multi-MB payloads in the exec stdout channel.
BODY_INLINE_LIMIT = 4 * 1024 * 1024

# ``/tmp`` is writable on every backend image we support.
SANDBOX_TMP_DIR = "/tmp"


# The shim itself, embedded as a string so it ships with this module.
# Conventions: stdlib-only; no shebang (always via ``python3 -c``);
# exit code is always 0 — failures land in the envelope; stderr is
# untouched so a Python traceback (e.g. syntax error after a botched
# edit) surfaces to the caller.
#
# ``sys.argv`` layout when run via ``python3 -c``::
#
#     argv[0] = "-c"   (Python convention)
#     argv[1] = method            (e.g. "GET" / "POST" / "DELETE")
#     argv[2] = url               (e.g. "http://127.0.0.1:5600/health")
#     argv[3] = body_file or ""   (path readable on the sandbox)
#     argv[4] = inline_limit      (bytes; int as str)
#     argv[5] = tmp_dir           (where to spill oversized responses)
SHIM_SCRIPT = r"""
import sys, json, base64, uuid, os
import urllib.request, urllib.error

method = sys.argv[1]
url = sys.argv[2]
body_file = sys.argv[3]
inline_limit = int(sys.argv[4])
tmp_dir = sys.argv[5]

body = None
if body_file:
    with open(body_file, "rb") as f:
        body = f.read()

req = urllib.request.Request(url, data=body, method=method)
if body is not None:
    req.add_header("Content-Type", "application/json")

try:
    with urllib.request.urlopen(req) as resp:
        status = int(resp.status)
        resp_body = resp.read()
except urllib.error.HTTPError as e:
    status = int(e.code)
    try:
        resp_body = e.read()
    except Exception:
        resp_body = b""
except Exception as e:
    json.dump(
        {"status": -1, "error": type(e).__name__ + ": " + str(e)},
        sys.stdout,
    )
    sys.exit(0)

env = {"status": status}
if len(resp_body) > inline_limit:
    p = os.path.join(tmp_dir, uuid.uuid4().hex + ".bin")
    with open(p, "wb") as f:
        f.write(resp_body)
    env["body_file"] = p
else:
    env["body"] = base64.b64encode(resp_body).decode("ascii")
json.dump(env, sys.stdout)
"""
