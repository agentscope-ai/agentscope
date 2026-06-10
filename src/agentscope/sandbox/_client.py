# -*- coding: utf-8 -*-
"""Sandbox client abstract interface."""

from abc import ABC, abstractmethod

from ._types import SandboxState
from ._sandbox import Sandbox


class SandboxClient(ABC):
    """Factory for creating and resuming :class:`Sandbox` instances."""

    @abstractmethod
    async def create(
        self,
        workspace_spec: dict | None,
        snapshot_spec: dict | None,
        options: dict | None,
    ) -> Sandbox:
        """Create a new sandbox.

        Returned in a pre-start state; call :meth:`Sandbox.start` before use.
        """

    @abstractmethod
    async def resume(self, state: SandboxState) -> Sandbox:
        """Resume a sandbox from previously serialized state."""

    @abstractmethod
    def serialize_state(self, state: SandboxState) -> str:
        """Serialize sandbox state to a JSON string."""

    @abstractmethod
    def deserialize_state(self, json_str: str) -> SandboxState:
        """Deserialize a JSON string back to sandbox state."""
