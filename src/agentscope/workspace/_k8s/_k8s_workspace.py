# -*- coding: utf-8 -*-
"""K8sWorkspace — sandboxed workspace backed by a Kubernetes Pod.

Architecture
------------

Mirrors :class:`agentscope.workspace.DockerWorkspace` but swaps the
Docker engine for the Kubernetes API (``kubernetes_asyncio``):

* **Lifecycle.** ``initialize()`` looks up an existing Pod by label,
  reuses it if Running, deletes-and-recreates if Failed/Unknown, or
  creates a new one. PVCs survive Pod deletion for data persistence.
  ``close()`` deletes the Pod but keeps the PVC (unless
  ``delete_pvc_on_close=True`` was passed at construction).
* **Persistence.** A PVC (``as-ws-{workspace_id}``) mounted at
  ``/workspace`` provides cross-Pod-restart persistence. Skills,
  ``.mcp``, sessions, and data survive restarts.
* **Bootstrap.** First-time provisioning installs system deps +
  uv + gateway venv + agentscope and uploads the gateway script.
  The probe + install loop lives on :class:`SandboxedWorkspaceBase`;
  this subclass only supplies Pod-specific shell commands via
  :meth:`_bootstrap_commands`.
* **MCP gateway.** Identical to Docker/E2B: a FastAPI process inside
  the Pod, host talks to it via :class:`GatewayClient` through the
  gateway shim (``exec_shell``-based transport, no host-to-Pod
  network required).
"""

import asyncio
import shlex
from typing import Any

