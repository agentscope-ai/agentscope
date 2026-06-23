# -*- coding: utf-8 -*-
"""K8sWorkspace — sandboxed workspace backed by a Kubernetes Pod.

Architecture
------------

Mirrors :class:`agentscope.workspace.DockerWorkspace` but swaps the
Docker engine for the Kubernetes API (``kubernetes_asyncio``):

* **Lifecycle.** ``initialize()`` looks up an existing Pod by label,
  reuses it if Running, deletes-and-recreates if Failed/Unknown, or
  creates a new one. PVCs survive Pod deletion for data persistence.
  ``close()`` deletes the Pod but keeps the PVC.
* **Persistence.** A PVC (``as-ws-{workspace_id}``) mounted at
  ``/workspace`` provides cross-Pod-restart persistence. Skills,
  ``.mcp``, sessions, and data survive restarts.
* **Bootstrap.** First-time provisioning installs system deps +
  uv + gateway venv + agentscope and uploads the gateway script.
  Detected by ``GATEWAY_SCRIPT`` file existence (idempotent).
* **MCP gateway.** Identical to Docker/E2B: a FastAPI process inside
  the Pod, host talks to it via ``GatewayClient`` over Pod IP
  (in-cluster) or port-forward (local dev).
* **Network modes.** ``pod-ip`` (default, production) directly
  connects to the Pod IP; ``port-forward`` (dev) establishes a
  local WebSocket tunnel.
"""

import asyncio
import base64
import hashlib
import json
import mimetypes
import os
import posixpath
import shlex
import uuid
from copy import deepcopy
from typing import Any

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
from .._utils import (
    _agentscope_version,
    _is_released_install,
    _read_gateway_script_bytes,
    _read_glob_helper_bytes,
)
from ._k8s_backend import K8sBackend
from ._k8s_bootstrap import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
    DEV_SRC_TAR,
    GATEWAY_CONFIG,
    GATEWAY_HOME,
    GATEWAY_LOG,
    GATEWAY_SCRIPT,
    GATEWAY_VENV_PY,
    GLOB_HELPER_SCRIPT,
    POD_DATA_DIR,
    POD_MCP_FILE,
    POD_SESSIONS_DIR,
    POD_SKILLS_DIR,
    POD_WORKDIR,
    _k8s_safe_name,
    bootstrap_commands,
    log_bootstrap_attempt,
    render_install_agentscope_cmd_dev,
    render_install_agentscope_cmd_released,
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


# ── the workspace ──────────────────────────────────────────────────


