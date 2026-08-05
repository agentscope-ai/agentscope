# BocomADP

基于 AgentScope 2.0 `create_app` 搭建的外层产品骨架。在官方 example 基础上集成了：

- **日志三件套**（请求级 trace_id 关联，从 deer-flow-2.0 适配）
- **自定义 ASGI 中间件**（访问日志、全局错误处理）
- **自定义路由**（健康检查、业务示例）
- **配置管理**（pydantic-settings，预留 QwenPaw 模块移植占位）
- **子智能体模板**（researcher / coder）

## 目录结构

```
examples/agent_service/
├── main.py                          # 入口：组装 create_app + 中间件 + 路由
├── README.md
├── Dockerfile
├── .env.example
├── bocomadp/                # 外层产品包
│   ├── __init__.py
│   ├── config.py                    # 配置管理（含 QwenPaw 移植占位）
│   ├── logging/                     # 日志三件套
│   │   ├── trace_context.py         # ContextVar trace_id 生成/规范化
│   │   ├── logging_config.py        # TraceContextFilter + JsonTraceFormatter
│   │   └── trace_middleware.py      # ASGI TraceMiddleware (X-Trace-Id)
│   ├── middleware/                  # 自定义 ASGI 中间件
│   │   ├── request_log.py           # 访问日志（method/path/status/耗时）
│   │   └── error_handler.py         # 全局错误兜底（带 trace_id 的 500）
│   ├── routers/                     # 自定义路由
│   │   ├── health.py                # /healthz /readyz
│   │   └── stats.py                 # /stats/ping /stats/storage 示例
│   └── agents/
│       └── templates.py             # subagent 模板 (researcher/coder)
└── tests/
    └── test_logging.py              # 日志三件套单元测试
```

## 快速开始

### 1. 安装依赖

```bash
cd agentscope
uv pip install -e [full]
```

### 2. 启动 Redis

```bash
docker run --rm -p 6379:6379 redis:7
```

### 3. 配置环境变量

```bash
cd examples/agent_service
cp .env.example .env
# 按需编辑 .env
```

### 4. 启动服务

```bash
cd examples/agent_service
python main.py
# 或
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. 运行测试

```bash
cd agentscope
pytest examples/agent_service/tests/ -v
```

## 如何加东西

### 加一个 ASGI 中间件

1. 在 `bocomadp/middleware/` 下新建文件，实现纯 ASGI 类：

```python
# bocomadp/middleware/my_mw.py
class MyMiddleware:
    def __init__(self, app, *, enabled=True):
        self.app = app
        self.enabled = enabled

    async def __call__(self, scope, receive, send):
        if not self.enabled or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # ... 你的逻辑
        await self.app(scope, receive, send)
```

2. 在 `main.py` 的 `extra_middlewares` 列表里注册，注意顺序（最后注册的最外层）：

```python
from bocomadp.middleware.my_mw import MyMiddleware

extra_middlewares=[
    Middleware(TraceMiddleware, enabled=trace_enabled),      # 最内层
    Middleware(AccessLogMiddleware, skip_paths=("/healthz",)),
    Middleware(ErrorHandlingMiddleware),
    Middleware(MyMiddleware, enabled=True),                 # ← 新加
    Middleware(CORSMiddleware, allow_origins=["*"], ...),   # 最外层
],
```

**顺序原则**：`TraceMiddleware` 必须在内层（先绑定 trace_id），`ErrorHandlingMiddleware` 必须在最外层（兜底所有错误）。新中间件按依赖关系插入中间。

### 加一个 Agent 级中间件（每轮对话执行）

Agent 级中间件走 `extra_agent_middlewares`（不是 ASGI 中间件），是 `(user_id, agent_id, session_id) -> list[MiddlewareBase]` 的异步工厂：

```python
from agentscope.middleware import MiddlewareBase
from agentscope.message import Msg

class AuditMiddleware(MiddlewareBase):
    async def pre_process(self, msg: Msg) -> Msg:
        # 每轮对话前执行：审计、参数注入、权限校验...
        return msg

    async def post_process(self, msg: Msg) -> Msg:
        # 每轮对话后执行
        return msg

async def audit_factory(user_id, agent_id, session_id):
    return [AuditMiddleware()]

app = create_app(
    ...,
    extra_agent_middlewares=audit_factory,
)
```

### 加一个路由

1. 在 `bocomadp/routers/` 下新建文件：

```python
# bocomadp/routers/orders.py
from fastapi import APIRouter, Request
from bocomadp.logging.trace_context import get_current_trace_id

orders_router = APIRouter(prefix="/orders", tags=["orders"])

@orders_router.get("/{order_id}")
async def get_order(order_id: str) -> dict:
    return {"order_id": order_id, "trace_id": get_current_trace_id()}
```

2. 在 `main.py` 挂载：

```python
from bocomadp.routers.orders import orders_router
app.include_router(orders_router)
```

### 加一个 subagent 模板

在 `bocomadp/agents/templates.py` 的 `load_subagent_templates()` 里追加：

```python
def _planner_template() -> SubAgentTemplate:
    return SubAgentTemplate(
        type="planner",
        description="...",
        system_prompt_template="You are {member_name}...",
        permission_context=PermissionContext(mode=PermissionMode.EXPLORE),
    )

