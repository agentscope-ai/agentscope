# AgentScope 开发指南

本文档介绍如何在容器化和本地环境中搭建 AgentScope 的开发环境并启动服务。

## 1. Docker Compose 容器化开发（全栈一键启动）

仓库内置了 `Docker/docker-compose.yml`，可一键拉起 **Redis + Agent Service + Web UI 后端 + Web UI 前端** 的完整技术栈，并通过源码挂载支持热修改开发，适合容器化开发、联调与快速体验。

### 1.1 架构总览

```
浏览器 ──▶ nginx (:80, 网关统一入口)
               ├─ /api/* ──▶ webui-backend (:3000, Node.js)
               └─ 其余 ──▶ webui-frontend (:80, nginx 静态资源)
           webui-backend
               │ AGENTSCOPE_API_URL=http://agentscope:8000
               ▼
           agentscope (:8000, FastAPI) ──▶ redis (:6379)
               │ /var/run/docker.sock
               ▼
           Docker 工作区沙箱容器（可选）
```

| 服务 | 镜像 / 构建来源 | 端口 | 说明 |
| --- | --- | --- | --- |
| `redis` | `redis:7-bookworm` | 6379 | 服务存储后端，数据持久化到 `redis-data` 卷 |
| `agentscope` | `examples/agent_service/Dockerfile`（构建上下文为仓库根目录） | 8000 | FastAPI Agent 服务，源码只读挂载 |
| `webui-backend` | `examples/web_ui/backend/Dockerfile` | 3000 | Web UI 的 Node.js 后端，源码读写挂载 |
| `webui-frontend` | `examples/web_ui/frontend/Dockerfile` | 5173（仅容器网络） | vite dev server（HMR），由网关 nginx 代理 |
| `nginx` | `nginx:alpine` | 80 | 网关统一入口：/api/* → webui-backend，其余 → webui-frontend |

前后端 Dockerfile 均为 **dev / prod 多阶段构建**，compose 默认使用 `target: dev`（容器内启动开发服务器，支持热更新）：

| 服务 | dev 阶段 | prod 阶段 | 切换方式 |
| --- | --- | --- | --- |
| `webui-backend` | nodemon + ts-node 热重载 | tsc 编译 + node 运行 | 删除 compose 中 `target: dev` |
| `webui-frontend` | vite dev server（5173，HMR） | vite build + nginx 托管静态文件（80） | 删除 `target: dev`，并把 nginx 的 `FRONTEND_UPSTREAM` 改为 `webui-frontend:80` |

依赖链为 `webui-frontend → webui-backend → agentscope → redis`，`docker compose up` 会按序启动。

### 1.2 快速开始

**第一步：导出模型 API Key**

compose 文件中 API Key 采用无默认值的透传写法（`- DASHSCOPE_API_KEY`），必须先在宿主机导出，否则容器内为空：

```bash
export DASHSCOPE_API_KEY="your-key"    # 通义千问
export OPENAI_API_KEY="your-key"       # OpenAI（可选）
export ANTHROPIC_API_KEY="your-key"    # Anthropic（可选）
export DEEPSEEK_API_KEY="your-key"     # DeepSeek（可选）
```

也可以在同目录创建 `.env` 文件写入上述变量，`docker compose` 会自动读取。

**第二步：构建并启动**

```bash
cd Docker
docker compose up -d --build

# 跟踪 Agent Service 启动日志
docker compose logs -f agentscope
```

**第三步：访问**

| 地址 | 内容 |
| --- | --- |
| `http://localhost` | nginx 网关统一入口（前端页面；/api/* 自动转发到后端） |
| `http://localhost:8000/docs` | Agent Service 的 FastAPI 交互文档 |

前端已在构建时注入 `VITE_API_PREFIX=/api`，`/api/*` 请求经 nginx 网关转发到后端；后端通过 `AGENTSCOPE_API_URL=http://agentscope:8000` 连接服务，**无需在页面上手动配置 endpoint**。

### 1.3 基于源码挂载的热修改开发

compose 将宿主机源码挂载进容器，配合镜像内的可编辑安装实现"改代码即生效"：

- **Agent Service（Python）**：`src/`、`main.py`、`pyproject.toml` 以只读（`:ro`）方式挂载，服务以热重载模式启动，**修改 Python 源码保存后自动重载，无需重建镜像**。若修改了 `pyproject.toml` 中的依赖，则需重建：`docker compose build agentscope && docker compose up -d agentscope`。
- **Web UI 后端（Node.js）**：dev 模式下 `src/`、`package.json`、`tsconfig.json` 读写挂载，nodemon 监听 `src/` 变更**自动重载，无需重启**；修改 `package.json` 依赖后需 `docker compose build webui-backend` 重建。
- **Web UI 前端（React）**：dev 模式下源码目录挂载进容器，vite dev server 提供 **HMR，保存即热更新**（`node_modules` 由匿名卷保留镜像内版本）；浏览器直连网关 `http://localhost` 即可，HMR WebSocket 经网关透传。

### 1.4 数据持久化

compose 声明了两个命名卷：

| 卷 | 挂载点 | 内容 |
| --- | --- | --- |
| `redis-data` | 容器内 `/data` | Redis 持久化数据（会话、团队等存储） |
| `workspace-data` | 容器内 `/app/workspaces` | Agent 工作区文件 |

`docker compose down` 不会删除卷；如需彻底清理数据，使用 `docker compose down -v`。

### 1.5 与 Docker 工作区沙箱的配合

`agentscope` 服务已挂载 `/var/run/docker.sock`，容器内的服务可以直接调用宿主机 Docker daemon 创建 Docker 工作区沙箱容器（参见 §3.1），把 `main.py` 中的 `LocalWorkspaceManager` 换成 `DockerWorkspaceManager` 即可，无需额外配置。

### 1.6 常用操作

```bash
cd Docker

# 查看全部服务状态
docker compose ps

# 查看某个服务的日志
docker compose logs -f webui-backend

# 只重建并重启单个服务（如改了后端代码）
docker compose up -d --build webui-backend

# 进入服务容器调试
docker compose exec agentscope /bin/bash

# 停止全部服务（保留数据卷）
docker compose down
```

### 1.7 注意事项

- **Redis 连接**：compose 已通过 `REDIS_HOST=redis` 环境变量注入，`main.py` 会从环境变量读取（默认 `localhost`），容器内开发无需改代码。
- **API Key 为空**：服务能启动但模型调用会失败，请确认宿主机已导出对应环境变量后再 `up`。
- **生产部署**：示例以 `reload=True` 单进程启动，生产环境建议关闭热重载并使用 `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4`，同时将 `InMemoryMessageBus` 换成 `RedisMessageBus`（`main.py` 中已附注切换方法）。

---

## 2. 本地开发

### 2.1 环境要求

| 依赖 | 版本 |
| --- | --- |
| Python | ≥ 3.11 |
| Node.js（Web UI） | ≥ 20，需包含 `npx` |
| Redis（可选，服务存储后端） | ≥ 7 |
| pnpm（Web UI 前端） | ≥ 8 |

### 2.2 安装 AgentScope

```bash
# 克隆仓库
git clone https://github.com/agentscope-ai/agentscope.git
cd agentscope

# 创建虚拟环境
uv venv
source .venv/bin/activate

# 以可编辑模式安装，包含全部开发依赖
uv pip install -e ".[dev]"
```

`dev` extra 会拉入 `pre-commit`、`pytest`、文档工具链以及 `full` extra（包含 `models`、`service`、`storage`）。

### 2.3 启用 pre-commit 钩子

```bash
pre-commit install
```

pre-commit 会在每次 commit 时自动执行代码格式化（black、flake8、pylint）与基础检查（AST、YAML、TOML 等）。

### 2.4 运行测试

```bash
# 运行全部单元测试
pytest tests

# 运行指定测试文件
pytest tests/agent_basic_test.py
```

依赖可选 extra 的测试（如 Redis、Ollama）在该 extra 未安装时会自动 skip。

### 2.5 启动 Agent Service

Agent Service 是基于 FastAPI 的多租户多会话服务，需要 Redis 作为存储后端。

**第一步：启动 Redis**

```bash
# macOS (Homebrew)
brew install redis
brew services start redis

# Linux (systemd)
sudo apt install redis-server
sudo systemctl start redis-server

# Docker (跨平台)
docker run --rm -d -p 6379:6379 redis:7
```

**第二步：配置模型 API Key**

根据使用的模型提供商设置环境变量：

```bash
export DASHSCOPE_API_KEY="your-key"    # 通义千问
export OPENAI_API_KEY="your-key"       # OpenAI
export ANTHROPIC_API_KEY="your-key"    # Anthropic
export DEEPSEEK_API_KEY="your-key"     # DeepSeek
# 其他模型提供商类似
```

**第三步：启动服务**

```bash
cd examples/agent_service
python main.py
```

服务默认监听 `http://0.0.0.0:8000`，开启热重载。

### 2.6 启动 Web UI

Web UI 采用前后端分离架构（React + Node.js），需要单独启动。

```bash
cd examples/web_ui

# 安装前端依赖
pnpm install

# 启动开发模式（同时启动前端和后端）
pnpm dev
```

启动后在浏览器中访问 Web UI，将 API endpoint 设置为 `http://localhost:8000` 即可开始使用。

### 2.7 本地开发目录结构

```
agentscope/
├── src/agentscope/       # SDK 核心源码
│   ├── agent/            # Agent 核心
│   ├── model/            # 模型适配层
│   ├── formatter/        # 消息格式化
│   ├── tool/             # 工具系统
│   ├── app/              # 应用服务（FastAPI）
│   ├── workspace/        # 工作区管理
│   ├── storage/          # 存储抽象
│   ├── rag/              # RAG 管线
│   └── ...
├── tests/                # 测试套件
├── examples/             # 示例代码
│   ├── agent_service/    # Agent Service 启动示例
│   └── web_ui/           # Web UI 示例
└── docs/                 # 项目文档
```

---

## 3. 工作区沙箱后端

AgentScope 提供多种容器化工作区后端，用于在隔离的沙箱环境中运行 Agent 的工具调用。以下分别介绍各后端的配置与启动方式。

### 3.1 Docker 工作区

Docker 工作区是 AgentScope 最常用的容器化方案。它的核心思想是：**每个 Agent 会话的工具执行都在一个独立的 Docker 容器中进行**，与宿主机完全隔离。

具体来说，当 Agent 需要执行 bash 命令、读写文件、运行 MCP 服务器等操作时，这些操作不会直接在宿主机上运行，而是被路由到一个专属的 Docker 容器中。容器内预装了 Python、uv、ripgrep 等基础工具，并通过一个 FastAPI 网关进程管理 MCP 服务器的生命周期。宿主机通过 `aiodocker` 与容器通信，无需暴露端口到外部。

这种设计的好处是：
- **安全性**：Agent 执行的文件操作、代码运行被限制在容器内，不会影响宿主机
- **隔离性**：不同会话/租户的工作区互不干扰
- **可持久化**：可选地将宿主机目录挂载到容器的 `/workspace`，使数据在容器重启后保留
- **镜像缓存**：镜像标签基于 Dockerfile 内容的哈希，相同配置会复用已构建的镜像，避免重复构建

**前置条件：**

```bash
# 安装 Docker
# macOS: 安装 Docker Desktop
# Linux: sudo apt install docker.io

# 安装 Docker 工作区依赖
uv pip install -e ".[workspace-docker]"
```

**配置与启动：**

在 `main.py` 中将 `LocalWorkspaceManager` 替换为 `DockerWorkspaceManager`：

```python
from agentscope.app.workspace_manager import DockerWorkspaceManager

app = create_app(
    workspace_manager=DockerWorkspaceManager(
        base_image="python:3.11-slim",  # 基础镜像
        # host_workdir="/path/to/persist",  # 可选：持久化挂载目录
        # extra_pip=["some-package"],       # 可选：预装额外 Python 包
        # node_version="20",                # 可选：预装 Node.js
    ),
    # ...
)
```

Docker 工作区会在首次使用时自动构建沙箱镜像（基于 `Dockerfile.template`），后续复用已构建的镜像。

### 3.2 Kubernetes (K8s) 工作区

K8s 工作区适用于需要集群级隔离的生产场景。

**前置条件：**

```bash
# 安装 Kubernetes 集群（本地开发推荐 kind）
# https://kind.sigs.k8s.io/

# 安装 K8s 工作区依赖
uv pip install -e ".[workspace-k8s]"
```

**构建测试镜像：**

```bash
# 在仓库根目录执行
docker build \
    -f tests/docker/k8s_workspace_test.Dockerfile \
    -t agentscope-k8s-test:ci .

# 加载到 kind 集群
kind load docker-image agentscope-k8s-test:ci --name <cluster-name>
```

**配置与启动：**

```python
from agentscope.app.workspace_manager import K8sWorkspaceManager

app = create_app(
    workspace_manager=K8sWorkspaceManager(
        # K8s 集群连接配置...
    ),
    # ...
)
```

### 3.3 E2B 工作区

E2B 提供云端沙箱环境，无需本地 Docker。

**前置条件：**

```bash
uv pip install -e ".[workspace-e2b]"
export E2B_API_KEY="your-e2b-api-key"
```

**配置与启动：**

```python
from agentscope.app.workspace_manager import E2BWorkspaceManager

app = create_app(
    workspace_manager=E2BWorkspaceManager(
        # E2B 模板配置...
    ),
    # ...
)
```

### 3.4 Daytona 工作区

Daytona 提供远程开发环境管理。

**前置条件：**

```bash
uv pip install -e ".[workspace-daytona]"
export DAYTONA_API_KEY="your-daytona-api-key"
```

**配置与启动：**

```python
from agentscope.app.workspace_manager import DaytonaWorkspaceManager

app = create_app(
    workspace_manager=DaytonaWorkspaceManager(
        # Daytona 连接配置...
    ),
    # ...
)
```

### 3.5 OpenSandbox 工作区

```bash
uv pip install -e ".[workspace-opensandbox]"
```

```python
from agentscope.app.workspace_manager import OpenSandboxWorkspaceManager
```

### 3.6 沙箱容器调试技巧

**查看沙箱容器日志：**

```bash
# Docker 工作区
docker logs <container-id>

# 查看所有 agentscope 相关容器
docker ps --filter "label=agentscope"
```

**进入沙箱容器调试：**

```bash
docker exec -it <container-id> /bin/bash
```

**清理残留容器：**

```bash
# 清理所有已停止的 agentscope 容器
docker container prune --filter "label=agentscope"
```

**跳过镜像构建（加速开发迭代）：**

对于 K8s 测试镜像，预装所有依赖可跳过 bootstrap 阶段，将初始化时间从 ~5 分钟缩短到 ~10 秒。参见 `tests/docker/k8s_workspace_test.Dockerfile` 中的注释说明。

---

## 4. 可选依赖速查

根据开发需要安装对应的 extra：

| Extra | 说明 | 安装命令 |
| --- | --- | --- |
| `dev` | 完整开发环境 | `uv pip install -e ".[dev]"` |
| `models` | 全部模型提供商 | `uv pip install -e ".[models]"` |
| `service` | FastAPI 服务 | `uv pip install -e ".[service]"` |
| `storage-redis` | Redis 存储 | `uv pip install -e ".[storage-redis]"` |
| `storage-sql` | SQL 存储 | `uv pip install -e ".[storage-sql]"` |
| `workspace-docker` | Docker 工作区 | `uv pip install -e ".[workspace-docker]"` |
| `workspace-k8s` | K8s 工作区 | `uv pip install -e ".[workspace-k8s]"` |
| `workspace-e2b` | E2B 云沙箱 | `uv pip install -e ".[workspace-e2b]"` |
| `rag` | RAG 文档解析 | `uv pip install -e ".[rag]"` |
| `vdb-qdrant` | Qdrant 向量库 | `uv pip install -e ".[vdb-qdrant]"` |

完整列表参见 `pyproject.toml` 中的 `[project.optional-dependencies]`。

---

## 5. 常见问题

### Q: 安装依赖时出现 `ImportError`？

AgentScope 遵循惰性导入原则，可选依赖未安装时不会在 `import agentscope` 阶段报错，只在实际使用对应功能时抛出。请确认安装了所需的 extra。

### Q: pre-commit 检查失败怎么办？

大部分格式问题会被自动修复。运行 `pre-commit run --all-files` 查看并修复剩余问题后重新 commit。

### Q: 测试依赖 Redis 但本地未安装？

可以使用 Docker 快速启动 Redis：`docker run --rm -d -p 6379:6379 redis:7`。

依赖 Redis 的测试在未安装 `storage-redis` extra 时会自动 skip，不会报错。

### Q: 如何切换工作区后端？

在 `create_app()` 中传入不同的 `WorkspaceManager` 实现即可，所有后端共享相同的接口。详见上方 §3 各后端说明。
