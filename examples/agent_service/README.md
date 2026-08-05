# BocomADP

基于 AgentScope 2.0 `create_app` 搭建的可扩展 Agent 服务骨架。在官方 `agent_service` 示例之上，构建了完整的模块化扩展架构，并接入企业扩展案例 `bankcomm_adp`。

## 核心特性

- **8 阶段请求编排**（`runtime/`）：PRE_DISPATCH → POST_DISPATCH → PRE_AGENT_BUILD → POST_AGENT_BUILD → PRE_EXECUTE → POST_RESPONSE → ON_ERROR → FINALLY，每阶段可插拔钩子
- **SSE 事件信封**（`runtime/envelope.py`）：流式对话状态机，心跳保活
- **多模型路由**（`providers/`）：ProviderManager 注册 / 切换 / 列表，配合 `/api/models` 路由
- **自动注册机制**：工具、中间件、MCP 三类组件均支持 `builtin + custom/` 自动扫描，新增组件只需放文件，重启即生效，无需改 `main.py`
- **日志三件套**（`logging/`）：ContextVar trace_id 关联、TraceContextFilter、JsonTraceFormatter、ASGI TraceMiddleware
- **自定义 ASGI 中间件**（`middleware/`）：访问日志、全局错误处理
- **自定义路由**（`routers/`）：健康检查、SSE 对话、Agent 管理、模型列表、统计示例
- **子智能体模板**（`agents/`）：researcher / coder，可通过 `custom_subagent_templates` 扩展
- **企业扩展案例**（`bankcomm_adp/`）：审计留痕、数据脱敏（DLP）、企业内部工具、平台健康检查

## 目录结构

```
examples/agent_service/
├── main.py                              # 入口：组装 create_app + 框架模块 + 中间件 + 路由
├── README.md
├── Dockerfile
├── .env.example
│
├── bocomadp/                            # 核心扩展包
│   ├── __init__.py
│   ├── config.py                        # 配置管理（pydantic-settings，含 QwenPaw 移植占位）
│   │
│   ├── logging/                         # 日志三件套
│   │   ├── __init__.py
│   │   ├── trace_context.py             # ContextVar trace_id 生成/规范化
│   │   ├── logging_config.py            # TraceContextFilter + JsonTraceFormatter
│   │   └── trace_middleware.py          # ASGI TraceMiddleware (X-Trace-Id)
│   │
│   ├── runtime/                         # 8 阶段请求编排引擎
│   │   ├── __init__.py                  # 导出 Runtime, HookRegistry
│   │   ├── phases.py                    # 8 阶段枚举 (Phase)
│   │   ├── hooks.py                     # 生命周期钩子注册表 (HookRegistry)
│   │   ├── envelope.py                  # SSE 事件信封状态机
│   │   ├── executor.py                  # 心跳包裹的 Agent 执行器
│   │   ├── builder.py                   # 每请求动态组装 Agent
│   │   └── runtime.py                   # 8 阶段编排器主入口
│   │
│   ├── providers/                       # 多模型路由
│   │   ├── __init__.py
│   │   └── provider_manager.py          # ProviderManager (注册/切换/列表)
│   │
│   ├── tools/                           # 自定义工具开发目录
│   │   ├── __init__.py
│   │   ├── registry.py                  # ToolRegistry (自动扫描 @tool)
│   │   ├── builtin_tools.py             # 内置示例工具
│   │   └── custom/                      # 你的产品工具放这里
│   │       └── __init__.py
│   │
│   ├── middleware/                      # 中间件开发目录
│   │   ├── __init__.py                  # 导出 MiddlewareRegistry
│   │   ├── registry.py                  # Agent 中间件注册表 (自动扫描)
│   │   ├── agent_middleware.py          # 内置示例: LoggingMiddleware
│   │   ├── error_handler.py             # ASGI 错误处理
│   │   ├── request_log.py               # ASGI 访问日志
│   │   └── custom/                       # 你的产品中间件放这里
│   │       └── __init__.py
│   │
│   ├── mcp/                             # MCP 连接器开发目录
│   │   ├── __init__.py
│   │   ├── registry.py                  # McpRegistry (自动扫描 MCPClient)
│   │   ├── builtin_mcps.py              # 内置 MCP 示例
│   │   └── custom/                      # 你的产品 MCP 放这里
│   │       └── __init__.py
│   │
│   ├── routers/                         # 自定义路由开发目录
│   │   ├── __init__.py
│   │   ├── chat_sse.py                  # SSE 流式对话 (/api/chat/run + /stop)
│   │   ├── agent_manage.py              # 多 Agent CRUD (/api/agents)
│   │   ├── models.py                    # 模型列表 + 切换 (/api/models)
│   │   ├── health.py                    # 健康检查 (/healthz /readyz)
│   │   ├── stats.py                     # 统计示例 (/stats/ping /stats/storage)
│   │   └── custom/                      # 你的产品路由放这里
│   │       └── __init__.py
│   │
│   └── agents/
│       ├── __init__.py
│       └── templates.py                 # subagent 模板 (researcher/coder)
│
├── bankcomm_adp/                        # 企业扩展案例包
│   ├── __init__.py
│   ├── _version.py
│   ├── config.py                        # ADP_ 前缀环境变量配置
│   ├── middlewares/
│   │   ├── __init__.py                  # 导出 build_enterprise_middlewares
│   │   ├── audit.py                     # 审计留痕中间件
│   │   ├── dlp.py                       # 数据脱敏中间件（手机号/身份证/银行卡号掩码）
│   │   └── factory.py                   # 中间件工厂 (extra_agent_middlewares)
│   ├── routers/
│   │   ├── __init__.py                  # 导出 health_router
│   │   └── health.py                    # GET /platform/health
│   └── tools/
│       ├── __init__.py                  # 导出 build_enterprise_tools
│       ├── enterprise.py               # HR / 内部文档库 / ITSM 工单占位
│       └── placeholder.py
│
└── tests/
    ├── __init__.py
    ├── test_logging.py                  # 日志三件套单元测试
    └── test_registry_scan.py            # 三大注册表自动扫描测试
```

