# -*- coding: utf-8 -*-
"""Dockerfile generation + build-context preparation for DockerWorkspace.

The image is keyed by content hash of the Dockerfile text plus all
files COPYed into it. If the tag already exists locally the build is
skipped; otherwise the caller builds with the prepared context.

Two install modes are supported, picked automatically from the host's
``agentscope`` install:

* **released** — when ``agentscope`` is in site-packages, the container
  installs the same version from PyPI.
* **dev** — when running from a source checkout, the project tree is
  copied into the build context and installed via ``uv pip install
  /tmp/agentscope_src``. This is a transitional path used until
  agentscope is published; it is marked with TODOs in the templates so
  it is easy to delete later.

Public functions:

* :func:`render_dockerfile` — substitute placeholders in
  ``Dockerfile.template`` and return the rendered text.
* :func:`compute_image_tag` — sha256 of Dockerfile + COPY files;
  returns ``agentscope-workspace:<12hex>``.
* :func:`prepare_build_context` — assemble a temp directory holding
  the Dockerfile, ``requirements.txt``, and (in dev mode) the
  agentscope source tree. Returns ``(ctx_dir, tag, copy_files)``.
"""

import hashlib
import importlib.resources as _res
import shutil
import tempfile
from pathlib import Path

from .._utils import (
    _GATEWAY_BASE_REQUIREMENTS,
    _agentscope_source_root,
    _agentscope_version,
    _is_released_install,
    _is_source_ignored,
    _read_gateway_script_bytes,
)

# ── shared constants (also imported by _docker_workspace) ──────────

DEFAULT_BASE_IMAGE = "python:3.11-slim"
DEFAULT_GATEWAY_PORT = 5600

CONTAINER_WORKDIR = "/workspace"
CONTAINER_DATA_DIR = f"{CONTAINER_WORKDIR}/data"
CONTAINER_SKILLS_DIR = f"{CONTAINER_WORKDIR}/skills"
CONTAINER_SESSIONS_DIR = f"{CONTAINER_WORKDIR}/sessions"
CONTAINER_MCP_FILE = f"{CONTAINER_WORKDIR}/.mcp"

GATEWAY_HOME = "/root/.agentscope"
GATEWAY_VENV = f"{GATEWAY_HOME}/.venv"
GATEWAY_CONFIG = f"{GATEWAY_HOME}/gateway.config.json"
GATEWAY_LOG = f"{GATEWAY_HOME}/gateway.log"
# Standalone gateway script copied into the image. We invoke this
# directly rather than via ``python -m agentscope.workspace._mcp_gateway``
# so Python does not auto-import ``agentscope.workspace.__init__`` (which
# pulls in skill/tool/local_workspace/docker_workspace and their heavy
# transitive dependencies).
GATEWAY_SCRIPT = f"{GATEWAY_HOME}/_mcp_gateway_app.py"

IMAGE_REPO = "agentscope-workspace"

# ── template loading ───────────────────────────────────────────────

_TEMPLATE_PKG = "agentscope.workspace._docker"
_DOCKERFILE_TEMPLATE = "Dockerfile.template"
_DOCKERFILE_NODE_FROM_TEMPLATE = "Dockerfile.node_from.template"
_DOCKERFILE_NODE_COPY_TEMPLATE = "Dockerfile.node_copy.template"
_DOCKERFILE_INSTALL_PYPI_TEMPLATE = "Dockerfile.install_pypi.template"
_DOCKERFILE_INSTALL_SRC_TEMPLATE = "Dockerfile.install_src.template"


def _read_template(name: str) -> str:
    """Read a packaged template file as text."""
    return _res.files(_TEMPLATE_PKG).joinpath(name).read_text(encoding="utf-8")


# ── source-tree packaging (dev mode) ───────────────────────────────


def _source_ignore(_dir: str, names: list[str]) -> list[str]:
    """``shutil.copytree`` ignore filter — defers
    to :func:`_is_source_ignored`."""
    return [n for n in names if _is_source_ignored(n)]


def _hash_directory(root: Path) -> bytes:
    """Hash a directory tree's contents in a stable order."""
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix().encode("utf-8")
        h.update(b"\x00")
        h.update(rel)
        h.update(b"\x00")
        h.update(p.read_bytes())
    return h.digest()


# ── public API ─────────────────────────────────────────────────────


def render_dockerfile(
    *,
    base_image: str = DEFAULT_BASE_IMAGE,
    gateway_home: str = GATEWAY_HOME,
    container_workdir: str = CONTAINER_WORKDIR,
    node_version: str | None = None,
    install_agentscope_block: str = "",
) -> str:
    """Render the Dockerfile by substituting into the template files.

    Args:
        base_image: Base image (must already provide ``python3``).
        gateway_home: In-container directory for the gateway venv,
            script and config.
        container_workdir: Container-side workdir; bind-mounted from
            the host when the workspace's ``workdir`` is set, else
            an empty in-image directory.
        node_version: When given (e.g. ``"20"``) a ``node`` and ``npm``
            of that version are copied from the official Node slim
            image. ``None`` skips Node installation.
        install_agentscope_block: Pre-rendered block (no surrounding
            blank lines) that installs ``agentscope`` into the gateway
            venv. Built by :func:`prepare_build_context` from one of
            ``Dockerfile.install_{pypi,src}.template``.

    Returns:
        The full Dockerfile text.
    """
    if node_version:
        # Normalise trailing whitespace so the main template's surrounding
        # newlines fully control inter-section spacing — the template files
        # themselves are not relied on for exact terminal newlines.
        nf_raw = _read_template(_DOCKERFILE_NODE_FROM_TEMPLATE).format(
            node_version=node_version,
        )
        nc_raw = _read_template(_DOCKERFILE_NODE_COPY_TEMPLATE)
        node_from_block = nf_raw.rstrip() + "\n"
        node_copy_block = nc_raw.rstrip() + "\n\n"
    else:
        node_from_block = ""
        node_copy_block = ""

    return _read_template(_DOCKERFILE_TEMPLATE).format(
        base_image=base_image,
        gateway_home=gateway_home,
        container_workdir=container_workdir,
        node_from_block=node_from_block,
        node_copy_block=node_copy_block,
        install_agentscope_block=install_agentscope_block.rstrip() + "\n",
    )


