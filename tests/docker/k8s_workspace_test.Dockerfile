# syntax=docker/dockerfile:1.6
# ---------------------------------------------------------------------------
# Pre-baked image for K8sWorkspace integration tests.
#
# Rationale
# ---------
# K8sWorkspace's ``_setup_mcp_gateway`` skips the bootstrap phase when the
# gateway script already exists inside the sandbox (see
# ``SandboxedWorkspaceBase._setup_mcp_gateway`` — fast-path skip).
# By pre-installing every dependency the bootstrap would install and by
# copying the gateway/glob-helper scripts to ``GATEWAY_HOME`` at build time,
# ``initialize()`` skips ``apt-get`` + ``uv install`` + ``pip install`` and
# starts in ~10 s instead of ~5 min.
#
# Build context MUST be the repository root:
#   docker build \
#       -f tests/docker/k8s_workspace_test.Dockerfile \
#       -t agentscope-k8s-test:ci .
#
# In CI the image is loaded into the kind cluster with:
#   kind load docker-image agentscope-k8s-test:ci --name <cluster>
# ---------------------------------------------------------------------------

FROM python:3.11-slim

# Keep in sync with agentscope.workspace._k8s._k8s_bootstrap.SYSTEM_DEPS
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
        curl ca-certificates ripgrep \
 && rm -rf /var/lib/apt/lists/*

# uv — matches the bootstrap command exactly.
RUN curl -LsSf https://astral.sh/uv/install.sh \
  | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh

# Keep in sync with agentscope.workspace._k8s._k8s_bootstrap.GATEWAY_HOME
ENV GATEWAY_HOME=/root/.agentscope

# Gateway venv + base runtime requirements
# (mirrors ``_GATEWAY_BASE_REQUIREMENTS`` in _sandboxed_base.py)
RUN mkdir -p "${GATEWAY_HOME}" \
 && uv venv "${GATEWAY_HOME}/.venv" \
 && uv pip install --python "${GATEWAY_HOME}/.venv/bin/python" \
        mcp uvicorn fastapi

# Install agentscope itself from the local checkout.
# Build context is repo root, so pyproject.toml + src/ are visible at /src.
COPY pyproject.toml README.md /src/
COPY src /src/src
RUN uv pip install --python "${GATEWAY_HOME}/.venv/bin/python" \
        --no-deps /src

# Copy the gateway script + glob helper into GATEWAY_HOME so that
# ``_setup_mcp_gateway`` sees ``_gateway_script`` on disk and skips bootstrap.
COPY src/agentscope/workspace/_mcp_gateway/_mcp_gateway_app.py \
     "${GATEWAY_HOME}/_mcp_gateway_app.py"
COPY src/agentscope/tool/_builtin/_scripts/_glob_helper.py \
     "${GATEWAY_HOME}/_glob_helper.py"

# The workdir is created lazily by ``_provision_sandbox``; leaving it here
# just makes ad-hoc `docker run` debugging friendlier.
WORKDIR /workspace

# Container has nothing to run by itself — K8s Pod spec will override CMD
# with the sleep loop from ``_build_pod_spec``.
CMD ["sleep", "infinity"]