## 快速开始

### 1. 安装依赖

```bash
cd agentscope
uv pip install -e [full]
```

### 2. 启动 Redis

```bash
# Docker
docker run --rm -p 6379:6379 redis:7

# macOS
brew install redis && brew services start redis

# Linux
sudo apt install redis-server && sudo systemctl start redis-server
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

### 5. 启动 Web UI

```bash
cd examples/web_ui/
pnpm install   # 或 npm install
pnpm dev
```

在 Web UI 中设置 API 端点为 `http://localhost:8000` 即可开始体验。

### 6. 运行测试

```bash
cd agentscope
pytest examples/agent_service/tests/ -v
```

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat/run` | POST | SSE 流式对话（Runtime 8 阶段编排） |
| `/api/chat/stop` | POST | 停止对话（协作式取消） |
| `/api/agents` | GET / POST | Agent 列表 / 创建 |
| `/api/agents/{id}` | GET / PUT / DELETE | Agent 详情 / 更新 / 删除 |
| `/api/models` | GET | 模型列表 |
| `/api/models/active` | POST | 切换活跃模型 |
| `/healthz` | GET | 存活检查 |
| `/readyz` | GET | 就绪检查 |
| `/stats/ping` | GET | Ping |
| `/stats/storage` | GET | 存储状态 |
| `/platform/health` | GET | 平台健康检查（bankcomm_adp 扩展） |

> 上述路由叠加在 AgentScope `create_app` 自动注册的 12 个内置路由之上。

## 配置项

### 核心配置（`bocomadp/config.py`，前缀 `BOCOMADP_`）

使用 `pydantic-settings`，嵌套字段以 `__` 分隔：

```bash
BOCOMADP_LOG_LEVEL=debug
BOCOMADP_LOGGING__ENHANCE__ENABLED=true
BOCOMADP_LOGGING__ENHANCE__FORMAT=json     # text | json
BOCOMADP_SERVICE__PORT=9000
BOCOMADP_REDIS__HOST=redis.local
BOCOMADP_TOOLS__LOAD_CUSTOM=true
BOCOMADP_MIDDLEWARES__LOAD_CUSTOM=true
BOCOMADP_MCP__LOAD_CUSTOM=true
BOCOMADP_RUNTIME__HEARTBEAT_INTERVAL_SECONDS=15.0
```

### 企业扩展配置（`bankcomm_adp/config.py`，前缀 `ADP_`）

```bash
ADP_APP_NAME="交通银行智能体平台"     # Web UI 展示的应用名
ADP_AUDIT_ENABLED=true               # 审计留痕开关
ADP_AUDIT_LOG_PATH=./logs/audit.jsonl
ADP_DLP_ENABLED=true                 # 数据脱敏开关
# ADP_WORKSPACE_DIR=./workspaces     # 工作区目录
```

### 日志配置

```bash
LOG_LEVEL=info
LOG_ENHANCE_ENABLED=true
LOG_ENHANCE_FORMAT=text               # text | json
```

切换为 JSON 格式后，每条日志变为：

```json
{"timestamp":"2026-08-05T10:00:00+00:00","logger":"bocomadp.main","level":"INFO","trace_id":"a1b2...","message":"..."}
```

## 架构概览

### main.py 组装流程

`main.py` 是唯一的装配点，按以下顺序组装所有关注点：

1. **配置加载** — `load_config()` 读取环境变量 / `.env`
2. **日志初始化** — `configure_logging(config)` 一次性配置
3. **框架模块初始化** — ToolRegistry、MiddlewareRegistry、McpRegistry、ProviderManager、HookRegistry、MultiAgentManager、Runtime
4. **构建 App** — `create_app()` 自动注册 12 个内置路由
5. **注入 ASGI 中间件** — TraceMiddleware → AccessLogMiddleware → ErrorHandlingMiddleware → CORSMiddleware
6. **挂载自定义路由** — health、stats、chat_sse、agent_manage、models、platform_health
7. **注册子智能体模板** — `custom_subagent_templates`
8. **企业扩展接入** — `extra_agent_middlewares`（审计 + DLP）、`extra_agent_tools`（企业工具）

### 中间件执行顺序

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

### 8 阶段运行时编排

```
PRE_DISPATCH      — 请求规范化，slash 命令分派前
POST_DISPATCH     — slash 命令分派完成（无匹配时）
PRE_AGENT_BUILD   — session 加载等构建前准备
  ── [固定步骤] AgentBuilder 动态组装 Agent ──
