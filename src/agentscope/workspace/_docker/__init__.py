# -*- coding: utf-8 -*-
"""Docker-backed workspace.

The container image is built on demand from a content-hashed Dockerfile
(see :mod:`._make_dockerfile`); a tag cache hit skips the build. MCP
servers run *inside* the container behind a FastAPI gateway and are
reached over HTTP — see :mod:`agentscope.workspace._gateway_client`.
"""

from ._docker_workspace import DockerWorkspace

__all__ = ["DockerWorkspace"]