def _render_requirements(extra_pip: list[str]) -> str:
    """Render ``requirements.txt`` content for the gateway venv."""
    pinned = list(_GATEWAY_BASE_REQUIREMENTS) + list(extra_pip or [])
    return "\n".join(pinned) + "\n"


def compute_image_tag(
    dockerfile_text: str,
    copy_files: dict[str, bytes],
) -> str:
    """Hash the Dockerfile and COPY payloads into a deterministic tag.

    Args:
        dockerfile_text: Full Dockerfile text.
        copy_files: Mapping of context-relative filename → bytes for
            every file referenced by a ``COPY`` instruction. Directory
            payloads (e.g. the agentscope source tree in dev mode) are
            represented as a single synthetic key whose value is the
            tree's content hash.

    Returns:
        Tag of the form ``agentscope-workspace:<12 hex chars>``.
    """
    h = hashlib.sha256()
    h.update(b"DOCKERFILE\x00")
    h.update(dockerfile_text.encode("utf-8"))
    for name in sorted(copy_files):
        h.update(b"\x00FILE\x00")
        h.update(name.encode("utf-8"))
        h.update(b"\x00")
        h.update(copy_files[name])
    return f"{IMAGE_REPO}:{h.hexdigest()[:12]}"


def prepare_build_context(
    *,
    base_image: str = DEFAULT_BASE_IMAGE,
    gateway_home: str = GATEWAY_HOME,
    container_workdir: str = CONTAINER_WORKDIR,
    node_version: str | None = None,
    extra_pip: list[str] | None = None,
) -> tuple[Path, str, dict[str, bytes]]:
    """Assemble a temporary build context directory.

    Writes Dockerfile, ``requirements.txt`` and the agentscope payload
    (source tree in dev mode, nothing extra in released mode) into a
    fresh temp dir. The caller is responsible for removing the
    directory after the build completes.

    Returns:
        ``(ctx_dir, tag, copy_files)`` — ``ctx_dir`` holds the
        materialised files; ``tag`` is the deterministic image tag;
        ``copy_files`` is the same mapping that was hashed into the
        tag (handy for callers that want to recompute / verify).
    """
    extra_pip_list = list(extra_pip or [])

    released = _is_released_install()
    if released:
        version = _agentscope_version()
        install_block = _read_template(
            _DOCKERFILE_INSTALL_PYPI_TEMPLATE,
        ).format(agentscope_version=version)
        source_root: Path | None = None
    else:
        # TODO(release): drop this branch once agentscope is on PyPI; the
        # released path above subsumes it. The dev branch copies the project
        # tree into the build context so the in-container venv can install
        # the same code the host is running.
        install_block = _read_template(_DOCKERFILE_INSTALL_SRC_TEMPLATE)
        source_root = _agentscope_source_root()

    dockerfile_text = render_dockerfile(
        base_image=base_image,
        gateway_home=gateway_home,
        container_workdir=container_workdir,
        node_version=node_version,
        install_agentscope_block=install_block,
    )
    requirements_text = _render_requirements(extra_pip_list)

    # Read the gateway script bytes once — we both hash them into the
    # image tag (so script edits invalidate the image cache) and write
    # them into the build context so the Dockerfile can ``COPY`` them
    # to ``GATEWAY_SCRIPT``.
    gateway_script_bytes = _read_gateway_script_bytes()

    copy_files: dict[str, bytes] = {
        "requirements.txt": requirements_text.encode("utf-8"),
        "_mcp_gateway_app.py": gateway_script_bytes,
    }
    if source_root is not None:
        # Synthetic entry: the directory tree is too large to inline, so we
        # hash it once and stash the digest under a stable key. Image-tag
        # determinism depends only on this digest, not on the temp path.
        copy_files["agentscope_src/"] = _hash_directory(source_root)

    tag = compute_image_tag(dockerfile_text, copy_files)

    ctx_dir = Path(tempfile.mkdtemp(prefix="as-ws-build-"))
    (ctx_dir / "Dockerfile").write_text(dockerfile_text, encoding="utf-8")
    (ctx_dir / "requirements.txt").write_bytes(
        copy_files["requirements.txt"],
    )
    (ctx_dir / "_mcp_gateway_app.py").write_bytes(gateway_script_bytes)
    if source_root is not None:
        shutil.copytree(
            source_root,
            ctx_dir / "agentscope_src",
            ignore=_source_ignore,
            symlinks=False,
        )

    return ctx_dir, tag, copy_files
