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

## Prepared gateway templates

A Firecracker template can include a running, healthy MCP gateway with an
empty registry, preloaded MCP/tool modules, and the standard empty workspace
layout. Build the image with the files in
`examples/workspace/opensandbox/template/`, then create the template through
the OpenSandbox API. Configure its readiness probe to check the gateway's
HTTP `/health` endpoint and verify the empty layout before the builder captures
the memory snapshot.

Build from the same AgentScope source version as your application.
The one-command builder packages this checkout and installs the gateway's
builtin-tool dependencies automatically:

```bash
# Local verification: build and load an amd64 image, start its gateway,
# check the empty layout/registry, and remove the validation container.
.venv/bin/python examples/workspace/opensandbox/build_template.py \
  --image agentscope-gateway:local --local

# Full build: Docker must already be logged in to this image registry.
export OPENSANDBOX_DOMAIN="your-opensandbox-server:80"
export OPENSANDBOX_API_KEY="your-server-api-key"
.venv/bin/python examples/workspace/opensandbox/build_template.py \
  --image your-registry/agentscope-gateway:your-version \
  --publish s3://your-template-bucket/publish \
  --nameservers 100.100.2.136 100.100.2.138 \
  --output dist/opensandbox-template.json
```

The tool requires Python 3.11+, `uv`, and Docker with Buildx. Full builds also
require a template-capable OpenSandbox SDK, server-side registry/artifact-store
access, and access from the application to both the lifecycle server and sandbox
ingress. Local mode does not use OpenSandbox or create a Firecracker snapshot.

Full builds push `linux/amd64`, create a native template from its immutable
image digest, wait for `Succeeded`, then restore a temporary sandbox to verify
the prepared marker and live empty gateway. Only after validation succeeds does
stdout print `export OPENSANDBOX_TEMPLATE_ID=...`; progress goes to stderr.
The temporary validation sandbox is deleted. The image and template are retained.

Defaults are one vCPU, 2 GiB memory, and a 4 GiB rootfs; override with `--cpu`,
`--memory`, and `--disk`. Use `--requirements ./requirements.txt` to bake extra
application dependencies into the gateway venv. Requirements must be portable
package requirements; local files and relative includes are not copied.
Use `--dry-run` to inspect the plan without building or contacting a server.

The JSON output records the image and template ID as soon as they are available.
`--wait-timeout` defaults to 1,800 seconds for template builds; on timeout the
remote build continues and its ID is retained for `osb template get <id>`.
Local readiness checks wait at most 120 seconds. A failed gateway validation
exits nonzero and never prints a success export.

To build and register the template manually:

```bash
uv build --wheel --out-dir dist
cp dist/agentscope-*.whl examples/workspace/opensandbox/template/
docker build -t your-registry/agentscope-gateway:your-version \
  examples/workspace/opensandbox/template/
docker push your-registry/agentscope-gateway:your-version
```

Use the resulting image digest when creating the native template. The image
contains dependencies and files; the template builder starts the gateway and
captures its running state. The startup script keeps an idle main process
alive so resetting the gateway after resume does not terminate execd:

```python
import asyncio
from opensandbox import SandboxManager
from opensandbox.models.templates import CreateTemplateRequest, TemplateReadiness

manager = await SandboxManager.create()
try:
    template = await manager.create_template(CreateTemplateRequest(
        image="your-registry/agentscope-gateway@sha256:your-image-digest",
        resource_limits={"cpu": "1", "memory": "2Gi", "disk": "2Gi"},
        entrypoint=["sh", "/opt/agentscope-template/start_gateway.sh"],
        # Set reachable guest resolvers only when your pool requires them.
        env={"SANDBOX_NAMESERVERS": "100.100.2.136 100.100.2.138"},
        readiness=TemplateReadiness(
            probe="cmd://python3 /opt/agentscope-template/check_gateway.py",
        ),
        publish="s3://your-template-bucket/publish",
        format="native",
    ))
    while template.status.phase not in {"Succeeded", "Failed"}:
        await asyncio.sleep(2)
        template = await manager.get_template(template.template_id)
    if template.status.phase != "Succeeded":
        raise RuntimeError(template.status.message)
    print(template.template_id)  # Set OPENSANDBOX_TEMPLATE_ID to this value.
finally:
    await manager.close()
```

