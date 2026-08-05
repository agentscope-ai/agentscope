# AgentScope Agent 服务镜像
# 构建上下文为仓库根目录（因为需复制 pyproject.toml / src / examples）：
#   docker build -f examples/agent_service/Dockerfile -t agentscope-service . --network=host
FROM python:3.14-bookworm

# 配置 apt 清华源（bookworm 使用 deb822 格式的 sources.list.d/debian.sources）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl ca-certificates ripgrep \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv（直接从官方镜像复制二进制，避免 astral.sh 脚本下载失败）
# 官方镜像为多平台 manifest，/uv 与 /uvx 已按目标架构自动选择
COPY --from=ghcr.io/astral-sh/uv:0.7.20 /uv /usr/local/bin/uv
COPY --from=ghcr.io/astral-sh/uv:0.7.20 /uvx /usr/local/bin/uvx

WORKDIR /app

# 创建虚拟环境
ENV UV_PROJECT_ENVIRONMENT=/app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# 配置 PyPI 清华镜像源（uv >= 0.4 使用 UV_DEFAULT_INDEX）
ENV UV_DEFAULT_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple

# 第一步：只复制依赖文件，安装所有依赖（不含项目本身）
# 这样只要 pyproject.toml 不变，这层就会被 Docker 缓存
COPY pyproject.toml README.md ./
COPY src ./src
RUN uv sync --extra service --extra storage-redis --extra workspace-docker --no-install-project

# 第二步：复制完整源码并以可编辑模式安装项目本身
# 源码通过 volume 挂载，修改即时生效
RUN uv pip install --no-deps -e .

# 复制启动脚本及其企业扩展子包（bankcomm_adp）
# 整个 examples/agent_service 目录复制到 /app，main.py 位于 /app/main.py，
# bankcomm_adp 位于 /app/bankcomm_adp，运行时即可被 import。
COPY examples/agent_service/ ./

EXPOSE 8000

# 启动服务
CMD ["python", "main.py"]
