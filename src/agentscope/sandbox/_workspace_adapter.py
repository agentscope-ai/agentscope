# -*- coding: utf-8 -*-
"""Adapter that wraps an existing :class:`WorkspaceBase` as a :class:`Sandbox`.

This bridges the Java-style sandbox lifecycle (acquire → start → use → stop →
shutdown) with the Python workspace system (initialize → use → close), so that
:class:`SandboxManager` and :class:`SandboxLifecycleMiddleware` can be used
with :class:`DockerWorkspace`, :class:`E2BWorkspace`, or :class:`LocalWorkspace`
without duplicating their container / bootstrap logic.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from ..workspace import WorkspaceBase
from ._types import SandboxState
from ._sandbox import Sandbox
from ._client import SandboxClient


class WorkspaceSandbox(Sandbox):
    """Wraps a :class:`WorkspaceBase` to satisfy the :class:`Sandbox` contract.

    Lifecycle mapping:
    - ``start()``  → ``workspace.initialize()``
    - ``stop()``   → no-op (WorkspaceBase state is filesystem-persistent)
    - ``shutdown()`` → ``workspace.close()``
    """

    def __init__(self, workspace: WorkspaceBase) -> None:
        self._workspace = workspace

    @property
    def workspace(self) -> WorkspaceBase:
        """The underlying workspace instance."""
        return self._workspace

    async def start(self) -> None:
        await self._workspace.initialize()

    async def stop(self) -> None:
        # WorkspaceBase has no separate "stop" concept; its state survives
        # across initialize/close cycles via the filesystem (bind-mounts or
        # E2B pause).  Nothing to do here.
        pass

    async def shutdown(self) -> None:
        await self._workspace.close()

    @property
    def state(self) -> SandboxState:
        return SandboxState(
            session_id=self._workspace.workspace_id,
            workspace_root_ready=self._workspace.is_alive,
        )

    @property
    def is_running(self) -> bool:
        return self._workspace.is_alive


class WorkspaceSandboxClient(SandboxClient):
    """A :class:`SandboxClient` that produces :class:`WorkspaceSandbox` instances.

    Args:
        workspace_factory: Callable that returns a fresh
            :class:`WorkspaceBase` subclass instance.  It must accept
            ``workspace_id`` as a keyword argument (used during resume).
    """

    def __init__(
        self,
        workspace_factory: Callable[..., WorkspaceBase],
    ) -> None:
        self._factory = workspace_factory

    async def create(
        self,
        workspace_spec: dict[str, Any] | None,
        snapshot_spec: dict[str, Any] | None,
        options: dict[str, Any] | None,
    ) -> Sandbox:
        """Create a new workspace sandbox.

        The ``workspace_spec`` is passed through to the factory as keyword
        arguments so each workspace subclass can pick the keys it recognises
        (e.g. ``host_workdir`` for Docker, ``template`` for E2B, ``workdir``
        for Local).
        """
        kwargs = dict(options or {})
        if workspace_spec:
            kwargs.update(workspace_spec)
        ws = self._factory(**kwargs)
        return WorkspaceSandbox(ws)

    async def resume(self, state: SandboxState) -> Sandbox:
        ws = self._factory(workspace_id=state.session_id)
        return WorkspaceSandbox(ws)

    def serialize_state(self, state: SandboxState) -> str:
        return json.dumps(state.to_dict())

    def deserialize_state(self, json_str: str) -> SandboxState:
        return SandboxState.from_dict(json.loads(json_str))