from ..._logging import logger
from ...mcp import MCPClient
from .._sandboxed_base import SandboxedWorkspaceBase
from .._utils import _GATEWAY_BASE_REQUIREMENTS
from ._k8s_backend import K8sBackend
from ._constants import (
    DEFAULT_GATEWAY_PORT,
    DEFAULT_IMAGE,
    GATEWAY_HOME,
    POD_WORKDIR,
    SYSTEM_DEPS,
    _k8s_safe_name,
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
</workspace>"""


# ── the workspace ──────────────────────────────────────────────────


class K8sWorkspace(SandboxedWorkspaceBase):
    """Workspace backed by a Kubernetes Pod with PVC persistence.

    ``default_mcps`` and ``skill_paths`` are seed-time inputs and are
    not retained as instance state past :meth:`initialize`.
    """

    _gateway_home = GATEWAY_HOME

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
        # ── Gateway ──
        gateway_port: int = DEFAULT_GATEWAY_PORT,
        extra_pip: list[str] | None = None,
        # ── Persistence ──
        storage_class: str | None = None,
        storage_size: str = "1Gi",
        delete_pvc_on_close: bool = False,
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
            extra_pip (`list[str] | None`, optional):
                Extra packages for the gateway venv.
            storage_class (`str | None`, optional):
                K8s StorageClass name. ``None`` uses cluster default.
            storage_size (`str`, defaults to ``"1Gi"``):
                PVC size.
            delete_pvc_on_close (`bool`, defaults to ``False``):
                When ``True``, :meth:`close` also deletes the PVC.
            env (`dict[str, str] | None`, optional):
                Environment variables for the container.
            instructions (`str`):
                System-prompt fragment template.
            default_mcps (`list[MCPClient] | None`, optional):
                MCPs seeded on first init.
            skill_paths (`list[str] | None`, optional):
                Skill directories seeded on first init.
        """
        super().__init__(
            workspace_id=workspace_id,
            default_mcps=default_mcps,
            skill_paths=skill_paths,
        )

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
        self.gateway_port = gateway_port
        self.extra_pip: list[str] = list(extra_pip or [])
        self._storage_class = storage_class
        self._storage_size = storage_size
        self._delete_pvc_on_close = delete_pvc_on_close
        self.env: dict[str, str] = dict(env or {})
        self.instructions = instructions

        # ── runtime state (K8s-only) ────────────────────────────
        self._api_client: Any = None
        self._v1: Any = None  # CoreV1Api
        self._pod_name: str = ""

    # ── lifecycle hooks ─────────────────────────────────────────

    async def _provision_backend(self) -> None:
        """Create or reattach to the K8s Pod, bootstrap if needed."""
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

    async def _teardown_backend(self) -> None:
        """Delete the Pod (and optionally the PVC), release the API
        client. Errors are swallowed so teardown is always safe.
        """
        if self._v1 is not None and self._pod_name:
            try:
                await self._v1.delete_namespaced_pod(
                    self._pod_name,
                    self._namespace,
                )
            except Exception as e:
                logger.warning("K8sWorkspace: Pod delete failed: %s", e)

            if self._delete_pvc_on_close:
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

        if self._api_client is not None:
            try:
                await self._api_client.close()
            except Exception:
                pass
            self._api_client = None
            self._v1 = None

    # ── instructions ────────────────────────────────────────────

    async def get_instructions(self) -> str:
        """Return the system-prompt fragment for this workspace."""
        return self.instructions.format(workdir=POD_WORKDIR)

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
        """Create or reuse the PVC for workspace persistence.

        Deletion-in-progress is detected via
        ``metadata.deletion_timestamp`` (K8s sets this field when a
        ``DELETE`` has been accepted but finalizers have not yet
        completed).  ``status.phase`` does not have a
        ``"Terminating"`` value in upstream Kubernetes.
        """
        from kubernetes_asyncio.client.rest import ApiException

        pvc_name = self._pod_name
        try:
            pvc = await self._v1.read_namespaced_persistent_volume_claim(
                pvc_name,
                self._namespace,
            )
            if pvc.metadata and pvc.metadata.deletion_timestamp is not None:
                logger.info(
                    "K8sWorkspace: PVC %r is being deleted, waiting...",
                    pvc_name,
                )
                await self._wait_pvc_deleted(pvc_name)
                await self._create_pvc(pvc_name)
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
            f"PVC {pvc_name!r} did not finish deleting " f"within {timeout}s",
        )

    async def _ensure_pod(self) -> None:
        """Create or reuse the workspace Pod.

        Only terminal phases (``Failed``, ``Unknown``, ``Succeeded``)
        trigger a delete-and-recreate cycle.  ``Pending`` Pods are left
        for :meth:`_wait_pod_running` which inspects container statuses
        for early failure detection.

        A Pod whose ``metadata.deletion_timestamp`` is set is already
        being deleted (by a previous ``close()`` or an external actor)
        — attaching to it would race the terminator, so we wait for it
        to disappear and then create a fresh one.
        """
        from kubernetes_asyncio.client.rest import ApiException

        _REBUILD_PHASES = frozenset({"Failed", "Unknown", "Succeeded"})

        try:
            pod = await self._v1.read_namespaced_pod(
                self._pod_name,
                self._namespace,
            )
            phase = pod.status.phase if pod.status else None
            deletion_ts = (
                pod.metadata.deletion_timestamp
                if pod.metadata is not None
                else None
            )
            if deletion_ts is not None:
                logger.info(
                    "K8sWorkspace: Pod %r is being deleted, "
                    "waiting to recreate",
                    self._pod_name,
                )
                await self._wait_pod_deleted()
                await self._create_pod()
                return
            if phase in {"Running", "Pending"}:
                return
            if phase in _REBUILD_PHASES:
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
            else:
                logger.warning(
                    "K8sWorkspace: Pod %r has unexpected phase %r, "
                    "deleting and recreating",
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
                    container_port=self.gateway_port,
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

            if phase == "Pending" and pod.status:
                for cs in pod.status.container_statuses or []:
                    if cs.state and cs.state.waiting:
                        reason = cs.state.waiting.reason or ""
                        if reason in _TERMINAL_WAITING_REASONS:
                            msg = cs.state.waiting.message or reason
                            raise RuntimeError(
                                f"Pod {self._pod_name!r} container "
                                f"is stuck: {msg}",
                            )
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

    def _bootstrap_commands(self) -> list[str]:
        """Shell commands that provision this Pod once.

        Only runs when the gateway script is missing (fresh PVC, or a
        Pod that died before the script was written). Slim base image
        runs as root, so no ``sudo`` needed — uv lands at
        ``/usr/local/bin/uv`` which is on the default PATH.
        """
        pip_pkgs = list(_GATEWAY_BASE_REQUIREMENTS) + list(self.extra_pip)
        # Quote every requirement string so entries containing spaces
        # or shell metacharacters (e.g. version specifiers wrapped in
        # brackets, direct-URL installs) cannot break the ``sh -c``
        # command or become an injection vector.
        pip_args = " ".join(shlex.quote(p) for p in pip_pkgs)
        sys_deps = " ".join(shlex.quote(d) for d in SYSTEM_DEPS)

        return [
            f"apt-get update -qq "
            f"&& apt-get install -y --no-install-recommends {sys_deps} "
            f"&& rm -rf /var/lib/apt/lists/*",
            "curl -LsSf https://astral.sh/uv/install.sh "
            "| env UV_INSTALL_DIR=/usr/local/bin "
            "INSTALLER_NO_MODIFY_PATH=1 sh",
            f"uv venv {self._gateway_venv}",
            f"uv pip install --python {self._gateway_python} {pip_args}",
            f"uv pip install --python {self._gateway_python} "
            f"--no-deps 'agentscope'",
        ]