def load_subagent_templates() -> list[SubAgentTemplate]:
    return [_researcher_template(), _coder_template(), _planner_template()]
```

### 切换日志格式为 JSON

编辑 `.env`：

```
LOG_ENHANCE_ENABLED=true
LOG_ENHANCE_FORMAT=json
```

重启服务。每条日志变为：

```json
{"timestamp":"2026-08-05T10:00:00+00:00","logger":"bocomadp.main","level":"INFO","trace_id":"a1b2...","message":"..."}
```

## 中间件执行顺序

```
请求 → CORSMiddleware (最外层)
      → ErrorHandlingMiddleware
      → AccessLogMiddleware
      → TraceMiddleware (最内层，绑定 trace_id)
      → FastAPI 路由 (12 个内置 + 自定义)
```

- `TraceMiddleware` 在最内层，最先执行，绑定 `X-Trace-Id` 到 ContextVar
- `AccessLogMiddleware` 依赖 trace_id，所以在 TraceMiddleware 外层
- `ErrorHandlingMiddleware` 在最外层，兜底所有错误（包括 AccessLog 的）

## 从 QwenPaw 移植模块

`config.py` 里预留了 QwenPaw 模块的移植占位（默认 `enabled=False`）。移植步骤：

### 1. 单点移植某个 provider

```bash
# 从 QwenPaw 复制单个 provider 文件
cp ../../QwenPaw/src/qwenpaw/providers/ollama_provider.py \
   bocomadp/providers/
```

调整 import（`from agentscope.model import ChatModelBase` 不变，QwenPaw 内部相对 import 改成本包路径）。在 `config.py` 把 `ProviderConfig.enabled` 改为 `True`，在 `main.py` 按 `config.providers.enabled` 条件加载。

### 2. 移植日志 rotation（推荐早期移植）

QwenPaw 的 `utils/logging.py` 提供了 `_SafeRotatingFileHandler`（Windows 容错）和 `add_project_file_handler`（幂等挂载），比本骨架的纯 stderr 输出更适合生产。复制过来后在 `main.py` 加：

```python
from bocomadp.logging.file_handler import add_project_file_handler
add_project_file_handler(Path("./logs/my-agent-service.log"))
```

### 3. 移植优先级建议

| 模块 | 体积 | 依赖 | 何时移植 |
|---|---|---|---|
| `utils/logging.py` (rotation) | 342 行 | 独立 | 早期，生产部署前 |
| `token_usage/` | 1k 行 | agentscope.model | 需要用量统计时 |
| `providers/xxx` | 单文件 | agentscope.model | 需要该 provider 时 |
| `hooks/error_hook` | 小 | agentscope Hook | 需要错误拦截时 |
| `governance/` | 5k 行 | 重 | 需要 doom-loop 防护时 |
| `checkpoints/` | 4.4k 行 | 中 | 需要会话回放/分支时 |

**原则**：按需单点移植，不要整体 fork。每次移植一个模块，确保测试通过再下一个。
# Agent Service

Agent service is a FastAPI-based, multi-tenant and multi-session service built with AgentScope 2.0.

This example demonstrates

- how to set up the agent service with Redis storage, and
- how to launch the service and its companion Web UI

Details about the agent service please refer to the [tutorial](https://docs.agentscope.io/latest/en/deploy/agent-service).

## Prerequisites

- Python ≥ 3.11
- Node.js ≥ 20 with `npx`
- [optional] Gaode/AMap API key in `AMAP_API_KEY` (for the `amap` MCP)

## Quickstart

Install AgentScope from PyPI or source:

```bash
uv pip install agentscope[full]
# or
# uv pip install -e [full]
```

Install Redis and start it as backend storage:

```bash
# macOS (Homebrew)
brew install redis
brew services start redis

# Linux (systemd)
sudo apt install redis-server
sudo systemctl start redis-server

# Docker (cross-platform)
docker run --rm -p 6379:6379 redis:7
```

Start the agent service:

```bash
cd examples/agent_service

python main.py
```

Launch the Web UI in a separate terminal to experience a chat-style interface:

```bash
cd examples/web_ui/

pnpm install
# or npm install

# Run in dev mode
pnpm dev
```

After that, you can set the API endpoint `http://localhost:8000` in the Web UI and start experiencing the agent service.

<img src="https://gw.alicdn.com/imgextra/i2/O1CN01Phmg1G1brIVC8WXyU_!!6000000003518-2-tps-2938-1736.png" alt="Web UI Screenshot" width="100%">

## What Next

- You can customize the service in `main.py` by adding your own MCPs, middlewares, or workspace manager implementations.

- Experience the agent service, including
    - human-in-the-loop interactions & permission system
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01vGGiBw20agWwpzmjy_!!6000000006866-2-tps-2934-1732.png" alt="Permission System" width="100%">

    - schedule tasks
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01Xi3Qw71E2haKKu4z0_!!6000000000294-2-tps-2932-1738.png" alt="Schedule Tasks" width="100%">

    - and more! (stay tuned for future updates)