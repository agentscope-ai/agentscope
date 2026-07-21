# Apple Container Workspace

AgentScope workspace backed by Apple's `container` CLI. Runs agent tool calls (Bash, Read, Write, Edit, Grep, Glob) inside an Apple Container Linux VM.

## Prerequisites

- **macOS 26+** on Apple silicon (Intel Macs are not supported by Apple Container).
- **Apple Container 1.0.0 or later** (tested on 1.0.0 and 1.1.0). Install from the [Apple Container developer site](https://developer.apple.com/container/).
- **`container system start`** must be running before creating any workspace.
- **Outbound network access from the container VM** is required during the first `initialize()` — the bootstrap installs system packages via `apt-get` and downloads `uv` via the installer script. If the container VM cannot reach the internet, initialize will fail at the bootstrap step with an apt-get or curl error.

### Network / Proxy

The container VM shares the host's network stack by default. If your host uses a proxy:

```bash
# Verify the container VM can reach external hosts before using the workspace:
container exec <container-id> curl -I https://pypi.org
```

DNS resolution inside the VM should work out of the box. If TCP connections time out while DNS resolves, check whether your host firewall is blocking traffic from the container VM.

## Supported Images

Any Debian/Ubuntu-based OCI image with `python3` pre-installed. The default is `python:3.11-slim`. Official Docker library images are recognized in both short form and canonical form:

```python
# These are equivalent:
AppleContainerWorkspace(base_image="python:3.11-slim")
AppleContainerWorkspace(base_image="docker.io/library/python:3.11-slim")
```

## Configuration

```python
from agentscope.workspace import AppleContainerWorkspace

ws = AppleContainerWorkspace(
    workspace_id="my-workspace",       # optional, auto-generated if omitted
    base_image="python:3.11-slim",     # default
    gateway_port=5600,                 # TCP port for the MCP gateway inside the container
    cpus=2,                            # virtual CPUs allocated to the container
    memory="2G",                       # memory limit (e.g. "512M", "4G")
    env={"MY_VAR": "value"},           # environment variables inside the container
    extra_pip=["requests"],            # extra pip packages installed during bootstrap
)
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `workspace_id` | auto UUID | Stable identifier, also used as the container name suffix (`as_ws_<id>`). |
| `base_image` | `python:3.11-slim` | OCI image to run. Must have `python3` and be Debian/Ubuntu-based for apt-get bootstrap. |
| `gateway_port` | `5600` | TCP port the in-container MCP gateway listens on. |
| `cpus` | `2` | Virtual CPUs for the container. |
| `memory` | `"2G"` | Memory limit. |
| `env` | `{}` | Environment variables injected into the container. |
| `extra_pip` | `[]` | Additional pip packages installed in the gateway venv during bootstrap. |

## Lifecycle

```python
import asyncio
from agentscope.workspace import AppleContainerWorkspace

async def main():
    async with AppleContainerWorkspace() as ws:
        # Container is created, bootstrapped, gateway is running.
        backend = ws.get_backend()
        result = await backend.exec_shell(["echo", "hello"])
        print(result.stdout)

    # Container is stopped and removed.

asyncio.run(main())
```

- `initialize()` / `async with`: creates the container, pulls the base image if not cached, bootstraps the gateway venv (apt-get + uv + pip), and starts the MCP gateway.
- `close()`: stops and removes the container. Filesystem state is not persisted.
- Second `initialize()` on the same container name is a no-op if the container is still running.

