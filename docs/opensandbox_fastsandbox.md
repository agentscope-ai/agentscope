---
title: OpenSandbox workspaces with FastSandbox
description: Template creation and durable pause/resume for AgentScope workspaces.
---

# OpenSandbox workspaces with FastSandbox

`OpenSandboxWorkspace` and `OpenSandboxWorkspaceManager` default to
FastSandbox template creation. Configure an OpenSandbox lifecycle server
connected to FastSandbox and a successfully built Firecracker template:

```bash
export OPENSANDBOX_DOMAIN="your-opensandbox-server:80"
export OPENSANDBOX_API_KEY="your-server-api-key"
export OPENSANDBOX_TEMPLATE_ID="your-succeeded-template-id"
```

Create the template through OpenSandbox's template API so it is registered in
the server's catalog. A standalone Kubernetes `SandboxTemplate` CR is not
sufficient for `Sandbox.create_from_template()`.

Use an OpenSandbox Python SDK that exposes `Sandbox.create_from_template()`.
If your installed release does not include this API, install a source-built
SDK wheel from the matching OpenSandbox repository before using template mode.

```python
from agentscope.workspace import OpenSandboxWorkspace

workspace = OpenSandboxWorkspace(
    workspace_id="my-agent-workspace",
    domain="your-opensandbox-server:80",
    api_key="your-server-api-key",
    timeout_seconds=900,
)
await workspace.initialize()
# Run agent tools through workspace.list_tools().
await workspace.close()

# A new instance with the same workspace_id resumes the existing sandbox.
workspace = OpenSandboxWorkspace(
    workspace_id="my-agent-workspace",
    domain="your-opensandbox-server:80",
    api_key="your-server-api-key",
    timeout_seconds=900,
)
await workspace.initialize()
```

You can pass `template_id` explicitly instead of using the environment.
The workspace manager forwards this option to each workspace. A missing
template fails during configuration rather than silently creating an
image-based sandbox. Existing image-based deployments can opt in explicitly
with `image="python:3.11-slim"`; `image` and `template_id` are mutually exclusive.

Templates fix the sandbox's environment, resources, and entrypoint. Configure
those during template construction; passing `env`, `resource`, or `entrypoint`
to a template-backed workspace raises an error. Workspace metadata and network
policy still accompany creation.

Template-mode identity metadata uses `agentscope-workspace-id`,
`agentscope-user-id`, and `agentscope-agent-id`. FastSandbox requires metadata
keys to be DNS labels and values to be Kubernetes label values (at most 63
characters). Explicit image mode retains the existing dotted identity keys.

First-use bootstrap supports Alpine (`apk`) and Debian/Ubuntu (`apt-get`).
It installs the gateway dependencies and builtin-tool prerequisites. Allocate
enough rootfs capacity and allow package downloads, or prepare a template with
the gateway dependencies already installed. VM readiness and completion of
workspace bootstrap are separate milestones.

The template's guest DNS must match the pool's network configuration. A
resolver pointing to the guest gateway requires a DNS proxy there. For an ACK
pool without that proxy, prepare the template with reachable DNS servers;
changing AgentScope's host resolver does not change the restored guest.

`close()` waits for `Paused`, including durable checkpoint publication, before
dropping its SDK handle. Reattachment also waits if it discovers a sandbox in
`Pausing`, avoiding an unnecessary new sandbox during that transition. The
existing workspace close contract logs teardown errors; applications that
require confirmation can check the sandbox's state through `SandboxManager`.
Use a timeout long enough for checkpoint publication and guest readiness on
your deployment. Filesystem data, tmpfs, and processes are restored by
Firecracker; the workspace restarts its MCP gateway after reattachment.

## Agent example

Set `DEEPSEEK_API_KEY` in your environment. The example defaults to
`DEEPSEEK_BASE_URL=https://api.deepseek.com` and `DEEPSEEK_MODEL=deepseek-flash`:

```bash
REPORT_PATH=fastsandbox-agent-result.json \
  python examples/opensandbox_fastsandbox_agent.py
```

The agent writes and runs a Python program using workspace tools. The example
then closes and reconstructs the workspace, checks the sandbox identity,
filesystem, tmpfs, boot ID, and background process identity, and runs a second
agent task after resume. It kills only its own test sandbox afterward. The
report contains timings and verification results, without credentials.

## ACK verification

On 2026-10-08, this example passed one complete cycle on Alibaba Cloud's
managed Kubernetes (ACK), using `deepseek-flash` at `https://api.deepseek.com`.
The agent created and executed a Python file, paused its workspace, then read
and executed the file again after resume. The sandbox ID, filesystem, tmpfs,
boot ID, and background process identity were preserved.

The first initialization took 72.76 seconds, durable pause 44.18 seconds, and
resume plus workspace initialization 119.70 seconds. The scheduler selected
another node for this resume. These are end-to-end functional-test timings,
including transfers, SDK readiness checks, and gateway setup; they are not a
same-node runtime benchmark. See the [test result](test-results/opensandbox_fastsandbox_ack_20261008.json).

The test used an Alpine template with reachable ACK DNS servers and
OpenSandbox SDK `1.1.1rc2.dev106+gc7dc78a4.d20261008`. Its template ID is recorded
in the result, along with the AgentScope version and verification flags.
