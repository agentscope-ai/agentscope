# AgentScope 示例项目启动指南

## 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.12+ | 使用 `uv` 管理，虚拟环境 `.venv/` |
| Node.js | 20+ | 当前 v26.1.0 |
| pnpm | - | 全局安装，PowerShell 中需用 `cmd /c` 包装 |
| Redis | 5.0.14.1 (Win x64) | `C:\googoe\Redis-x64-5.0.14.1\` |

## 一键启动（Windows）

双击 `start_services.bat`，自动启动所有服务。

## 手动启动

### 1. 启动 Redis

```powershell
Start-Process -FilePath "C:\googoe\Redis-x64-5.0.14.1\redis-server.exe" -WindowStyle Hidden
```

验证：
```powershell
uv run python -c "import redis; r=redis.Redis(host='127.0.0.1', port=6379, protocol=2); print(r.ping())"
# → PONG: True
```

### 2. 启动 Agent Service（Python FastAPI）

```powershell
cd examples/agent_service
uv run python main.py
# → http://localhost:8000
```

### 3. 安装 Web UI 依赖（首次）

```powershell
cd examples/web_ui
pnpm install
```

### 4. 启动 Web UI 后端（Express）

```powershell
cd examples/web_ui/backend
cmd /c "npx nodemon --watch src --ext ts --exec npx ts-node src/index.ts"
# → http://localhost:3000
```

### 5. 启动 Web UI 前端（Vite + React）

```powershell
cd examples/web_ui/frontend
cmd /c "npx vite --host 0.0.0.0"
# → http://localhost:5173
```

## 停止服务

```powershell
# 停止所有 Node 进程（WebUI 前后端 + Agent Service）
taskkill /f /im node.exe

# 停止 Redis
taskkill /f /im redis-server.exe
```

## 服务架构

```
┌──────────────────────────────────────┐
│  浏览器 http://localhost:5173         │
│  React + Vite (WebUI 前端)            │
└──────────────┬───────────────────────┘
               │ HTTP
┌──────────────▼───────────────────────┐
│  http://localhost:3000               │
│  Express (WebUI 后端)                 │
└──────────────┬───────────────────────┘
               │ HTTP
┌──────────────▼───────────────────────┐
│  http://localhost:8000               │
│  FastAPI (Agent Service)             │
│  ├─ RedisStorage (127.0.0.1:6379)    │
│  ├─ QdrantStore (:memory:)           │
│  ├─ InMemoryMessageBus               │
│  └─ LocalWorkspaceManager            │
└──────────────────────────────────────┘
```

## 使用指南

浏览器打开 `http://localhost:5173`

### 第一步：初始配置

首次访问会自动弹出设置页，填写：
- **Server URL**：`http://localhost:8000`
- **Username**：任意名字

> 若未弹出，点左侧底部 ⚙️ 设置图标手动配置

### 第二步：添加模型凭证

左侧 🔑 **Credential（凭证）**→ 创建凭证 → 填写 LLM API Key

支持 OpenAI、DashScope、本地模型等，Agent 需要凭证才能调用模型。

### 第三步：创建 Agent 开始对话

左侧 💬 **Chat（聊天）**→ 点 Agent 下拉旁 `+` 创建 Agent → 选择模型 → 开始对话

### 四个核心功能

| 图标 | 页面 | 作用 |
|------|------|------|
| 💬 | Chat 聊天 | 创建 Agent、多轮对话、Team 团队协作 |
| 📅 | Schedule 定时任务 | 基于 cron 表达式编排自动化任务 |
| 🔑 | Credential 凭证 | 管理 LLM/TTS 模型 API Key |
| 📚 | Knowledge 知识库 | RAG 文档上传、向量检索测试 |

### 开发者调试页面

FastAPI 自带交互式 API 文档，可直接在线调试所有接口：

| 地址 | 页面 | 用途 |
|------|------|------|
| `http://localhost:8000/docs` | Swagger UI | 可视化调试 API，在线发送请求 |
| `http://localhost:8000/redoc` | ReDoc | API 文档阅读 |

相比前端聊天界面，Swagger 页面更适合开发者直接测试接口、查看请求/响应格式。

### 注意事项

- `http://localhost:3000` 是纯 API 服务，不提供页面（只有 `/api/health` 路由），应通过 `:5173` 前端使用
- 前端通过 Vite proxy 将 `/api` 请求转发到 `:3000` 后端，后端再转发到 `:8000` Agent Service
- Redis 数据文件在 `C:\googoe\Redis-x64-5.0.14.1\dump.rdb`，备份项目根目录旧 `dump.rdb` 以备恢复