For a fresh template-created sandbox, AgentScope validates
`/root/.agentscope/template-state.json` against its gateway port, layout, and
gateway/helper script hashes, then performs one live gateway health check.
When they match, it reuses the running gateway and skips directory creation,
skill-layout migration, and gateway restart. User-provided skill paths are
still seeded, and MCP registration remains lazy and scoped to each agent and
session. Missing or incompatible markers, unhealthy gateways, and explicit
image mode retain the normal initialization path. `extra_pip` uses that path
as well; bake any application dependencies into the image in advance.

This optimization applies only to newly created sandboxes. Reattaching an
existing workspace restores its persisted MCP declarations and resets the
gateway through the existing initialization flow, so a restored registry or
stale external MCP connection is not mistaken for an empty public template.
Do not bake workspace IDs, user files, credentials, conversation history, or
connected MCP clients into a shared template. Each sandbox receives its own
copy of the prepared VM state.

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

## User demo

Use `examples/workspace/opensandbox/main.py` for a minimal agent workflow:
create or attach a workspace, write and execute a Python file through agent
tools, pause, then create a new workspace handle with the **same workspace ID**
and read the file after resume. The demo uses public workspace APIs and
confirms the paused state with `SandboxManager`.

Install the fork branch containing this integration rather than an unmodified
AgentScope release:

```bash
git clone --branch codex/opensandbox-fastsandbox-workspace \
  https://github.com/jwx0925/agentscope.git
cd agentscope
uv venv --python 3.12
uv pip install -e '.[workspace-opensandbox]'
# Install a matching OpenSandbox SDK wheel if the release lacks template APIs.
uv pip install /path/to/opensandbox-1.1.1rc2.dev106+gc7dc78a4.d20261008-py3-none-any.whl
.venv/bin/python -c \
  "from opensandbox import Sandbox; assert hasattr(Sandbox, 'create_from_template')"
```

Run from an application environment that can reach **both** the OpenSandbox
lifecycle server and the sandbox ingress addresses returned by that server.
The ACK service names below are reachable inside the cluster. A laptop cannot
use these names directly, and forwarding only the lifecycle server port does
not make the ingress reachable. The Kubernetes API address is not an
OpenSandbox lifecycle endpoint.

```bash
# Current ACK example: run the application in a Pod with cluster networking.
export OPENSANDBOX_DOMAIN="opensandbox-server.opensandbox-system.svc:80"
export OPENSANDBOX_PROTOCOL="http"
export OPENSANDBOX_API_KEY="your-opensandbox-server-api-key"
# The prestarted gateway template verified on this ACK cluster:
export OPENSANDBOX_TEMPLATE_ID="tpl-d4ba6ea1-c091-4ed7-81dd-247cdcf80da8"
export DEEPSEEK_BASE_URL="https://api.deepseek.com"
export DEEPSEEK_MODEL="deepseek-flash"
export DEEPSEEK_API_KEY="your-deepseek-api-key"

.venv/bin/python examples/workspace/opensandbox/main.py
# Or choose a stable ID; rerunning with it reattaches before sandbox expiry.
.venv/bin/python examples/workspace/opensandbox/main.py \
  --workspace-id my-fastsandbox-demo
```

The application talks to OpenSandbox; its server selects the FastSandbox
backend. It does not connect directly to a Fastlet or Firecracker. Keep the
model key in the application environment, not in the shared VM template.
Do not pass `image`, `env`, `resource`, `entrypoint`, or `extra_pip` when using
the prepared fast path; prepare dependencies and runtime settings in the
template instead. Optional application skills can be supplied with
`skill_paths=["./my-skill"]` and are seeded after creation.

The demo ends with the workspace **paused and retained**, not deleted. Its
sandbox lifetime is 1,800 seconds; save the printed workspace ID for reuse
before expiration. `close()` currently includes durable checkpoint publication
and can take tens of seconds. Agent conversation history is separate from
sandbox state: the demo creates a new agent after resume and reads saved files.
The fixed demo tasks use `PermissionMode.BYPASS` to run without interactive
confirmation; applications should choose their own permission policy.

For a service managing multiple users, use `OpenSandboxWorkspaceManager` and
keep a stable, application-owned workspace ID:

