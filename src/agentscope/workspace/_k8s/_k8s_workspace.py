# -*- coding: utf-8 -*-
"""K8SWorkspace — sandboxed workspace backed by a Kubernetes Pod.

Architecture
------------

Mirrors :class:`agentscope.workspace.E2BWorkspace` but replaces the
E2B SDK with ``kubernetes_asyncio``:

* **Lifecycle.** ``initialize()`` looks up an existing Pod by label
  selector and reattaches, or creates a fresh one and runs the
  bootstrap shell sequence.  ``close()`` deletes the Pod; an optional
  PVC preserves the workspace filesystem across restarts.
* **Gateway communication.** The MCP gateway runs inside the Pod on
  ``localhost:<port>`` and is **never exposed externally**. All HTTP
  traffic to the gateway is routed through a custom
  :class:`K8SExecTransport` that translates ``httpx`` requests into
  ``kubectl exec`` + ``curl`` calls. This eliminates the need for
  port-forward, Services, or any network exposure.
* **File I/O.** Reads and writes use ``exec`` with base64 encoding,
  avoiding the need for a shared filesystem or kubectl cp.
* **Bootstrap.** First-time provisioning installs uv + a gateway venv
  + agentscope (``--no-deps``) via exec commands. Presence of the
  gateway script is the idempotency marker.
"""

import asyncio
import base64
import hashlib
import json
import mimetypes
import posixpath
import shlex
import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import AnyUrl

from ..._logging import logger
from ...mcp import MCPClient
from ...message import (
    Base64Source,
    DataBlock,
    Msg,
    TextBlock,
    ToolResultBlock,
    URLSource,
)
from ...skill import Skill
from ...tool import ToolBase
from .._base import WorkspaceBase
from .._gateway_client import (
    GatewayClient,
    GatewayMCPClient,
)
from ._bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
    DEFAULT_NAMESPACE,
    DEV_SRC_TAR,
    GATEWAY_CONFIG,
    GATEWAY_HOME,
    GATEWAY_LOG,
    GATEWAY_SCRIPT,
    GATEWAY_VENV_PY,
    LABEL_WORKSPACE,
    LABEL_WORKSPACE_ID,
    POD_DATA_DIR,
    POD_MCP_FILE,
    POD_SESSIONS_DIR,
    POD_SKILLS_DIR,
    POD_WORKDIR,
    bootstrap_commands,
    build_source_tarball,
    log_bootstrap_attempt,
    render_install_agentscope_cmd_dev,
    render_install_agentscope_cmd_released,
)
from .._utils import (
    _agentscope_version,
    _is_released_install,
    _read_gateway_script_bytes,
)


_DEFAULT_INSTRUCTIONS = """<workspace>
You have a Kubernetes-based workspace. All tool calls execute **inside
the Pod** at ``{workdir}``.

Layout:

```
{workdir}
├── data/        # offloaded multimodal files
├── skills/      # reusable skills
└── sessions/    # session context and tool results
```

Use the MCP-provided tools to interact with the Pod's filesystem
and processes.
</workspace>"""


# ── exec output parsing ───────────────────────────────────────────

_META_MARKER = "\n__K8S_META__\n"


# ── small helpers ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _ExecResult:
    """Result of running a command inside the Pod via ``exec``."""

    exit_code: int
    stdout: bytes
    stderr: bytes

    def ok(self) -> bool:
        """Return ``True`` iff the command exited with code ``0``."""
        return self.exit_code == 0


# ── K8S exec-based httpx transport ─────────────────────────────────


_SKIP_HEADERS = frozenset(
    {
        "host",
        "user-agent",
        "accept-encoding",
        "connection",
        "content-length",
        "transfer-encoding",
        "accept",
    },
)


