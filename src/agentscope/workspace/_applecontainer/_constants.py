# -*- coding: utf-8 -*-
"""Apple-Container-specific constants for
:class:`AppleContainerWorkspace`.

Path layout (venv, script, log, helper) is derived on the base class
from ``_gateway_home``. This module only carries defaults that cannot
be derived: image, timeouts, port, container user, workdir.
"""

#: Default base image. Must provide ``python3`` and be Debian/Ubuntu
#: based for apt-get bootstrap compatibility.
DEFAULT_BASE_IMAGE = "python:3.11-slim"

#: Default gateway port inside the container (no host port mapping).
DEFAULT_GATEWAY_PORT = 5600

#: Default CPUs allocated to the container.
DEFAULT_CPUS = 2

#: Default memory allocated to the container (2 GiB).
DEFAULT_MEMORY = "2G"

#: Container-side workdir — agent-visible root.
CONTAINER_WORKDIR = "/workspace"

#: Container-side gateway home (root user, same as Docker workspace).
GATEWAY_HOME = "/root/.agentscope"