```python
from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager

async with OpenSandboxWorkspaceManager(
    template_id="your-succeeded-template-id",
    domain="your-opensandbox-server:80",
    api_key="your-server-api-key",
    timeout_seconds=1800,
) as manager:
    workspace = await manager.get_workspace(
        user_id="user-1", agent_id="agent-1", session_id="session-1",
        workspace_id="user-1-agent-1",
    )
    # Use workspace.list_tools() when constructing the agent's Toolkit.
    await manager.close(workspace.workspace_id)  # Pause and evict the handle.
    workspace = await manager.get_workspace(
        user_id="user-1", agent_id="agent-1", session_id="session-1",
        workspace_id="user-1-agent-1",
    )  # Resume the same sandbox on this cache miss.
```

## Agent example

Set `DEEPSEEK_API_KEY` in your environment. The example defaults to
`DEEPSEEK_BASE_URL=https://api.deepseek.com` and `DEEPSEEK_MODEL=deepseek-flash`:

```bash
REPORT_PATH=fastsandbox-agent-result.json \
  python examples/workspace/opensandbox/verify_pause_resume.py
```

The agent writes and runs a Python program using workspace tools. The example
then closes and reconstructs the workspace, checks the sandbox identity,
filesystem, tmpfs, boot ID, and background process identity, and runs a second
agent task after resume. It kills only its own test sandbox afterward. The
report contains timings and verification results, without credentials.

## ACK verification

On 2026-10-08, the agent example passed a complete cycle on Alibaba Cloud's
managed Kubernetes (ACK), using `deepseek-flash` at `https://api.deepseek.com`.
The agent created and executed a Python file, paused its workspace, then read
and executed the file again after resume. Its sandbox identity, filesystem,
tmpfs, boot ID, and background process were preserved. See the
[agent test result](test-results/opensandbox_fastsandbox_ack_20261008.json).

The prepared gateway template was then verified separately on the same ACK
cluster. The template was already cached on the node; two warmups preceded ten
serial measurements. Each measured workspace ran Python, file read/write,
ripgrep, and glob matching, and checked its empty MCP registry and filesystem
isolation. These timings exclude model inference and template build/download:

| Operation | Median | Range | Samples |
| --- | ---: | ---: | ---: |
| SDK sandbox creation and readiness | 57.8 ms | 56.9–69.3 ms | 10 |
| Complete workspace initialization | 357.2 ms | 354.5–366.8 ms | 10 |
| Prepared marker and live gateway check | 278.8 ms | 278.4–279.7 ms | 10 |

The earlier template with only dependencies preinstalled took 3.802 seconds
for complete initialization (five samples, range 3.768–16.900 seconds,
including the slow sample). Preparing the live gateway and empty layout
reduced the median by 90.6%. These were separate runs rather than a controlled
host/storage comparison. User skills still require seeding: the functional
case with one supplied skill initialized in 1.204 seconds.

The remaining live gateway check executes through execd's command API, whose
default stream shutdown grace period is 200 ms. Sandbox creation and complete
AgentScope workspace readiness are therefore different measurements.

A durable pause/resume cycle was verified on the **same node and Fastlet**.
Pause took 51.531 seconds, including 37.289 seconds to publish a 5 GiB
checkpoint. Full resume and workspace initialization took 68.080 seconds:
66.157 seconds were spent provisioning the SDK handle and waiting for service
readiness, followed by the normal workspace restoration flow. Runtime restore
itself logged 31.7 ms, but `/ping` returned transient HTTP 503 responses. The
cause of that readiness delay remains unresolved; runtime readiness alone
must not be reported as a usable workspace. Gateway restart took 1.294
seconds. Files, tmpfs, boot ID, background process, user skills, callable MCP
restoration, and agent/session scope isolation all passed.

The native template used one vCPU, 2 GiB of memory, a 3 GiB rootfs, and the host
CPU configuration (`none`, after the AMD host rejected T2A). The test used
OpenSandbox SDK `1.1.1rc2.dev106+gc7dc78a4.d20261008`. See the
[prepared gateway test result](test-results/opensandbox_prestarted_gateway_ack_20261008.json)
for all samples, template identity, placement evidence, and checks.