class K8SExecTransport(httpx.AsyncBaseTransport):
    """Custom httpx transport that routes HTTP requests through
    K8S ``exec`` + ``curl`` inside the Pod.

    The gateway port is never exposed outside the Pod. All HTTP
    traffic goes through: host → K8S exec API → curl localhost →
    gateway.
    """

    def __init__(self, exec_fn: Any) -> None:
        self._exec_fn = exec_fn

    async def handle_async_request(
        self,
        request: httpx.Request,
    ) -> httpx.Response:
        """Translate an httpx request into a curl command executed
        inside the Pod."""
        curl_parts: list[str] = [
            "curl",
            "-s",
            "-w",
            r"\n%{http_code}",
            "-X",
            request.method,
        ]

        for name, value in request.headers.raw:
            header_name = name.decode("ascii").lower()
            if header_name not in _SKIP_HEADERS:
                curl_parts.extend(
                    [
                        "-H",
                        f"{name.decode('ascii')}: {value.decode('ascii')}",
                    ],
                )

        body = request.content
        if body:
            has_ct = any(
                name.decode("ascii").lower() == "content-type"
                for name, _ in request.headers.raw
            )
            if not has_ct:
                curl_parts.extend(["-H", "Content-Type: application/json"])
            curl_parts.extend(["-d", body.decode("utf-8")])

        curl_parts.append(str(request.url))

        cmd = " ".join(shlex.quote(p) for p in curl_parts)
        result = await self._exec_fn(cmd)

        if not result.ok():
            return httpx.Response(
                502,
                content=result.stderr or result.stdout or b"exec failed",
            )

        output = result.stdout.decode("utf-8")
        parts = output.rsplit("\n", 1)

        if len(parts) == 2 and parts[1].strip().isdigit():
            body_text = parts[0]
            status_code = int(parts[1].strip())
        else:
            body_text = output
            status_code = 502

        return httpx.Response(
            status_code=status_code,
            content=body_text.encode("utf-8"),
            headers={"content-type": "application/json"},
        )


# ── the workspace ──────────────────────────────────────────────────