POST_AGENT_BUILD  — Agent 构造完成，注入模式上下文
PRE_EXECUTE       — bootstrap / prompt 刷新 / env 堆栈推入
  ── [固定步骤] AgentExecutor 执行回复流 ──
POST_RESPONSE     — session.save / cron 触发回写
ON_ERROR           — 异常规范化，取消信封
FINALLY           — 幂等清理（关闭 MCP，重置 ContextVars）
```

> Runtime 钩子层与 `agentscope.middleware`（包裹单个 Agent 回复循环的中间件）正交，互不干扰。

## 自定义开发

### 自动注册机制

三类组件采用统一的 `builtin + custom/` 自动扫描模式：

| 组件类型 | 注册表 | builtin 扫描 | custom 扫描 | 判定标记 |
|---------|--------|-------------|------------|---------|
| 工具 | `ToolRegistry` | `tools/builtin_tools.py` | `tools/custom/*.py` | `_is_tool = True` |
| Agent 中间件 | `MiddlewareRegistry` | `middleware/agent_middleware.py` | `middleware/custom/*.py` | `_is_agent_middleware = True` |
| MCP 连接器 | `McpRegistry` | `mcp/builtin_mcps.py` | `mcp/custom/*.py` | duck-type（有 `name` + `mcp_config`） |

**核心原则**：在对应 `custom/` 目录下新建 `.py` 文件，定义并导出组件实例，重启后自动注册，无需修改 `main.py`。

### 加一个自定义工具

```python
# bocomadp/tools/custom/my_tool.py
from agentscope.tool import tool

@tool
def my_tool(query: str) -> str:
    """工具描述。"""
    return f"result: {query}"
```

重启即生效，`ToolRegistry` 自动扫描注册。

### 加一个 Agent 级中间件

```python
# bocomadp/middleware/custom/audit_mw.py
from agentscope.middleware import MiddlewareBase
from agentscope.message import Msg

class AuditMiddleware(MiddlewareBase):
    async def pre_process(self, msg: Msg) -> Msg:
        # 每轮对话前执行：审计、参数注入、权限校验...
        return msg

    async def post_process(self, msg: Msg) -> Msg:
        # 每轮对话后执行
        return msg

# 模块级实例导出，自动注册
audit_mw = AuditMiddleware()
```

### 加一个 MCP 连接器

```python
# bocomadp/mcp/custom/amap.py
from agentscope.mcp import MCPClient, HttpMCPConfig

amap = MCPClient(
    name="amap",
    mcp_config=HttpMCPConfig(url="https://mcp.amap.com/mcp?key=xxx"),
    is_stateful=False,
)
```

### 配置开关

三个注册表都有独立的 `enabled` + `load_custom` 开关，默认全开，可通过环境变量关闭：

| 配置项 | 环境变量 | 默认 | 说明 |
|--------|---------|------|------|
| `tools.enabled` | `BOCOMADP_TOOLS__ENABLED` | `true` | 是否加载内置工具 |
| `tools.load_custom` | `BOCOMADP_TOOLS__LOAD_CUSTOM` | `true` | 是否扫描 `tools/custom/` |
| `middlewares.enabled` | `BOCOMADP_MIDDLEWARES__ENABLED` | `true` | 是否加载内置中间件 |
| `middlewares.load_custom` | `BOCOMADP_MIDDLEWARES__LOAD_CUSTOM` | `true` | 是否扫描 `middleware/custom/` |
| `mcp.enabled` | `BOCOMADP_MCP__ENABLED` | `true` | 是否加载内置 MCP |
| `mcp.load_custom` | `BOCOMADP_MCP__LOAD_CUSTOM` | `true` | 是否扫描 `mcp/custom/` |

### 扫描机制详解

三个注册表使用统一的 **"builtin + custom/ 双路扫描"** 模式，内部实现对称：

```
load_builtin()  →  import builtin_*.py  →  _scan_module(mod)  →  register(每个匹配项)
load_custom()   →  pkgutil.walk_packages(custom/)  →  逐模块 _scan_module  →  register
```

**各注册表的判定条件**：

| 注册表 | 判定函数 | 条件 | 为什么这样判 |
|--------|---------|------|-------------|
| `ToolRegistry` | `_is_tool` 属性 | `callable(obj) and getattr(obj, "_is_tool", False)` | `@tool` 装饰器自动打标记，纯函数无需实例化 |
| `MiddlewareRegistry` | `_is_agent_middleware` 属性 | `getattr(obj, "_is_agent_middleware", False) and not isinstance(obj, type)` | 中间件需带状态/参数，扫实例不扫类；基类统一打标记 |
| `McpRegistry` | duck-type | `hasattr(obj, "name") and hasattr(obj, "mcp_config") and not isinstance(obj, type)` | `MCPClient` 不自带标记，用 duck-type 避免改 agentscope 源码 |

**设计要点**：

- **幂等注册**：三个注册表都按名称去重（Tool/MCP 按 `name` 属性，Middleware 按类名），重复加载不会重复注册
- **容错**：单个 custom 模块 import 失败只 warning 跳过，不影响其他模块
- **无 agentscope 可运行**：tool 和 middleware 的 fallback 装饰器/基类仍打标记，语法检查环境也能测扫描逻辑

### 加一个 ASGI 中间件

在 `bocomadp/middleware/` 下新建文件实现纯 ASGI 类，然后在 `main.py` 的 `build_asgi_middlewares()` 中注册：

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

```python
# main.py — build_asgi_middlewares()
from bocomadp.middleware.my_mw import MyMiddleware
from fastapi.middleware import Middleware

def build_asgi_middlewares(trace_enabled: bool) -> list[Middleware]:
    return [
        Middleware(TraceMiddleware, enabled=trace_enabled),      # 最内层
        Middleware(AccessLogMiddleware, skip_paths=("/healthz",)),
        Middleware(ErrorHandlingMiddleware),
        Middleware(MyMiddleware, enabled=True),                  # ← 新加
        Middleware(CORSMiddleware, allow_origins=["*"], ...),     # 最外层
    ]
```

**顺序原则**：`TraceMiddleware` 必须在内层（先绑定 trace_id），`ErrorHandlingMiddleware` 必须在最外层（兜底所有错误）。

### 加一个路由

```python
# bocomadp/routers/custom/orders.py
from fastapi import APIRouter
from bocomadp.logging.trace_context import get_current_trace_id

orders_router = APIRouter(prefix="/orders", tags=["orders"])

@orders_router.get("/{order_id}")
async def get_order(order_id: str) -> dict:
    return {"order_id": order_id, "trace_id": get_current_trace_id()}
```

```python
# main.py
from bocomadp.routers.custom.orders import orders_router
app.include_router(orders_router)
```

### 加一个子智能体模板

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

### 加一个 Runtime 钩子

用 `@hook_registry.register(Phase.XXX)` 在 8 个阶段中的任意阶段插入逻辑：

```python
from bocomadp.runtime import HookRegistry, Phase

@hook_registry.register(Phase.PRE_EXECUTE)
async def my_hook(ctx):
    # 在 Agent 执行前注入上下文 / 刷新 prompt
    ...

@hook_registry.register(Phase.ON_ERROR)
async def error_hook(ctx):
    # 异常时记录 / 告警
    ...
```

## 企业扩展（`bankcomm_adp`）

本示例在官方入口之上叠加了一个企业扩展包，承载企业内部智能体平台所需的基础能力，同时保持与官方 `web_ui` 和 `Docker-agentscope` 启动脚本完全兼容：

| 能力 | 位置 | 说明 |
|---|---|---|
| 审计留痕 | `bankcomm_adp/middlewares/audit.py` | 记录每次 agent 调用（谁、何时、用了哪些工具、输出摘要），以 JSONL 写入 `ADP_AUDIT_LOG_PATH` |
| 数据脱敏（DLP） | `bankcomm_adp/middlewares/dlp.py` | 对发往模型的输入做手机号 / 身份证 / 银行卡号掩码 |
| 企业内部工具 | `bankcomm_adp/tools/` | HR / 内部文档库 / ITSM 工单占位实现，可替换为真实系统调用 |
| 平台健康检查 | `bankcomm_adp/routers/health.py` | `GET /platform/health` 返回服务状态 |

认证保持官方默认的 `X-User-ID` 头方式，前端 `examples/web_ui` 无需任何改动。

## 从 QwenPaw 移植模块

`config.py` 里预留了 QwenPaw 模块的移植占位（默认 `enabled=False`）：

| 模块 | 体积 | 依赖 | 何时移植 |
|---|---|---|---|
| `utils/logging.py` (rotation) | 342 行 | 独立 | 早期，生产部署前 |
| `token_usage/` | 1k 行 | agentscope.model | 需要用量统计时 |
| `providers/xxx` | 单文件 | agentscope.model | 需要该 provider 时 |
| `hooks/error_hook` | 小 | agentscope Hook | 需要错误拦截时 |
| `governance/` | 5k 行 | 重 | 需要 doom-loop 防护时 |
| `checkpoints/` | 4.4k 行 | 中 | 需要会话回放/分支时 |

**原则**：按需单点移植，不要整体 fork。每次移植一个模块，确保测试通过再下一个。

### 移植步骤

1. 从 QwenPaw 复制单个模块文件到 `bocomadp/` 对应目录
2. 调整 import（`from agentscope.model import ChatModelBase` 不变，QwenPaw 内部相对 import 改成本包路径）
3. 在 `config.py` 把对应 `enabled` 改为 `True`
4. 在 `main.py` 按 `config.xxx.enabled` 条件加载

## 用 Docker 启动

```bash
# 构建镜像（构建上下文为仓库根目录）
docker build -f examples/agent_service/Dockerfile -t bocomadp-service . --network=host

# 或使用 Docker Compose
cd Docker-agentscope
docker compose up -d
```

`Docker-agentscope/docker-compose.yml` 已适配企业扩展：它会将 `examples/agent_service/bankcomm_adp` 一并挂载进容器，`main.py` 可直接 import。参考 `Docker-agentscope/` 下的 `README` 与 `compose-images.sh` 构建并启动。

Dockerfile 特性：
- 基于 `python:3.14-bookworm`，使用 `uv` 管理依赖
- 分层构建：先装依赖（缓存层），再装项目本身
- 外层包 `bocomadp/` 和 `bankcomm_adp/` 均复制进 `/app`，`PYTHONPATH=/app` 保证可直接 import
