# -*- coding: utf-8 -*-
"""Reference namespace configuration built atop the logical filesystem."""
from __future__ import annotations

from copy import deepcopy
from typing import Sequence

from ._handle import FsHandle
from ._memory import InMemoryFileSystem
from ._types import Grant, Operation

INTERNAL_PREFIX = "/internal/"
USERINPUT_PREFIX = "/userinput/"
WORKSPACE_PREFIX = "/workspace/"

_READ_OPS: set[Operation] = {
    "list",
    "file",
    "read_binary",
    "read_file",
    "read_re",
}
_INTERNAL_OPS: set[Operation] = _READ_OPS | {"write"}
_USERINPUT_OPS: set[Operation] = set(_READ_OPS)
_WORKSPACE_OPS: set[Operation] = _READ_OPS | {"write", "delete"}

_DEFAULT_NAMESPACE_GRANTS: dict[str, Grant] = {
    "internal": {
        "prefix": INTERNAL_PREFIX,
        "ops": _INTERNAL_OPS,
    },
    "userinput": {
        "prefix": USERINPUT_PREFIX,
        "ops": _USERINPUT_OPS,
    },
    "workspace": {
        "prefix": WORKSPACE_PREFIX,
        "ops": _WORKSPACE_OPS,
    },
}


def builtin_grant(name: str) -> Grant:
    """Return a grant copy for the configured namespace ``name``."""
    if name not in _DEFAULT_NAMESPACE_GRANTS:
        raise KeyError(f"Unknown builtin namespace: {name}")
    return deepcopy(_DEFAULT_NAMESPACE_GRANTS[name])


def builtin_grants(*names: str) -> list[Grant]:
    """Return grants for the provided namespace names."""
    return [builtin_grant(name) for name in names]


class BuiltinFileSystem(InMemoryFileSystem):
    """Reference filesystem composed of three namespaces."""

    def create_internal_handle(self) -> FsHandle:
        """Return a handle with read/write access to ``/internal/``."""
        return self.create_handle([builtin_grant("internal")])

    def create_userinput_handle(self) -> FsHandle:
        """Return a handle with read-only access to ``/userinput/``."""
        return self.create_handle([builtin_grant("userinput")])

    def create_workspace_handle(self) -> FsHandle:
        """Return a handle with full access to ``/workspace/``."""
        return self.create_handle([builtin_grant("workspace")])

    def create_handle_for(self, namespaces: Sequence[str]) -> FsHandle:
        """Create a handle authorised for the provided namespace names."""
        return self.create_handle(builtin_grants(*namespaces))


__all__ = [
    "BuiltinFileSystem",
    "builtin_grant",
    "builtin_grants",
    "INTERNAL_PREFIX",
    "USERINPUT_PREFIX",
    "WORKSPACE_PREFIX",
]
