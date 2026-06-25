# -*- coding: utf-8 -*-
"""Host-side helpers shared by Docker + E2B backends.

Pure functions for detecting the local ``agentscope`` install (released
vs dev), iterating the source tree with a stable ignore set, and
reading the gateway script bundled with the package. No Docker / E2B
SDK dependency lives here — both backends import the same helpers so
their install logic stays byte-for-byte in sync.

All names in this module are package-private (leading underscore).
External code should not import from here directly; the two backends
that consume these helpers live next to it under
``agentscope.workspace``.
"""

import importlib.resources as _res
from pathlib import Path

# ── shared constants ───────────────────────────────────────────────

#: Minimum Python packages the gateway script needs at runtime.
#: Both Docker (image build) and E2B (sandbox bootstrap) install this
#: same tuple into the gateway venv before adding ``agentscope`` itself.
_GATEWAY_BASE_REQUIREMENTS: tuple[str, ...] = (
    "mcp",
    "uvicorn",
    "fastapi",
)

#: Basename set excluded when packaging the agentscope source tree
#: into a build / bootstrap context (dev-mode only — released installs
#: pull from PyPI and never copy the tree).
_SOURCE_IGNORE_NAMES: frozenset[str] = frozenset(
    {
        "__pycache__",
        "node_modules",
        "build",
        "dist",
        "venv",
        "workdir",
        "examples",
        "tests",
        "docs",
        "assets",
        "scripts",
        "dump.rdb",
        "uv.lock",
    },
)


def _is_source_ignored(name: str) -> bool:
    """Whether a single basename should be excluded from the source payload.

    Args:
        name (`str`):
            Basename to check (e.g. ``"__pycache__"``, ``".git"``,
            ``"foo.pyc"``).

    Returns:
        `bool`:
            ``True`` if the name matches the ignore set / hidden-file
            rule / cache-extension rule; ``False`` otherwise.
    """
    if name.startswith("."):
        return True
    if name in _SOURCE_IGNORE_NAMES:
        return True
    return name.endswith(".pyc") or name.endswith(".egg-info")


# ── agentscope install detection ───────────────────────────────────


def _agentscope_module_path() -> Path:
    """Return the filesystem path of the imported ``agentscope`` package.

    Returns:
        `Path`:
            The directory containing ``agentscope/__init__.py``.
    """
    import agentscope  # local import — keeps module import cheap

    file = getattr(agentscope, "__file__", None)
    if not file:
        raise RuntimeError(
            "agentscope has no __file__ attribute; cannot locate package",
        )
    return Path(file).resolve().parent


def _is_released_install() -> bool:
    """Return ``True`` if the imported ``agentscope`` lives in site-packages.

    Used to pick between PyPI install (released) and source-tree
    upload (dev) when provisioning the gateway venv inside a
    container or sandbox.
    """
    parts = _agentscope_module_path().parts
    return "site-packages" in parts or "dist-packages" in parts


def _agentscope_version() -> str:
    """Return the installed ``agentscope`` version string.

    Falls back to :func:`importlib.metadata.version` when the package
    has no ``__version__`` attribute.
    """
    import agentscope

    version = getattr(agentscope, "__version__", None)
    if not version:
        try:
            from importlib.metadata import version as _v

            version = _v("agentscope")
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(
                "cannot determine agentscope version",
            ) from e
    return version


def _agentscope_source_root() -> Path:
    """Locate the project root containing ``pyproject.toml`` + ``src/``.

    Only valid in dev mode. Walks up from the package directory until
    a ``pyproject.toml`` is found alongside a ``src/`` (or
    ``agentscope/``) directory.

    Returns:
        `Path`:
            The project root path.
    """
    pkg = _agentscope_module_path()
    for parent in [pkg, *pkg.parents]:
        if (parent / "pyproject.toml").is_file() and (
            (parent / "src").is_dir() or (parent / "agentscope").is_dir()
        ):
            return parent
    raise RuntimeError(
        f"cannot locate agentscope project root from {pkg}",
    )


# ── gateway script ─────────────────────────────────────────────────


def _read_gateway_script_bytes() -> bytes:
    """Read the standalone gateway script as bytes via ``importlib.resources``.

    The script ships at
    ``agentscope/workspace/_mcp_gateway/_mcp_gateway_app.py``. Both
    backends copy it to a fixed in-container / in-sandbox path so the
    launch command can invoke it directly, avoiding ``python -m`` and
    the heavy ``agentscope.workspace.__init__`` import graph.
    """
    return (
        _res.files("agentscope.workspace._mcp_gateway")
        .joinpath("_mcp_gateway_app.py")
        .read_bytes()
    )


# ── builtin tool helper scripts ───────────────────────────────────


def _read_glob_helper_bytes() -> bytes:
    """Read the standalone glob helper script as bytes.

    The script ships at
    ``agentscope/tool/_builtin/_scripts/_glob_helper.py``. Both Docker
    and E2B backends copy it into the workspace so the :class:`Glob`
    tool can invoke it uniformly via ``exec_shell``.

    Returns:
        `bytes`:
            The raw contents of the ``_glob_helper.py`` script.
    """
    return (
        _res.files("agentscope.tool._builtin._scripts")
        .joinpath("_glob_helper.py")
        .read_bytes()
    )
