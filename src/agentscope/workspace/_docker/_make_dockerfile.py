# -*- coding: utf-8 -*-
"""Dockerfile generation + build-context preparation for DockerWorkspace.

The image is keyed by content hash of the Dockerfile text plus all
files COPYed into it. If the tag already exists locally the build is
skipped; otherwise the caller builds with the prepared context.

The container installs the same ``agentscope`` version as the host via
``uv pip install --no-deps`` inside the gateway venv.

Public functions:

* :func:`render_dockerfile` — substitute placeholders in
  ``Dockerfile.template`` and return the rendered text.
* :func:`compute_image_tag` — sha256 of Dockerfile + COPY files;
  returns ``agentscope-workspace:<12hex>``.
* :func:`prepare_build_context` — assemble a temp directory holding
  the Dockerfile, ``requirements.txt``, and helper scripts. Returns
  ``(ctx_dir, tag, copy_files)``.
"""

import hashlib
import importlib.resources as _res
import tempfile
from pathlib import Path

from .._utils import (
    _GATEWAY_BASE_REQUIREMENTS,
    _read_gateway_script_bytes,
    _read_glob_helper_bytes,
)

# ── shared constants (also imported by _docker_workspace) ──────────

DEFAULT_BASE_IMAGE = "python:3.11-slim"
DEFAULT_GATEWAY_PORT = 5600

CONTAINER_WORKDIR = "/workspace"

GATEWAY_HOME = "/root/.agentscope"

IMAGE_REPO = "agentscope-workspace"

# ── template loading ───────────────────────────────────────────────

_TEMPLATE_PKG = "agentscope.workspace._docker"
_DOCKERFILE_TEMPLATE = "Dockerfile.template"
_DOCKERFILE_NODE_FROM_TEMPLATE = "Dockerfile.node_from.template"
_DOCKERFILE_NODE_COPY_TEMPLATE = "Dockerfile.node_copy.template"


def _read_template(name: str) -> str:
    """Read a packaged template file as text."""
    return _res.files(_TEMPLATE_PKG).joinpath(name).read_text(encoding="utf-8")


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
            venv. Built by :func:`prepare_build_context`.

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
            every file referenced by a ``COPY`` instruction.

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

    Writes Dockerfile, ``requirements.txt`` and helper scripts into a
    fresh temp dir. The caller is responsible for removing the directory
    after the build completes.

    Returns:
        ``(ctx_dir, tag, copy_files)`` — ``ctx_dir`` holds the
        materialised files; ``tag`` is the deterministic image tag;
        ``copy_files`` is the same mapping that was hashed into the
        tag (handy for callers that want to recompute / verify).
    """
    extra_pip_list = list(extra_pip or [])

    install_block = 'RUN uv pip install "agentscope"'

    dockerfile_text = render_dockerfile(
        base_image=base_image,
        gateway_home=gateway_home,
        container_workdir=container_workdir,
        node_version=node_version,
        install_agentscope_block=install_block,
    )
    requirements_text = _render_requirements(extra_pip_list)

    # Read helper scripts once — we both hash them into the image tag
    # (so edits invalidate the image cache) and write them into the
    # build context so the Dockerfile can ``COPY`` them.
    gateway_script_bytes = _read_gateway_script_bytes()
    glob_helper_bytes = _read_glob_helper_bytes()

    copy_files: dict[str, bytes] = {
        "requirements.txt": requirements_text.encode("utf-8"),
        "_mcp_gateway_app.py": gateway_script_bytes,
        "_glob_helper.py": glob_helper_bytes,
    }
    tag = compute_image_tag(dockerfile_text, copy_files)

    ctx_dir = Path(tempfile.mkdtemp(prefix="as-ws-build-"))
    (ctx_dir / "Dockerfile").write_text(dockerfile_text, encoding="utf-8")
    (ctx_dir / "requirements.txt").write_bytes(
        copy_files["requirements.txt"],
    )
    (ctx_dir / "_mcp_gateway_app.py").write_bytes(gateway_script_bytes)
    (ctx_dir / "_glob_helper.py").write_bytes(glob_helper_bytes)

    return ctx_dir, tag, copy_files
