# -*- coding: utf-8 -*-
"""The run instance configuration in agentscope."""
from contextvars import ContextVar


class _ConfigCls:
    """The run instance configuration in agentscope."""

    def __init__(
        self,
        run_id: ContextVar[str],
        project: ContextVar[str],
        name: ContextVar[str],
        created_at: ContextVar[str],
        trace_enabled: ContextVar[bool],
    ) -> None:
        """The constructor for _Config class."""
        # Copy the default context variables
        self._run_id = run_id
        self._created_at = created_at
        self._project = project
        self._name = name
        self._trace_enabled = trace_enabled

    @property
    def run_id(self) -> str:
        """Get the run ID."""
        return self._run_id.get()

    @run_id.setter
    def run_id(self, value: str) -> None:
        """Set the run ID."""
        self._run_id.set(value)

    @property
    def created_at(self) -> str:
        """Get the creation time."""
        return self._created_at.get()

    @created_at.setter
    def created_at(self, value: str) -> None:
        """Set the creation time."""
        self._created_at.set(value)

    @property
    def project(self) -> str:
        """Get the project name."""
        return self._project.get()

    @project.setter
    def project(self, value: str) -> None:
        """Set the project name."""
        self._project.set(value)

    @property
    def name(self) -> str:
        """Get the run name."""
        return self._name.get()

    @name.setter
    def name(self, value: str) -> None:
        """Set the run name."""
        self._name.set(value)

    @property
    def trace_enabled(self) -> bool:
        """Get whether tracing is enabled."""
        return self._trace_enabled.get()

    @trace_enabled.setter
    def trace_enabled(self, value: bool) -> None:
        """Set whether tracing is enabled."""
        self._trace_enabled.set(value)
