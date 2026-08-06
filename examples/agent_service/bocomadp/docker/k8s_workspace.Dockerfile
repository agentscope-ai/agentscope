# syntax=docker/dockerfile:1.6
# ============================================================================
# 预构建 K8s 沙箱工作区镜像（离线可用）
#
# 用途：K8s Pod 使用此镜像启动后，gateway venv + 脚本已就绪，
#       bootstrap 阶段被完全跳过，Pod 就绪时间从 ~3min 降至 ~10s。
#
# 构建（构建上下文为 agentscope 仓库根目录）：
#   docker build \
#       -f examples/agent_service/bocomadp/docker/k8s_workspace.Dockerfile \
#       -t <your-registry>/agentscope-k8s-workspace:<version> \
#       .
#
# 推送到内网 registry：
#   docker push <your-registry>/agentscope-k8s-workspace:<version>
#
# K8s 使用方式（环境变量）：
#   ADP_K8S_IMAGE=<your-registry>/agentscope-k8s-workspace:<version>
#
# 注意：若构建环境也无法访问公网，请先准备以下物料：
#   1. 将 Docker Hub 的 python:3.11-slim 拉取到本地 registry
#   2. 修改 apt 源为内部镜像（替换 deb.debian.org）
#   3. 修改 PyPI 源（设置 UV_DEFAULT_INDEX 环境变量）
# ============================================================================

FROM python:3.11-slim

# ---- 1. 系统依赖 ----
# 与 agentscope.workspace._k8s._constants.SYSTEM_DEPS 保持一致
RUN apt-get update -qq \
 && apt-get install -y --no-install-recommends \
        curl ca-certificates ripgrep \
 && rm -rf /var/lib/apt/lists/*

# ---- 2. 安装 uv ----
RUN curl -LsSf https://astral.sh/uv/install.sh \
  | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh

# ---- 3. Gateway 目录与环境变量 ----
# 与 agentscope.workspace._k8s._constants.GATEWAY_HOME 保持一致
ENV GATEWAY_HOME=/root/.agentscope

# ---- 4. 创建 Gateway 虚拟环境并安装运行时依赖 ----
# 与 agentscope.workspace._utils._GATEWAY_BASE_REQUIREMENTS 保持一致
RUN mkdir -p "${GATEWAY_HOME}" \
 && uv venv "${GATEWAY_HOME}/.venv" \
 && uv pip install --python "${GATEWAY_HOME}/.venv/bin/python" \
        'mcp<2.0.0' uvicorn fastapi httpx

# ---- 5. 安装 agentscope（仅 mcp 模块，--no-deps 避免重复拉依赖） ----
# 构建上下文为仓库根目录，pyproject.toml + src/ 可被 COPY
COPY pyproject.toml README.md /src/
COPY src /src/src
RUN uv pip install --python "${GATEWAY_HOME}/.venv/bin/python" \
        --no-deps /src

# ---- 6. 写入 Gateway 脚本 + Glob Helper ----
# 脚本存在时 SandboxedWorkspaceBase._setup_mcp_gateway 直接跳过 bootstrap
COPY src/agentscope/workspace/_mcp_gateway/_mcp_gateway_app.py \
     "${GATEWAY_HOME}/_mcp_gateway_app.py"
COPY src/agentscope/tool/_builtin/_scripts/_glob_helper.py \
     "${GATEWAY_HOME}/_glob_helper.py"

# ---- 7. 工作区根目录 ----
# Pod 会将 PVC 挂载到此处；K8s Pod spec 会覆盖 CMD 为 sleep infinity
WORKDIR /workspace
CMD ["sleep", "infinity"]