class K8sWorkspace(WorkspaceBase):
    """Workspace backed by a Kubernetes Pod with PVC persistence.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.
    """

    def __init__(
        self,
        *,
        workspace_id: str | None = None,
        # ── K8s connection ──
        kubeconfig: str | None = None,
        namespace: str = "agentscope",
        # ── Pod construction ──
        image: str = DEFAULT_IMAGE,
        image_pull_policy: str = "IfNotPresent",
        image_pull_secrets: list[str] | None = None,
        resources: dict[str, Any] | None = None,
        node_selector: dict[str, str] | None = None,
        tolerations: list[dict[str, Any]] | None = None,
        service_account: str | None = None,
        # ── Gateway network ──
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        network_mode: str = "pod-ip",
        extra_pip: list[str] | None = None,
        # ── Persistence ──
        storage_class: str | None = None,
        storage_size: str = "1Gi",
        # ── Environment ──
        env: dict[str, str] | None = None,
        instructions: str = _DEFAULT_INSTRUCTIONS,
        # ── Seed ──
        default_mcps: list[MCPClient] | None = None,
        skill_paths: list[str] | None = None,
    ) -> None:
        """Construct a :class:`K8sWorkspace`.

        The Pod is *not* started here; call :meth:`initialize`
        (or use the workspace as an ``async`` context manager).

        Args:
            workspace_id (`str | None`, optional):
                Stable identifier. ``None`` generates a fresh UUID.
            kubeconfig (`str | None`, optional):
                Path to kubeconfig file. ``None`` uses in-cluster
                config.
            namespace (`str`, defaults to ``"agentscope"``):
                K8s namespace for the Pod and PVC.
            image (`str`, defaults to ``"python:3.11-slim"``):
                Container image.
            image_pull_policy (`str`, defaults to ``"IfNotPresent"``):
                K8s imagePullPolicy.
            image_pull_secrets (`list[str] | None`, optional):
                Names of K8s image pull secrets.
            resources (`dict[str, Any] | None`, optional):
                K8s ResourceRequirements dict.
            node_selector (`dict[str, str] | None`, optional):
                K8s nodeSelector.
            tolerations (`list[dict[str, Any]] | None`, optional):
                K8s tolerations list.
            service_account (`str | None`, optional):
                K8s serviceAccountName.
            gateway_port (`int`, defaults to `5600`):
                Port the gateway listens on inside the Pod.
            network_mode (`str`, defaults to ``"pod-ip"``):
                ``"pod-ip"`` for direct Pod IP access (production),
                ``"port-forward"`` for local dev.
            extra_pip (`list[str] | None`, optional):
                Extra packages for the gateway venv.
            storage_class (`str | None`, optional):
                K8s StorageClass name. ``None`` uses cluster default.
            storage_size (`str`, defaults to ``"1Gi"``):
                PVC size.
            env (`dict[str, str] | None`, optional):
                Environment variables for the container.
            instructions (`str`):
                System-prompt fragment template.
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs seeded on first init.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded on first init.
        """
        super().__init__(workspace_id=workspace_id)

        # ── serializable config ─────────────────────────────────
        self.workdir = POD_WORKDIR
        self._kubeconfig = kubeconfig
        self._namespace = namespace
        self._image = image
        self._image_pull_policy = image_pull_policy
        self._image_pull_secrets = list(image_pull_secrets or [])
        self._resources = resources
        self._node_selector = node_selector
        self._tolerations = tolerations
        self._service_account = service_account
        self._gateway_port = gateway_port
        self._network_mode = network_mode
        self.extra_pip: list[str] = list(extra_pip or [])
        self._storage_class = storage_class
        self._storage_size = storage_size
        self.env: dict[str, str] = dict(env or {})
        self.instructions = instructions

        # ── seed-only ───────────────────────────────────────────
        self.default_mcps: list[MCPClient] = list(default_mcps or [])
        self.skill_paths: list[str] = list(skill_paths or [])

        # ── runtime state ───────────────────────────────────────
        self.is_alive: bool = False
        self._api_client: Any = None
        self._v1: Any = None  # CoreV1Api
        self._pod_name: str = ""
        self._backend: K8sBackend | None = None
        self._gateway: GatewayClient | None = None
        self._gateway_token: str = ""
        self._mcps: list[MCPClient] = []
        self._gateway_clients: dict[str, GatewayMCPClient] = {}
        self._mcp_lock = asyncio.Lock()
        self._skill_lock = asyncio.Lock()
        self._port_forward_proc: Any = None

    # ── lifecycle ───────────────────────────────────────────────

    async def initialize(self) -> None:
        """Create or reattach to the Pod, bootstrap, start gateway.

        Idempotent — a no-op when already alive.
        """
        if self.is_alive:
            return

        from kubernetes_asyncio import client as k8s_client
        from kubernetes_asyncio import config as k8s_config

        if self._kubeconfig:
            await k8s_config.load_kube_config(
                config_file=self._kubeconfig,
            )
        else:
            try:
                k8s_config.load_incluster_config()
            except k8s_config.ConfigException:
                await k8s_config.load_kube_config()

        self._api_client = k8s_client.ApiClient()
        self._v1 = k8s_client.CoreV1Api(self._api_client)

        self._pod_name = _k8s_safe_name(self.workspace_id)

        await self._ensure_namespace()
        await self._ensure_pvc()
        await self._ensure_pod()
        await self._wait_pod_running()

        self._backend = K8sBackend(
            api_client=self._api_client,
            namespace=self._namespace,
            pod_name=self._pod_name,
            container_name="workspace",
            workdir=POD_WORKDIR,
        )

        if not await self._backend.file_exists(GATEWAY_SCRIPT):
            await self._backend.exec_shell(
                ["mkdir", "-p", POD_WORKDIR],
                cwd="/",
            )
            await self._run_bootstrap()

        # Kill stale gateway from a prior Pod restart
        await self._backend.exec_shell(
            ["sh", "-c", "pkill -f _mcp_gateway_app.py || true"],
        )

        self._mcps = await self._restore_or_seed_mcps()

        self._gateway_token = uuid.uuid4().hex

        await self._write_gateway_config()
        await self._start_gateway_process()

        gateway_url = await self._resolve_gateway_url()
        self._gateway = GatewayClient(
            base_url=gateway_url,
            token=self._gateway_token,
            timeout=30.0,
        )
        await self._wait_for_gateway()

        self._gateway_clients = {
            c.name: c for c in await self._gateway.list_mcps()
        }

        await self._save_mcp_file()
        await self._seed_skills()

        self.is_alive = True

    async def reset(self) -> None:
        """Return the workspace to an empty state.

        Mirrors :meth:`DockerWorkspace.reset`.
        """
        if self._backend is None:
            raise RuntimeError(
                "K8sWorkspace is not initialized: its Pod backend "
                "is unavailable. Use 'async with workspace:' or call "
                "'await workspace.initialize()' before 'reset()'.",
            )

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

            for path in (
                POD_SESSIONS_DIR,
                POD_DATA_DIR,
                POD_SKILLS_DIR,
            ):
                await self._backend.delete_path(path)

            await self._save_mcp_file()

    async def close(self, *, delete_pvc: bool = False) -> None:
        """Delete the Pod, optionally delete the PVC, release resources.

        By default the PVC is kept so that a subsequent
        :meth:`initialize` can reattach to the existing data.  Pass
        ``delete_pvc=True`` when the workspace data is no longer
        needed to avoid storage resource leaks.

        Errors during teardown are swallowed so ``close`` is always
        safe to call from ``__aexit__``.

        Args:
            delete_pvc (`bool`, defaults to ``False``):
                If ``True``, also delete the PVC after removing the
                Pod.  The underlying PersistentVolume will be reclaimed
                according to its ``reclaimPolicy``.
        """
        if self._gateway is not None:
            try:
                await self._gateway.aclose()
            except Exception:
                pass
            self._gateway = None
        self._gateway_clients.clear()

        if self._port_forward_proc is not None:
            try:
                self._port_forward_proc.terminate()
                await self._port_forward_proc.wait()
            except Exception:
                pass
            self._port_forward_proc = None

        if self._v1 is not None and self._pod_name:
            try:
                await self._v1.delete_namespaced_pod(
                    self._pod_name,
                    self._namespace,
                )
            except Exception as e:
                logger.warning("K8sWorkspace: Pod delete failed: %s", e)

            if delete_pvc:
                try:
                    await self._v1.delete_namespaced_persistent_volume_claim(
                        self._pod_name,
                        self._namespace,
                    )
                except Exception as e:
                    logger.warning(
                        "K8sWorkspace: PVC delete failed: %s",
                        e,
                    )

        self._backend = None

        if self._api_client is not None:
            try:
                await self._api_client.close()
            except Exception:
                pass
            self._api_client = None
            self._v1 = None

        self.is_alive = False

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace."""
        return self.instructions.format(workdir=POD_WORKDIR)

    # ── tool / MCP / skill discovery ────────────────────────────

    async def list_tools(self) -> list[ToolBase]:
        """Built-in tools backed by the K8s Pod.

        Raises:
            `RuntimeError`:
                If the workspace has not been initialized yet.
        """
        if self._backend is None:
            raise RuntimeError(
                "K8sWorkspace is not initialized: its Pod backend "
                "is unavailable. Use 'async with workspace:' or call "
                "'await workspace.initialize()' before 'list_tools()'.",
            )

        from ...tool._builtin import Bash, Edit, Glob, Grep, Read, Write

        return [
            Bash(cwd=POD_WORKDIR, backend=self._backend),
            Edit(backend=self._backend),
            Glob(
                backend=self._backend,
                glob_helper_path=GLOB_HELPER_SCRIPT,
            ),
            Grep(backend=self._backend),
            Read(backend=self._backend),
            Write(backend=self._backend),
        ]

    async def list_mcps(self) -> list[MCPClient]:
        """Return one :class:`GatewayMCPClient` per registered MCP."""
        return list(self._gateway_clients.values())

    async def list_skills(self) -> list[Skill]:
        """Enumerate skills by scanning ``skills/`` inside the Pod."""
        import frontmatter as fm

        result = await self._backend.exec_shell(
            [
                "sh",
                "-c",
                f"find {POD_SKILLS_DIR} -name SKILL.md "
                f"2>/dev/null || true",
            ],
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
                raw = await self._backend.read_file(md_path)
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
        """Register a new MCP server on the in-Pod gateway."""
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
        """Unregister an MCP server by name."""
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
        """Upload a local skill directory into ``skills/``."""
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.isfile(skill_md):
            raise ValueError(
                f"Invalid skill at {skill_path!r}: SKILL.md not found",
            )

        async with self._skill_lock:
            await self._backend.exec_shell(
                ["mkdir", "-p", POD_SKILLS_DIR],
            )
            dir_name = os.path.basename(os.path.abspath(skill_path))

            check = await self._backend.exec_shell(
                ["test", "-e", POD_SKILLS_DIR + "/" + dir_name],
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
                    await self._backend.write_file(remote, data)

            logger.info(
                "K8sWorkspace: added skill %r at %s/%s",
                dir_name,
                POD_SKILLS_DIR,
                dir_name,
            )

    async def remove_skill(self, name: str) -> None:
        """Delete a skill directory by its agent-facing name."""
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
        await self._backend.delete_path(target_dir)

    # ── offload ─────────────────────────────────────────────────

    async def offload_context(
        self,
        session_id: str,
        msgs: list[Msg],
    ) -> str:
        """Persist messages as JSONL inside the Pod."""
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

        await self._backend.exec_shell(["mkdir", "-p", base])
        existing = b""
        try:
            existing = await self._backend.read_file(path)
        except FileNotFoundError:
            pass
        await self._backend.write_file(
            path,
            existing + ("\n".join(lines) + "\n").encode("utf-8"),
        )
        return path

    async def offload_tool_result(
        self,
        session_id: str,
        tool_result: ToolResultBlock,
    ) -> str:
        """Persist a single tool result as a flat text file."""
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

        await self._backend.exec_shell(["mkdir", "-p", base])
        await self._backend.write_file(
            path,
            "".join(parts).encode("utf-8"),
        )
        return path

    # ── internals: K8s resource management ─────────────────────

    async def _ensure_namespace(self) -> None:
        """Create the namespace if it doesn't exist."""
        from kubernetes_asyncio.client.rest import ApiException

        try:
            await self._v1.read_namespace(self._namespace)
        except ApiException as e:
            if e.status == 404:
                from kubernetes_asyncio import client as k8s_client

                ns = k8s_client.V1Namespace(
                    metadata=k8s_client.V1ObjectMeta(
                        name=self._namespace,
                    ),
                )
                await self._v1.create_namespace(ns)
            else:
                raise

    async def _ensure_pvc(self) -> None:
        """Create or reuse the PVC for workspace persistence."""
        from kubernetes_asyncio.client.rest import ApiException

        pvc_name = self._pod_name
        try:
            pvc = await self._v1.read_namespaced_persistent_volume_claim(
                pvc_name,
                self._namespace,
            )
            phase = pvc.status.phase if pvc.status else None
            if phase == "Terminating":
                logger.info(
                    "K8sWorkspace: PVC %r is Terminating, waiting...",
                    pvc_name,
                )
                await self._wait_pvc_deleted(pvc_name)
                await self._create_pvc(pvc_name)
            # Bound or Pending — reuse
        except ApiException as e:
            if e.status == 404:
                await self._create_pvc(pvc_name)
            else:
                raise

    async def _create_pvc(self, pvc_name: str) -> None:
        """Create a new PVC."""
        from kubernetes_asyncio import client as k8s_client

        spec_kwargs: dict[str, Any] = {
            "access_modes": ["ReadWriteOnce"],
            "resources": k8s_client.V1VolumeResourceRequirements(
                requests={"storage": self._storage_size},
            ),
        }
        if self._storage_class is not None:
            spec_kwargs["storage_class_name"] = self._storage_class

        pvc = k8s_client.V1PersistentVolumeClaim(
            metadata=k8s_client.V1ObjectMeta(
                name=pvc_name,
                namespace=self._namespace,
                labels={
                    "app.kubernetes.io/managed-by": "agentscope",
                    "agentscope.workspace.id": self.workspace_id,
                },
            ),
            spec=k8s_client.V1PersistentVolumeClaimSpec(**spec_kwargs),
        )
        await self._v1.create_namespaced_persistent_volume_claim(
            self._namespace,
            pvc,
        )

    async def _wait_pvc_deleted(
        self,
        pvc_name: str,
        timeout: float = 60.0,
    ) -> None:
        """Poll until the PVC is fully deleted."""
        from kubernetes_asyncio.client.rest import ApiException

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                await self._v1.read_namespaced_persistent_volume_claim(
                    pvc_name,
                    self._namespace,
                )
            except ApiException as e:
                if e.status == 404:
                    return
                raise
            await asyncio.sleep(1.0)
        raise RuntimeError(
            f"PVC {pvc_name!r} did not finish deleting within {timeout}s",
        )

    async def _ensure_pod(self) -> None:
        """Create or reuse the workspace Pod."""
        from kubernetes_asyncio.client.rest import ApiException

        try:
            pod = await self._v1.read_namespaced_pod(
                self._pod_name,
                self._namespace,
            )
            phase = pod.status.phase if pod.status else None
            if phase == "Running":
                return
            # Failed, Unknown, Succeeded, Pending — delete and recreate
            logger.info(
                "K8sWorkspace: Pod %r is %s, deleting and recreating",
                self._pod_name,
                phase,
            )
            try:
                await self._v1.delete_namespaced_pod(
                    self._pod_name,
                    self._namespace,
                )
            except ApiException:
                pass
            await self._wait_pod_deleted()
            await self._create_pod()
        except ApiException as e:
            if e.status == 404:
                await self._create_pod()
            else:
                raise

    async def _create_pod(self) -> None:
        """Create the workspace Pod."""
        from kubernetes_asyncio import client as k8s_client

        container_env = None
        if self.env:
            container_env = [
                k8s_client.V1EnvVar(name=k, value=v)
                for k, v in self.env.items()
            ]

        container = k8s_client.V1Container(
            name="workspace",
            image=self._image,
            image_pull_policy=self._image_pull_policy,
            command=["sleep", "infinity"],
            working_dir=POD_WORKDIR,
            ports=[
                k8s_client.V1ContainerPort(
                    container_port=self._gateway_port,
                ),
            ],
            resources=(
                k8s_client.V1ResourceRequirements(**self._resources)
                if self._resources
                else None
            ),
            volume_mounts=[
                k8s_client.V1VolumeMount(
                    name="workspace-data",
                    mount_path=POD_WORKDIR,
                ),
            ],
            env=container_env,
        )

        volumes = [
            k8s_client.V1Volume(
                name="workspace-data",
                persistent_volume_claim=(
                    k8s_client.V1PersistentVolumeClaimVolumeSource(
                        claim_name=self._pod_name,
                    )
                ),
            ),
        ]

        spec_kwargs: dict[str, Any] = {
            "restart_policy": "OnFailure",
            "containers": [container],
            "volumes": volumes,
        }
        if self._node_selector:
            spec_kwargs["node_selector"] = self._node_selector
        if self._tolerations:
            spec_kwargs["tolerations"] = [
                k8s_client.V1Toleration(**t) for t in self._tolerations
            ]
        if self._service_account:
            spec_kwargs["service_account_name"] = self._service_account
        if self._image_pull_secrets:
            spec_kwargs["image_pull_secrets"] = [
                k8s_client.V1LocalObjectReference(name=s)
                for s in self._image_pull_secrets
            ]

        pod = k8s_client.V1Pod(
            metadata=k8s_client.V1ObjectMeta(
                name=self._pod_name,
                namespace=self._namespace,
                labels={
                    "app.kubernetes.io/managed-by": "agentscope",
                    "agentscope.workspace": "true",
                    "agentscope.workspace.id": self.workspace_id,
                },
            ),
            spec=k8s_client.V1PodSpec(**spec_kwargs),
        )
        await self._v1.create_namespaced_pod(self._namespace, pod)

    async def _wait_pod_running(self, timeout: float = 120.0) -> None:
        """Poll until the Pod phase is Running.

        Also inspects container statuses during the Pending phase to
        detect unrecoverable conditions (``ImagePullBackOff``,
        ``ErrImagePull``, ``InvalidImageName``) early instead of
        waiting for the full timeout.
        """
        _TERMINAL_WAITING_REASONS = frozenset(
            {
                "ImagePullBackOff",
                "ErrImagePull",
                "InvalidImageName",
                "CrashLoopBackOff",
            },
        )
        _UNSCHEDULABLE_TYPES = frozenset(
            {"PodScheduled"},
        )

        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.5
        while asyncio.get_event_loop().time() < deadline:
            pod = await self._v1.read_namespaced_pod(
                self._pod_name,
                self._namespace,
            )
            phase = pod.status.phase if pod.status else None
            if phase == "Running":
                return
            if phase in ("Failed", "Unknown"):
                raise RuntimeError(
                    f"Pod {self._pod_name!r} entered {phase} state",
                )

            # Check for unrecoverable Pending conditions
            if phase == "Pending" and pod.status:
                # Container-level waiting reasons
                for cs in pod.status.container_statuses or []:
                    if cs.state and cs.state.waiting:
                        reason = cs.state.waiting.reason or ""
                        if reason in _TERMINAL_WAITING_REASONS:
                            msg = cs.state.waiting.message or reason
                            raise RuntimeError(
                                f"Pod {self._pod_name!r} container "
                                f"is stuck: {msg}",
                            )
                # Pod-level Unschedulable condition
                for cond in pod.status.conditions or []:
                    if (
                        cond.type in _UNSCHEDULABLE_TYPES
                        and cond.status == "False"
                        and cond.reason == "Unschedulable"
                    ):
                        raise RuntimeError(
                            f"Pod {self._pod_name!r} is "
                            f"unschedulable: {cond.message}",
                        )

            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 3.0)
        raise RuntimeError(
            f"Pod {self._pod_name!r} did not become Running "
            f"within {timeout}s",
        )

    async def _wait_pod_deleted(self, timeout: float = 30.0) -> None:
        """Poll until the Pod is gone."""
        from kubernetes_asyncio.client.rest import ApiException

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                await self._v1.read_namespaced_pod(
                    self._pod_name,
                    self._namespace,
                )
            except ApiException as e:
                if e.status == 404:
                    return
                raise
            await asyncio.sleep(1.0)
        raise RuntimeError(
            f"Pod {self._pod_name!r} did not finish deleting "
            f"within {timeout}s",
        )

    # ── internals: bootstrap ────────────────────────────────────

    async def _run_bootstrap(self) -> None:
        """Provision a fresh Pod: sys deps -> uv -> venv -> agentscope."""
        from .._e2b._bootstrap import build_source_tarball

        if _is_released_install():
            log_bootstrap_attempt(self.workspace_id, "released")
            install_cmd = render_install_agentscope_cmd_released(
                _agentscope_version(),
            )
        else:
            log_bootstrap_attempt(self.workspace_id, "dev")
            tar_bytes = build_source_tarball()
            await self._backend.write_file(DEV_SRC_TAR, tar_bytes)
            install_cmd = render_install_agentscope_cmd_dev()

        commands = bootstrap_commands(
            extra_pip=self.extra_pip,
            install_agentscope_cmd=install_cmd,
        )
        for cmd in commands:
            r = await self._backend.exec_shell(
                ["sh", "-c", cmd],
                timeout=600.0,
            )
            if not r.ok():
                raise RuntimeError(
                    f"K8sWorkspace bootstrap failed (exit {r.exit_code}) "
                    f"for: {cmd!r}\n"
                    f"stderr: {r.stderr.decode(errors='replace')}\n"
                    f"stdout: {r.stdout.decode(errors='replace')}",
                )

        await self._backend.write_file(
            GLOB_HELPER_SCRIPT,
            _read_glob_helper_bytes(),
        )
        await self._backend.write_file(
            GATEWAY_SCRIPT,
            _read_gateway_script_bytes(),
        )

    # ── internals: gateway lifecycle ────────────────────────────

    async def _restore_or_seed_mcps(self) -> list[MCPClient]:
        """Read ``.mcp`` from Pod, or fall back to ``default_mcps``."""
        try:
            raw = await self._backend.read_file(POD_MCP_FILE)
        except FileNotFoundError:
            return list(self.default_mcps)
        try:
            data = json.loads(raw.decode("utf-8"))
            return [MCPClient.model_validate(m) for m in data]
        except Exception as e:
            logger.warning(
                "K8sWorkspace: failed to parse %s, falling back to "
                "default_mcps: %s",
                POD_MCP_FILE,
                e,
            )
            return list(self.default_mcps)

    async def _save_mcp_file(self) -> None:
        """Persist ``self._mcps`` to ``.mcp`` inside the Pod."""
        payload = json.dumps(
            [m.model_dump(mode="json") for m in self._mcps],
            indent=2,
            ensure_ascii=False,
        )
        try:
            await self._backend.exec_shell(
                ["mkdir", "-p", POD_WORKDIR],
            )
            await self._backend.write_file(
                POD_MCP_FILE,
                payload.encode("utf-8"),
            )
        except Exception as e:
            logger.warning(
                "K8sWorkspace: failed to save %s: %s",
                POD_MCP_FILE,
                e,
            )

    async def _write_gateway_config(self) -> None:
        """Drop the gateway config JSON into the Pod."""
        cfg = {
            "token": self._gateway_token,
            "servers": [m.model_dump(mode="json") for m in self._mcps],
        }
        await self._backend.exec_shell(
            ["mkdir", "-p", GATEWAY_HOME],
        )
        await self._backend.write_file(
            GATEWAY_CONFIG,
            json.dumps(cfg, indent=2, ensure_ascii=False).encode("utf-8"),
        )

    async def _start_gateway_process(self) -> None:
        """Launch the gateway inside the Pod as a detached process."""
        cmd = (
            f"nohup {shlex.quote(GATEWAY_VENV_PY)} -u "
            f"{shlex.quote(GATEWAY_SCRIPT)} "
            f"--config {shlex.quote(GATEWAY_CONFIG)} "
            f"--port {self._gateway_port} "
            f"> {shlex.quote(GATEWAY_LOG)} 2>&1 &"
        )
        await self._backend.exec_shell(["sh", "-c", cmd])

    async def _resolve_gateway_url(self) -> str:
        """Determine the gateway URL based on ``network_mode``."""
        if self._network_mode == "port-forward":
            return await self._setup_port_forward()

        # Default: pod-ip
        pod = await self._v1.read_namespaced_pod(
            self._pod_name,
            self._namespace,
        )
        pod_ip = pod.status.pod_ip if pod.status else None
        if not pod_ip:
            raise RuntimeError(
                f"Pod {self._pod_name!r} has no IP assigned",
            )
        return f"http://{pod_ip}:{self._gateway_port}"

    async def _setup_port_forward(self) -> str:
        """Establish a kubectl port-forward subprocess.

        Uses ``asyncio.create_subprocess_exec`` so the event loop is
        not blocked. Checks that the process is still alive after a
        brief settle period — if kubectl fails immediately (e.g.
        invalid kubeconfig, Pod not found), we surface the error
        early instead of waiting for the gateway health timeout.

        Returns the local ``http://127.0.0.1:<port>`` URL.
        """
        local_port = self._gateway_port + hash(self.workspace_id) % 10000
        local_port = max(1024, min(65535, local_port))

        self._port_forward_proc = await asyncio.create_subprocess_exec(
            "kubectl",
            "port-forward",
            f"pod/{self._pod_name}",
            f"{local_port}:{self._gateway_port}",
            "-n",
            self._namespace,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        # Give port-forward time to establish
        await asyncio.sleep(2.0)

        if self._port_forward_proc.returncode is not None:
            stderr = b""
            if self._port_forward_proc.stderr:
                stderr = await self._port_forward_proc.stderr.read()
            raise RuntimeError(
                f"kubectl port-forward exited immediately "
                f"(code {self._port_forward_proc.returncode}): "
                f"{stderr.decode(errors='replace')[:500]}",
            )

        return f"http://127.0.0.1:{local_port}"

    async def _wait_for_gateway(self, timeout: float = 30.0) -> None:
        """Block until the gateway answers ``/health`` with 200."""
        assert self._gateway is not None
        deadline = asyncio.get_event_loop().time() + timeout
        delay = 0.1
        while asyncio.get_event_loop().time() < deadline:
            if await self._gateway.health():
                return
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
        try:
            log = await self._backend.read_file(GATEWAY_LOG)
            tail = log[-2000:].decode(errors="replace")
        except Exception:
            tail = "<no gateway log available>"
        raise RuntimeError(
            f"gateway did not become healthy within {timeout}s. "
            f"Tail of {GATEWAY_LOG}:\n{tail}",
        )

    async def _seed_skills(self) -> None:
        """Copy ``self.skill_paths`` into ``skills/`` once."""
        if not self.skill_paths:
            return
        listing = await self._backend.exec_shell(
            [
                "sh",
                "-c",
                f"ls -A {shlex.quote(POD_SKILLS_DIR)} " f"2>/dev/null || true",
            ],
        )
        if listing.ok() and listing.stdout.strip():
            return
        for path in self.skill_paths:
            try:
                await self.add_skill(path)
            except Exception as e:
                logger.warning(
                    "K8sWorkspace: skip skill %r: %s",
                    path,
                    e,
                )

    # ── internals: data offload ────────────────────────────────

    async def _offload_data_block(self, block: DataBlock) -> DataBlock:
        """Persist a base64 :class:`DataBlock` under ``data/``."""
        if not isinstance(block.source, Base64Source):
            return block
        h = hashlib.sha256(block.source.data.encode()).hexdigest()
        ext = mimetypes.guess_extension(block.source.media_type) or ".bin"
        path = f"{POD_DATA_DIR}/{h}{ext}"
        await self._backend.exec_shell(
            ["mkdir", "-p", POD_DATA_DIR],
        )
        await self._backend.write_file(
            path,
            base64.b64decode(block.source.data),
        )
        return DataBlock(
            id=block.id,
            name=block.name,
            source=URLSource(
                url=AnyUrl(f"file://{path}"),
                media_type=block.source.media_type,
            ),
        )
