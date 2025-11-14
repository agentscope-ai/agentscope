# -*- coding: utf-8 -*-
"""The runtime configuration in agentscope.

.. note:: You should import this module as ``import ._config``, then use the
 variables defined in this module, instead of ``from ._config import xxx``.
 Because when the variables are changed, the changes will not be reflected in
 the imported module.
"""
import sys
from contextvars import ContextVar
from datetime import datetime
from typing import Any

import shortuuid


def _generate_random_suffix(length: int) -> str:
    """Generate a random suffix."""
    return shortuuid.uuid()[:length]


def _get_default_project() -> str:
    """Get default project name."""
    return "UnnamedProject_At" + datetime.now().strftime("%Y%m%d")


def _get_default_name() -> str:
    """Get default run name."""
    return datetime.now().strftime("%H%M%S_") + _generate_random_suffix(4)


def _get_default_run_id() -> str:
    """Get default run ID."""
    return shortuuid.uuid()


def _get_default_created_at() -> str:
    """Get default created timestamp."""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# Context variables using None as sentinel for lazy initialization
_project_var: ContextVar[str | None] = ContextVar("project", default=None)
_name_var: ContextVar[str | None] = ContextVar("name", default=None)
_run_id_var: ContextVar[str | None] = ContextVar("run_id", default=None)
_created_at_var: ContextVar[str | None] = ContextVar(
    "created_at",
    default=None,
)
_trace_enabled_var: ContextVar[bool] = ContextVar(
    "trace_enabled",
    default=False,
)


class _Config:
    """Configuration class that provides simple access to context variables."""

    @property
    def project(self) -> str:
        """Get the current project name."""
        value = _project_var.get()
        if value is None:
            value = _get_default_project()
            _project_var.set(value)
        return value

    @project.setter
    def project(self, value: str) -> None:
        """Set the project name for the current context."""
        if value is None:
            value = _get_default_project()
        _project_var.set(value)

    @property
    def name(self) -> str:
        """Get the current run name."""
        value = _name_var.get()
        if value is None:
            value = _get_default_name()
            _name_var.set(value)
        return value

    @name.setter
    def name(self, value: str) -> None:
        """Set the run name for the current context."""
        if value is None:
            value = _get_default_name()
        _name_var.set(value)

    @property
    def run_id(self) -> str:
        """Get the current run ID."""
        value = _run_id_var.get()
        if value is None:
            value = _get_default_run_id()
            _run_id_var.set(value)
        return value

    @run_id.setter
    def run_id(self, value: str) -> None:
        """Set the run ID for the current context."""
        if value is None:
            value = _get_default_run_id()
        _run_id_var.set(value)

    @property
    def created_at(self) -> str:
        """Get the creation timestamp."""
        value = _created_at_var.get()
        if value is None:
            value = _get_default_created_at()
            _created_at_var.set(value)
        return value

    @created_at.setter
    def created_at(self, value: str) -> None:
        """Set the creation timestamp for the current context."""
        if value is None:
            value = _get_default_created_at()
        _created_at_var.set(value)

    @property
    def trace_enabled(self) -> bool:
        """Check if tracing is enabled."""
        return _trace_enabled_var.get()

    @trace_enabled.setter
    def trace_enabled(self, value: bool) -> None:
        """Enable or disable tracing for the current context."""
        _trace_enabled_var.set(value)


_config_instance = _Config()

# Configuration attribute names
_CONFIG_ATTRS = {"project", "name", "run_id", "created_at", "trace_enabled"}


def __getattr__(attr_name: str) -> Any:
    """Support attribute access for backward compatibility.

    This allows accessing configuration values as module attributes,
    e.g., `_config.project` instead of `_config._config_instance.project`.
    """
    if attr_name in _CONFIG_ATTRS:
        return getattr(_config_instance, attr_name)
    raise AttributeError(f"module '{__name__}' has no attribute '{attr_name}'")


def __setattr__(attr_name: str, value: Any) -> None:
    """Support attribute assignment for backward compatibility.

    This allows setting configuration values as module attributes,
    e.g., `_config.project = "MyProject"`.
    """
    if attr_name in _CONFIG_ATTRS:
        setattr(_config_instance, attr_name, value)
    else:
        sys.modules[__name__].__dict__[attr_name] = value
