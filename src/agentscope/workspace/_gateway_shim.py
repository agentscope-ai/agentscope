# -*- coding: utf-8 -*-
"""Tiny Python script that runs **inside the sandbox** to relay a single
HTTP request to the gateway, plus the host-side helpers that drive it
through :class:`agentscope.tool.BackendBase.exec_shell`.

Architectural shift
-------------------

Previously the workspace reached its in-sandbox MCP gateway through a
host-visible URL — DockerWorkspace mapped the gateway TCP port to a
random host port and dialed ``http://127.0.0.1:<host_port>``;
E2BWorkspace used ``sandbox.get_host(port)`` + ``X-Access-Token`` to
ride E2B's HTTPS proxy. Both flows require host → sandbox network
reachability.

The new flow is: **every request runs inside the sandbox**. The host
spawns a short Python script there via ``backend.exec_shell`` which
makes the call against the gateway's loopback port using nothing but
the standard library (``urllib.request``), serialises the response into
a self-describing JSON envelope, and prints it to stdout. The host
reads stdout back as an ``ExecResult`` and reconstructs the status
code and body bytes (response headers are not transmitted — callers
consume JSON bodies directly).

Why ``python3 -c`` (and not ``curl``)
-------------------------------------

* ``python3`` is guaranteed by every backend we support — the gateway
  itself is a Python process, and our Dockerfile sets up a venv. We
  cannot make the same guarantee for ``curl`` on third-party E2B
  templates.
* No new dependency: ``urllib.request`` is stdlib.
* argv stays free of shell-special characters; the whole script is one
  argv element delivered to ``python3 -c`` via the backend's argv-based
  ``exec_shell`` (Docker passes ``cmd=`` directly; E2B's ``shlex.quote``
  POSIX-quotes the element). No interpolation, no escape bugs.

Wire format
-----------

Stdout always carries a single JSON object — even on transport failure
the shim exits 0 and reports the error in the envelope, so callers
need only one parse path::

    {
        "status": <int>,                  # HTTP status, or -1 on error
        "body":   "<base64-of-bytes>",    # only when status >= 0 and small
        "body_file": "<sandbox-path>",    # only when body is large
        "error":  "<short message>"       # only when status == -1
    }

For typical MCP tool calls (≤ 1 MiB) the body rides inline as base64.
For exceptional payloads (> ``BODY_INLINE_LIMIT`` bytes) the shim
spills to ``/tmp/<uuid>.bin`` and returns the path; the caller reads
that file via ``backend.read_file`` and deletes it. This avoids loading
multi-megabyte responses through stdout, which both ``aiodocker`` and
``e2b`` accumulate in host RAM.
"""

from __future__ import annotations

# Request bodies larger than this go through a temp file rather than
# riding through stdout. 4 MiB comfortably covers any real MCP tool
# result without paying the disk-roundtrip cost on the common path.
BODY_INLINE_LIMIT = 4 * 1024 * 1024

# Where the shim spills oversized payloads. ``/tmp`` is writable on
# every backend image (Docker python:3.11-slim, E2B default templates).
SANDBOX_TMP_DIR = "/tmp"


# The shim itself. Embedded as a string so it ships with this module —
# the host always knows the exact source the sandbox is executing.
#
# Conventions kept tight on purpose:
#  * stdlib-only;
#  * no shebang (we always invoke as ``python3 -c``);
#  * exit code is always 0 — failures land in the envelope;
#  * stderr is intentionally untouched so a Python traceback (e.g.
#    syntax error after a botched edit) is visible to the caller.
#
# ``sys.argv`` layout when run via ``python3 -c``::
#
#     argv[0] = "-c"   (Python convention)
#     argv[1] = method            (e.g. "GET" / "POST" / "DELETE")
#     argv[2] = url               (e.g. "http://127.0.0.1:5600/health")
#     argv[3] = token             (bearer; empty string for none)
#     argv[4] = body_file or ""   (path readable on the sandbox)
#     argv[5] = inline_limit      (bytes; int as str)
#     argv[6] = tmp_dir           (where to spill oversized responses)
SHIM_SCRIPT = r"""
import sys, json, base64, uuid, os
import urllib.request, urllib.error

method = sys.argv[1]
url = sys.argv[2]
token = sys.argv[3]
body_file = sys.argv[4]
inline_limit = int(sys.argv[5])
tmp_dir = sys.argv[6]

body = None
if body_file:
    with open(body_file, "rb") as f:
        body = f.read()

req = urllib.request.Request(url, data=body, method=method)
if token:
    req.add_header("Authorization", "Bearer " + token)
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