class K8SWorkspace(WorkspaceBase):
    """Workspace backed by a Kubernetes Pod.

    Gateway communication goes through ``exec`` + ``curl`` — no port
    is exposed outside the Pod.
    """

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        image: str = DEFAULT_IMAGE,
        namespace: str = DEFAULT_NAMESPACE,
        kubeconfig: str | None = None,
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        pvc_name: str | None = None,
        env: dict[str, str] | None = None,
        pod_labels: dict[str, str] | None = None,
        extra_pip: list[str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Construct a :class:`K8SWorkspace`.

        The Pod is *not* started here; call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id: Stable identifier used as the Pod label
                value for reattachment. ``None`` generates a UUID.
            image: Container image for the Pod.
            namespace: Kubernetes namespace.
            kubeconfig: Path to kubeconfig file. ``None`` tries
                in-cluster config first, then falls back to the
                default kubeconfig.
            gateway_port: TCP port the gateway listens on inside
                the Pod (localhost only, never exposed).
            pvc_name: PersistentVolumeClaim name to mount at the
                workspace directory. ``None`` means no persistence.
            env: Environment variables set inside the Pod.
            pod_labels: Extra labels merged onto the Pod.
            extra_pip: Extra Python packages installed in the
                gateway venv during bootstrap.
            instructions: System-prompt template for the agent.
            default_mcps: MCPs seeded on first initialize.
            skill_paths: Skill directories seeded on first
                initialize.
        """
        super().__init__(workspace_id=workspace_id)

        self.image = image
        self.namespace = namespace
        self.kubeconfig = kubeconfig
        self.gateway_port = gateway_port
        self.pvc_name = pvc_name
        self.env: dict[str, str] = dict(env or {})
        self.pod_labels: dict[str, str] = dict(pod_labels or {})
        self.extra_pip: list[str] = list(extra_pip or [])
        self.instructions = instructions

        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

        # runtime state
        self._api_client: Any = None
        self._core_api: Any = None
        self._pod_name: str = ""
        self._gateway: GatewayClient | None = None
        self._gateway_token: str = ""
        self._mcps: list[MCPClient] = []
        self._gateway_clients: dict[str, GatewayMCPClient] = {}
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()

    # ── lifecycle ───────────────────────────────────────────────

    async def initialize(self) -> None:
        """Find or create the Pod, bootstrap it, and start the gateway.

        Idempotent — a no-op when already alive.
        """
        if self.is_alive:
            return

        await self._load_k8s_config()
        await self._attach_or_create_pod()
        await self._wait_until_ready()

        if not (
            await self._exec(
                f"test -f {shlex.quote(GATEWAY_SCRIPT)}",
            )
        ).ok():
            await self._exec(
                f"mkdir -p {shlex.quote(POD_WORKDIR)}",
            )
            await self._run_bootstrap()

        self._mcps = await self._restore_or_seed_mcps()

        self._gateway_token = uuid.uuid4().hex

        await self._exec("pkill -f _mcp_gateway_app.py || true")

        await self._write_gateway_config()
        await self._start_gateway_process()

        transport = K8SExecTransport(exec_fn=self._exec)
        self._gateway = GatewayClient(
            base_url=f"http://localhost:{self.gateway_port}",
            token=self._gateway_token,
        )
        self._gateway._http = httpx.AsyncClient(transport=transport)

        await self._wait_for_gateway()

        self._gateway_clients = {
            c.name: c for c in await self._gateway.list_mcps()
        }

        await self._save_mcp_file()
        await self._seed_skills()

        self.is_alive = True

    async def reset(self) -> None:
        """Return the workspace to an empty state."""
        async with self._mcp_lock, self._skill_lock:
            for gw_client in list(self._gateway_clients.values()):
                try:
                    await gw_client.close()
                except Exception as e:
                    logger.warning(
                        "MCP %r close failed during reset: %s",
                        gw_client.name,
                        e,
                    )
            self._gateway_clients.clear()
            self._mcps = []

            paths = [POD_SESSIONS_DIR, POD_DATA_DIR, POD_SKILLS_DIR]
            await self._exec(
                "rm -rf " + " ".join(shlex.quote(p) for p in paths),
            )
            await self._save_mcp_file()

    async def close(self) -> None:
        """Delete the Pod and release host-side resources.

        PVC (if configured) is preserved for the next initialize.
        """
        if self._gateway is not None:
            try:
                await self._gateway.aclose()
            except Exception:
                pass
            self._gateway = None
        self._gateway_clients.clear()

        if self._core_api is not None and self._pod_name:
            try:
                await self._core_api.delete_namespaced_pod(
                    self._pod_name,
                    self.namespace,
                )
            except Exception as e:
                logger.warning("K8SWorkspace: pod delete failed: %s", e)

        if self._api_client is not None:
            try:
                await self._api_client.close()
            except Exception:
                pass
            self._api_client = None
            self._core_api = None

        self._pod_name = ""
        self.is_alive = False

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        return self.instructions.format(workdir=POD_WORKDIR)

    # ── tool / MCP / skill discovery ────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        return []

    async def list_mcps(self) -> list[MCPClient]:
        return list(self._gateway_clients.values())

    async def list_skills(self) -> list[Skill]:
        import frontmatter as fm

        result = await self._exec(
            f"find {POD_SKILLS_DIR} -name SKILL.md 2>/dev/null || true",
        )
        if not result.ok():
            return []
        listing = result.stdout.decode(errors="replace").strip()
        if not listing:
            return []

        skills: list[Skill] = []
        for md_path in (line.strip() for line in listing.split("\n")):
            if not md_path:
                continue
            try:
                raw = await self._read(md_path)
                doc = fm.loads(raw.decode("utf-8"))
                name = doc.get("name")
                desc = doc.get("description")
                if not name or not desc:
                    continue
                skills.append(
                    Skill(
                        name=str(name),
                        description=str(desc),
                        dir=posixpath.dirname(md_path),
                        markdown=doc.content or "",
                        updated_at=0.0,
                    ),
                )
            except Exception as e:
                logger.warning("Failed to load skill %s: %s", md_path, e)
        return skills

    # ── dynamic MCP management ──────────────────────────────────

    async def add_mcp(self, mcp_client: MCPClient) -> None:
        async with self._mcp_lock:
            if mcp_client.name in self._gateway_clients:
                raise ValueError(
                    f"MCP {mcp_client.name!r} already exists in workspace.",
                )
            spec = mcp_client.model_dump(mode="json")
            assert self._gateway is not None
            gw_client = self._gateway.make_client(spec)
            await gw_client.connect()
            self._mcps.append(mcp_client)
            self._gateway_clients[gw_client.name] = gw_client
            await self._save_mcp_file()

    async def remove_mcp(self, name: str) -> None:
        async with self._mcp_lock:
            gw_client = self._gateway_clients.pop(name, None)
            if gw_client is None:
                logger.warning("MCP %r not found in workspace", name)
                return
            try:
                await gw_client.close()
            except Exception as e:
                logger.warning("MCP %r close failed: %s", name, e)
            self._mcps = [m for m in self._mcps if m.name != name]
            await self._save_mcp_file()

    # ── dynamic skill management ────────────────────────────────

    async def add_skill(self, skill_path: str) -> None:
        import os

        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path!r}: SKILL.md not found",
            )

        async with self._skill_lock:
            await self._exec(f"mkdir -p {POD_SKILLS_DIR}")
            dir_name = os.path.basename(os.path.abspath(skill_path))

            check = await self._exec(
                f"test -e " f"{shlex.quote(POD_SKILLS_DIR + '/' + dir_name)}",
            )
            if check.ok():
                raise ValueError(
                    f"Skill directory {dir_name!r} already exists in "
                    f"{POD_SKILLS_DIR}",
                )

            for root, _dirs, files in os.walk(skill_path):
                for fname in files:
                    local = os.path.join(root, fname)
                    rel = os.path.relpath(local, skill_path)
                    remote = f"{POD_SKILLS_DIR}/{dir_name}/{rel}"
                    with open(local, "rb") as f:
                        data = f.read()
                    await self._write(remote, data)

            logger.info(
                "K8SWorkspace: added skill %r at %s/%s",
                dir_name,
                POD_SKILLS_DIR,
                dir_name,
            )

    async def remove_skill(self, name: str) -> None:
        skills = await self.list_skills()
        target_dir: str | None = None
        for s in skills:
            if s.name == name:
                target_dir = s.dir
                break
        if target_dir is None:
            available = [s.name for s in skills]
            raise KeyError(
                f"Skill {name!r} not found. Available: {available}",
            )
        result = await self._exec(f"rm -rf {shlex.quote(target_dir)}")
        if not result.ok():
            raise RuntimeError(
                f"Failed to remove skill {name!r}: "
                f"{result.stderr.decode(errors='replace')}",
            )

    # ── offload ─────────────────────────────────────────────────

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        base = f"{POD_SESSIONS_DIR}/{session_id}"
        path = f"{base}/context.jsonl"

        copied = deepcopy(msgs)
        lines: list[str] = []
        for msg in copied:
            if not isinstance(msg.content, str):
                content = []
                for block in msg.content:
                    if isinstance(block, DataBlock) and isinstance(
                        block.source,
                        Base64Source,
                    ):
                        block = await self._offload_data_block(block)
                    content.append(block)
                msg.content = content
            lines.append(msg.model_dump_json())

        await self._exec(f"mkdir -p {shlex.quote(base)}")
        existing = b""
        try:
            existing = await self._read(path)
        except FileNotFoundError:
            pass
        await self._write(
            path,
            existing + ("\n".join(lines) + "\n").encode("utf-8"),
        )
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        base = f"{POD_SESSIONS_DIR}/{session_id}"
        path = f"{base}/tool_result-{tool_result.id}.txt"

        parts: list[str] = []
        if isinstance(tool_result.output, str):
            parts.append(tool_result.output)
        else:
            for block in tool_result.output:
                if isinstance(block, TextBlock):
                    parts.append(block.text)
                elif isinstance(block, DataBlock):
                    if isinstance(block.source, Base64Source):
                        d = await self._offload_data_block(block)
                        url = str(d.source.url)
                    else:
                        url = str(block.source.url)
                    parts.append(
                        f"<data url='{url}' name='{block.name}' "
                        f"media_type='{block.source.media_type}'/>",
                    )

        await self._exec(f"mkdir -p {shlex.quote(base)}")
        await self._write(path, "".join(parts).encode("utf-8"))
        return path

    # ── internals: K8S config ──────────────────────────────────

    async def _load_k8s_config(self) -> None:
        """Load Kubernetes configuration.

        When ``kubeconfig`` is set, loads that specific file.
        Otherwise tries in-cluster config first (for when agentscope
        runs inside a Pod), falling back to the default kubeconfig.
        """
        from kubernetes_asyncio import config

        if self.kubeconfig is not None:
            await config.load_kube_config(
                config_file=self.kubeconfig,
            )
        else:
            try:
                config.load_incluster_config()
            except config.ConfigException:
                await config.load_kube_config()

    # ── internals: Pod attach / create ─────────────────────────

    async def _attach_or_create_pod(self) -> None:
        """Reattach to an existing Pod by label, or create one."""
        from kubernetes_asyncio import client
        from kubernetes_asyncio.stream import WsApiClient

        self._api_client = WsApiClient()
        self._core_api = client.CoreV1Api(api_client=self._api_client)

        existing = await self._find_existing_pod()
        if existing is not None:
            self._pod_name = existing
            logger.info(
                "K8SWorkspace: reattaching to pod %r",
                self._pod_name,
            )
            return

        self._pod_name = f"as-ws-{self.workspace_id}"
        labels = {
            LABEL_WORKSPACE: "true",
            LABEL_WORKSPACE_ID: self.workspace_id,
            **self.pod_labels,
        }

        container = client.V1Container(
            name="workspace",
            image=self.image,
            command=["sleep", "infinity"],
            working_dir=POD_WORKDIR,
        )

        if self.env:
            container.env = [
                client.V1EnvVar(name=k, value=v) for k, v in self.env.items()
            ]

        pod_spec = client.V1PodSpec(containers=[container])

        if self.pvc_name:
            pod_spec.volumes = [
                client.V1Volume(
                    name="workspace-data",
                    persistent_volume_claim=(
                        client.V1PersistentVolumeClaimVolumeSource(
                            claim_name=self.pvc_name,
                        )
                    ),
                ),
            ]
            container.volume_mounts = [
                client.V1VolumeMount(
                    name="workspace-data",
                    mount_path=POD_WORKDIR,
                ),
            ]

        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(
                name=self._pod_name,
                namespace=self.namespace,
                labels=labels,
            ),
            spec=pod_spec,
        )

        await self._core_api.create_namespaced_pod(self.namespace, pod)
        logger.info("K8SWorkspace: created pod %r", self._pod_name)

    async def _find_existing_pod(self) -> str | None:
        """Find a Running or Pending Pod with our workspace_id label."""
        label_selector = f"{LABEL_WORKSPACE_ID}={self.workspace_id}"
        pods = await self._core_api.list_namespaced_pod(
            self.namespace,
            label_selector=label_selector,
        )
        for pod in pods.items:
            if pod.status.phase in ("Running", "Pending"):
                return pod.metadata.name
        return None

    async def _wait_until_ready(
        self,
        timeout: float = 120.0,
    ) -> None:
        """Poll until the Pod's workspace container is ready."""
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.5
        while asyncio.get_event_loop().time() < deadline:
            try:
                pod = await self._core_api.read_namespaced_pod(
                    self._pod_name,
                    self.namespace,
                )
                if pod.status.phase == "Running":
                    for cs in pod.status.container_statuses or []:
                        if cs.name == "workspace" and cs.ready:
                            return
            except Exception as e:
                logger.debug(
                    "K8SWorkspace: pod status probe error: %s",
                    e,
                )
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 2.0)
        raise RuntimeError(
            f"Pod {self._pod_name!r} did not become ready "
            f"within {timeout}s",
        )

    # ── internals: bootstrap ────────────────────────────────────

    async def _run_bootstrap(self) -> None:
        """Provision a fresh Pod: uv → venv → agentscope → script."""
        if _is_released_install():
            log_bootstrap_attempt(self.workspace_id, "released")
            install_cmd = render_install_agentscope_cmd_released(
                _agentscope_version(),
            )
        else:
            log_bootstrap_attempt(self.workspace_id, "dev")
            tar_bytes = build_source_tarball()
            await self._write(DEV_SRC_TAR, tar_bytes)
            install_cmd = render_install_agentscope_cmd_dev()

        commands = bootstrap_commands(
            extra_pip=self.extra_pip,
            install_agentscope_cmd=install_cmd,
        )
        for cmd in commands:
            r = await self._exec(cmd, timeout=600.0)
            if not r.ok():
                raise RuntimeError(
                    f"K8SWorkspace bootstrap failed (exit {r.exit_code}) "
                    f"for: {cmd!r}\n"
                    f"stderr: {r.stderr.decode(errors='replace')}\n"
                    f"stdout: {r.stdout.decode(errors='replace')}",
                )

        await self._write(GATEWAY_SCRIPT, _read_gateway_script_bytes())

    # ── internals: gateway lifecycle ────────────────────────────

    async def _restore_or_seed_mcps(self) -> list[MCPClient]:
        try:
            raw = await self._read(POD_MCP_FILE)
        except FileNotFoundError:
            return list(self.default_mcps)
        try:
            data = json.loads(raw.decode("utf-8"))
            return [MCPClient.model_validate(m) for m in data]
        except Exception as e:
            logger.warning(
                "K8SWorkspace: failed to parse %s, falling back to "
                "default_mcps: %s",
                POD_MCP_FILE,
                e,
            )
            return list(self.default_mcps)

    async def _save_mcp_file(self) -> None:
        payload = json.dumps(
            [m.model_dump(mode="json") for m in self._mcps],
            indent=2,
            ensure_ascii=False,
        )
        try:
            await self._exec(
                f"mkdir -p {shlex.quote(POD_WORKDIR)}",
            )
            await self._write(
                POD_MCP_FILE,
                payload.encode("utf-8"),
            )
        except Exception as e:
            logger.warning(
                "K8SWorkspace: failed to save %s: %s",
                POD_MCP_FILE,
                e,
            )

    async def _write_gateway_config(self) -> None:
        cfg = {
            "token": self._gateway_token,
            "servers": [m.model_dump(mode="json") for m in self._mcps],
        }
        await self._exec(f"mkdir -p {shlex.quote(GATEWAY_HOME)}")
        await self._write(
            GATEWAY_CONFIG,
            json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    async def _start_gateway_process(self) -> None:
        cmd = (
            f"nohup {shlex.quote(GATEWAY_VENV_PY)} -u "
            f"{shlex.quote(GATEWAY_SCRIPT)} "
            f"--config {shlex.quote(GATEWAY_CONFIG)} "
            f"--port {self.gateway_port} "
            f"> {shlex.quote(GATEWAY_LOG)} 2>&1 &"
        )
        await self._exec(cmd)

    async def _wait_for_gateway(self, timeout: float = 30.0) -> None:
        assert self._gateway is not None
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if await self._gateway.health():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        try:
            log = await self._read(GATEWAY_LOG)
            tail = log[-2000:].decode(errors="replace")
        except Exception:
            tail = "<no gateway log available>"
        raise RuntimeError(
            f"gateway did not become healthy within {timeout}s. "
            f"Tail of {GATEWAY_LOG}:\n{tail}",
        )

    async def _seed_skills(self) -> None:
        if not self.skill_paths:
            return
        listing = await self._exec(
            f"ls -A {shlex.quote(POD_SKILLS_DIR)} 2>/dev/null || true",
        )
        if listing.ok() and listing.stdout.strip():
            return
        for path in self.skill_paths:
            try:
                await self.add_skill(path)
            except Exception as e:
                logger.warning(
                    "K8SWorkspace: skip skill %r: %s",
                    path,
                    e,
                )

    # ── internals: Pod I/O ──────────────────────────────────────

    async def _exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> _ExecResult:
        """Run ``sh -c <command>`` inside the Pod.

        Wraps the command to capture both the exit code and stderr
        in a structured footer appended to stdout. The K8S exec API
        via ``WsApiClient`` returns combined output as a string;
        the wrapper lets us reconstruct separate stdout/stderr/rc.
        """
        wrapped = (
            f"_as_err=$(mktemp 2>/dev/null || echo /tmp/_as_err_$$) && "
            f'{{ {command}; }} 2>"$_as_err"; _as_rc=$?; '
            f'printf "\\n__K8S_META__\\n%d\\n" "$_as_rc"; '
            f'cat "$_as_err" 2>/dev/null; '
            f'rm -f "$_as_err"'
        )

        async def _run() -> _ExecResult:
            try:
                resp = await self._core_api.connect_get_namespaced_pod_exec(
                    self._pod_name,
                    self.namespace,
                    command=["sh", "-c", wrapped],
                    stdout=True,
                    stderr=True,
                    stdin=False,
                    tty=False,
                )
            except Exception as e:
                return _ExecResult(
                    exit_code=-1,
                    stdout=b"",
                    stderr=str(e).encode("utf-8"),
                )

            raw = resp if isinstance(resp, str) else ""
            idx = raw.find(_META_MARKER)
            if idx == -1:
                return _ExecResult(
                    exit_code=-1,
                    stdout=raw.encode("utf-8"),
                    stderr=b"",
                )

            stdout_text = raw[:idx]
            meta_text = raw[idx + len(_META_MARKER) :]
            meta_lines = meta_text.split("\n", 1)
            try:
                exit_code = int(meta_lines[0])
            except (ValueError, IndexError):
                exit_code = -1
            stderr_text = meta_lines[1] if len(meta_lines) > 1 else ""

            return _ExecResult(
                exit_code=exit_code,
                stdout=stdout_text.encode("utf-8"),
                stderr=stderr_text.encode("utf-8"),
            )

        if timeout is None:
            return await _run()
        try:
            return await asyncio.wait_for(_run(), timeout=timeout)
        except asyncio.TimeoutError:
            return _ExecResult(
                exit_code=-1,
                stdout=b"",
                stderr=b"timed out",
            )

    async def _read(self, path: str) -> bytes:
        """Read a file from the Pod via exec + base64."""
        r = await self._exec(f"base64 < {shlex.quote(path)}")
        if not r.ok():
            raise FileNotFoundError(f"not found in pod: {path}")
        return base64.b64decode(r.stdout)

    async def _write(self, path: str, data: bytes) -> None:
        """Write data to a file inside the Pod via exec + base64.

        For files larger than 64 KB (base64-encoded), the data is
        written in chunks to avoid command-line length limits.
        """
        parent = posixpath.dirname(path) or "/"
        await self._exec(f"mkdir -p {shlex.quote(parent)}")

        b64 = base64.b64encode(data).decode("ascii")
        chunk_size = 65536  # 64 KB chunks, aligned to base64 (divisible by 4)

        if len(b64) <= chunk_size:
            await self._exec(
                f"printf '%s' {shlex.quote(b64)} "
                f"| base64 -d > {shlex.quote(path)}",
            )
        else:
            await self._exec(f": > {shlex.quote(path)}")
            for i in range(0, len(b64), chunk_size):
                chunk = b64[i : i + chunk_size]
                await self._exec(
                    f"printf '%s' {shlex.quote(chunk)} "
                    f"| base64 -d >> {shlex.quote(path)}",
                )

    # ── internals: data offload ────────────────────────────────

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        if not isinstance(block.source, Base64Source):
            return block
        h = hashlib.sha256(block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        path = f"{POD_DATA_DIR}/{h}{ext}"
        await self._exec(f"mkdir -p {shlex.quote(POD_DATA_DIR)}")
        await self._write(path, base64.b64decode(block.source.data))
        return DataBlock(
            id=block.id,
            name=block.name,
            source=URLSource(
                url=AnyUrl(f"file://{path}"),
                media_type=block.source.media_type,
            ),
        )
