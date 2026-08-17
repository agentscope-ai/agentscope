# BocomADP

基于 AgentScope 2.0 `create_app` 搭建的可扩展 Agent 服务骨架。在官方 `agent_service` 示例之上，构建了完整的模块化扩展架构，企业扩展能力已全部整合进 `bocomadp`。

> 文档索引（本目录 `docs/`）：
>
> - [API接口文档.md](./API接口文档.md) —— 全量 curl 速查（网关 / 直连两种调用方式）
> - [api.md](./api.md) —— 智能体工具白名单 + 会话 Token 用量接口
> - [custom_params.md](./custom_params.md) —— custom_params 机制教学文档（请求级运行时配置）
> - [config_load_design.md](./config_load_design.md) —— 配置加载链路设计
> - [examples_架构分析.md](./examples_架构分析.md) —— examples/ 整体架构分析

## 核心特性

- **DeerFlow 风格 SSE**（`deerflow/`）：`/api/threads/{tid}/runs/*` 四端点（stream / wait / join / cancel），事件/数据/id 帧 + 心跳 + Last-Event-ID 断线续传，执行引擎复用原生 `ChatService`
- **SSE 协议与翻译**（`deerflow/protocol.py` + `formatter.py`）：AgentScope 事件 → deer-flow 事件（metadata/messages/custom/error/end）
- **请求级运行时配置**（`custom_params`）：空间码强制覆盖 / custom_prompt 整体替换 / 检索开关 / 认证方案（guwp/jrt/okic/muwp），随 run 请求注入并落盘回退，详见 [custom_params.md](./custom_params.md)
- **会话与凭证自动供给**：`_ensure_session` 懒建会话；模型凭证按 `deerflow-<user_id>-<provider_id>` 幂等写入 credential 存储（id 带 user_id 维度，避免 SQL 存储全局主键跨用户冲突）
- **多模型路由**（`providers/`）：ProviderManager 注册 / 切换 / 列表，配合 `/api/models` 路由
- **自动注册机制**：工具、中间件、MCP 三类组件均支持 `builtin + custom/` 自动扫描，新增组件只需放文件，重启即生效，无需改 `main.py`
- **日志三件套**（`logging/`）：ContextVar trace_id 关联、TraceContextFilter、JsonTraceFormatter、ASGI TraceMiddleware
- **自定义 ASGI 中间件**（`middleware/`）：访问日志、全局错误处理
- **自定义路由**（`routers/`）：健康检查、Agent 工具白名单、模型列表、会话用量、上传、统计等（SSE 对话见 deerflow/）
- **子智能体模板**（`agents/`）：researcher / coder，可通过 `custom_subagent_templates` 扩展
- **企业扩展能力**（bocomadp）：审计留痕、企业内部工具、平台健康检查

## 目录结构

```
examples/agent_service/
├── main.py                              # 入口：组装 create_app + 框架模块 + 中间件 + 路由
├── config.yaml                          # 单一配置文件（模型 + 企业扩展共享）
├── .env                                 # 环境变量（可选，自动加载）
├── Dockerfile
├── docs/                                # 项目文档（README / api / custom_params / 配置与架构）
│
├── bocomadp/                            # 核心扩展包（含企业扩展能力）
│   ├── config/                           # 配置包：app_config.py（唯一 schema）/ base.py（公共加载层）/ audit_config.py
│   │
│   ├── logging/                         # 日志三件套
│   │   ├── logging_config.py            # TraceContextFilter + JsonTraceFormatter
│   │   └── trace_middleware.py          # ASGI TraceMiddleware (X-Trace-Id)
│   │
│   ├── deerflow/                        # DeerFlow 风格 SSE
│   │   ├── protocol.py                   # 帧序列化（event/data/id + 心跳 + end 哨兵）
│   │   ├── formatter.py                  # AgentScope 事件 → deer-flow 事件翻译
│   │   ├── bridge.py                     # MessageBus 回放 + 订阅（断线续传）
│   │   ├── runs.py                       # RunManager：run 状态机 / 延迟清理
│   │   ├── custom_params.py              # 请求级 custom_params：ContextVar + workspace 落盘回退
│   │   ├── auth_context.py               # 认证方案解析（ResolvedAuth + ContextVar）
│   │   ├── deps.py                       # FastAPI 依赖注入
│   │   └── routers/                      # threads.py / deerflow_chat.py / auth_stub.py
│   │
│   ├── providers/                       # 多模型路由
│   │   └── provider_manager.py          # ProviderManager
│   │
│   ├── credential/                      # 自定义凭证类型（如 ELLMCredential）
│   │
│   ├── tools/                           # 自定义工具
│   │   ├── registry.py                  # ToolRegistry (自动扫描)
│   │   ├── builtin_tools.py             # 内置示例工具
│   │   ├── enterprise.py                # 企业工具 build 工厂（检索开关消费点）
│   │   ├── cross_search.py              # 行内检索（空间码强制覆盖中间件）
│   │   ├── agent_factory_tools.py       # 智能体工厂工具（guwp token 联动）
│   │   ├── placeholder.py               # 企业工具占位（HR / 文档库 / ITSM）
│   │   └── custom/                      # 你的产品工具放这里（自动扫描）
│   │
│   ├── middleware/                      # 中间件
│   │   ├── registry.py                  # MiddlewareRegistry (自动扫描)
│   │   ├── agent_middleware.py          # 内置示例
│   │   ├── audit.py                     # 企业审计留痕中间件
│   │   ├── custom_prompt.py             # custom_prompt 整体覆盖中间件
│   │   ├── factory.py                   # 企业中间件 build 工厂
│   │   ├── error_handler.py             # ASGI 错误处理
│   │   ├── request_log.py               # ASGI 访问日志
│   │   └── custom/                      # 你的产品中间件放这里（自动扫描）
│   │
│   ├── mcp/                             # MCP 连接器
│   │   ├── registry.py                  # McpRegistry (自动扫描)
│   │   ├── builtin_mcps.py              # 内置 MCP 示例
│   │   └── custom/                      # 你的产品 MCP 放这里
│   │
│   ├── routers/                         # 自定义路由
│   │   ├── models.py                    # 模型列表 + 切换
│   │   ├── health.py                    # 健康检查 (/healthz /readyz)
│   │   ├── platform_health.py           # 平台健康检查 GET /platform/health
│   │   ├── stats.py                     # 统计示例
│   │   ├── agent_tools.py               # 智能体工具白名单管理（详见 docs/api.md）
│   │   ├── session_usage.py             # 会话 Token 用量（详见 docs/api.md）
│   │   ├── channels.py                  # deer-flow channels 兼容
│   │   ├── credential_model.py          # 凭证-模型绑定查询 / 更新
│   │   ├── skill_router.py              # 技能路由
│   │   ├── uploads.py / workspace_files.py  # 文件上传与工作区文件
│   │   └── custom/                      # 你的产品路由放这里
│   │
│   ├── skills/                          # 企业技能（BocomSkillHub）
│   ├── uploads/                         # 上传 staging 管理（过期清理）
│   ├── workspace/                       # K8s 沙箱工作区（whitelist 代理 / factory / 共享 PVC）
│   ├── docker/                          # Docker 相关
│   ├── toolkit_whitelist.py             # 工具白名单持久化
│   └── agents/
│       └── templates.py                 # subagent 模板
│
└── tests/
    ├── test_logging.py
    └── test_deerflow_*.py               # DeerFlow SSE 全链路测试
                                         # （bridge/formatter/protocol/runs/stream/join/
                                         #   confirm/channels/threads_* 等 13 个）
```

---

## 配置体系

**单源化**：`config.yaml` 为唯一配置载体，`AppConfig`（`bocomadp/config/app_config.py`）为唯一 schema（已含 `app_name` / `workspace_dir` / 日志 / Redis / 注册表开关等全部字段），环境变量仅作部署期覆盖。

### 配置读取优先级（高 → 低）

① 进程环境变量（`BOCOMADP_*`，嵌套字段用 `__` 分隔）→ ② `.env` 文件 → ③ `config.yaml`（含 `$VAR` / `${VAR}` 展开）→ ④ 代码默认值

其中 `$VAR` 的取值来源：进程环境变量 > `.env` 文件（首次访问时自动加载，`setdefault` 不覆盖已有值）

### config.yaml 结构

`config.yaml` 是唯一 YAML 配置载体，根节点统一声明框架与业务配置：

```yaml
# ===== 业务配置 =====
app_name: "交通银行智能体平台"        # 应用名
workspace_dir: "./workspaces"        # 工作区目录（支持 $VAR 展开）
audit:                               # AuditConfig
  enabled: true
  log_path: "./logs/audit.jsonl"

# ===== 框架配置 =====
log_level: info
logging:
  enhance:
    enabled: true
    format: text                     # text | json
service:
  host: 0.0.0.0
  port: 8000
  reload: false
redis:
  host: localhost
  port: 6379
tools / middlewares / mcp:
  enabled: true
  load_custom: true
providers:
  enabled: true
  config_file: null

# ===== 模型 Provider =====
models:
  - provider_id: deepseek
    provider_type: deepseek
    model_name: deepseek-chat
    api_key: ${DEEPSEEK_API_KEY}     # 支持 ${ENV_VAR} 展开
    ...
```

### 配置加载流程（bocomadp/config）

```
main.py
  └─ get_app_config()                       # bocomadp/config/app_config.py
       └─ AppConfig()                       # pydantic-settings
            ├─ 读取 config.yaml（主源，$VAR 展开）
            ├─ 读取 .env 文件
            ├─ 读取 BOCOMADP_* 环境变量（优先级最高）
            └─ 嵌套字段用 __ 分隔
                 如 BOCOMADP_LOGGING__ENHANCE__FORMAT=json
```

**② config.yaml → models 节点**（模型 Provider 注册）

```
main.py
  └─ load_models_from_yaml(config.providers.config_file)
       └─ yaml.safe_load("config.yaml")
            └─ 取 data["models"] → ModelEntry 列表
                 └─ api_key 中 ${ENV_VAR} 被 _resolve_env() 替换为实际值
                 └─ 逐条注册到 ProviderManager
```

常用环境变量：

```bash
BOCOMADP_LOG_LEVEL=debug
BOCOMADP_LOGGING__ENHANCE__ENABLED=true
BOCOMADP_LOGGING__ENHANCE__FORMAT=json     # text | json
BOCOMADP_TOOLS__LOAD_CUSTOM=true
BOCOMADP_MIDDLEWARES__LOAD_CUSTOM=true
BOCOMADP_MCP__LOAD_CUSTOM=true
BOCOMADP_PROVIDERS__CONFIG_FILE=config.yaml # 模型配置文件路径
```

> 完整加载链路（热加载语义 / 键拼写校验 / 扩展规范）见 [config_load_design.md](./config_load_design.md)。

---

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

### 3. 配置

```bash
cd examples/agent_service
cp .env.example .env
# 编辑 .env 填入 API Key 等敏感配置
# config.yaml 已存在，按需修改
```

### 4. 启动服务

```bash
python main.py
# 或
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

> 工作区模式：`ADP_K8S_ENABLED=true`（默认）走 K8s 沙箱工作区（每个 agent 独立 Pod/PVC，
> 或共享 PVC 按 session 子目录隔离）；本地开发设 `ADP_K8S_ENABLED=false` 回退
> `LocalWorkspaceManager`（`{workspace_dir}/{agent_id}/` 布局）。

### 5. 启动 Web UI

```bash
cd examples/web_ui/
pnpm install && pnpm dev
```

设置 API 端点为 `http://localhost:8000` 即可。

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/threads/{tid}/runs/stream` | POST | 创建 run + SSE 流式（deer-flow 协议，含 Content-Location 头） |
| `/api/threads/{tid}/runs/wait` | POST | 创建 run + 阻塞至完成 |
| `/api/threads/{tid}/runs/{rid}/stream` | GET | join 已有 run（回放 + Last-Event-ID 续传） |
| `/api/threads/{tid}/runs/{rid}/cancel` | POST | 取消 run（映射原生 interrupt） |
| `/api/models` | GET | 模型列表 |
| `/api/models/active` | POST | 切换活跃模型 |
| `/api/agents/{agent_id}/tools` | GET/PUT/DELETE | 智能体工具白名单（详见 api.md） |
| `/api/sessions/{session_id}/usage` | GET | 会话 Token 用量（详见 api.md） |
| `/healthz` | GET | 存活检查 |
| `/readyz` | GET | 就绪检查 |
| `/platform/health` | GET | 平台健康检查（bocomadp） |

> 上述路由叠加在 `create_app` 自动注册的内置路由之上；全量 curl 速查（含网关转发规则）
> 见 [API接口文档.md](./API接口文档.md)。

---

## 自定义开发

### 自动注册机制

| 组件类型 | 注册表 | builtin 扫描 | custom 扫描 | 判定标记 |
|---------|--------|-------------|------------|---------|
| 工具 | `ToolRegistry` | `tools/builtin_tools.py` | `tools/custom/*.py` | `_is_tool = True` |
| Agent 中间件 | `MiddlewareRegistry` | `middleware/agent_middleware.py` | `middleware/custom/*.py` | `_is_agent_middleware = True` |
| MCP 连接器 | `McpRegistry` | `mcp/builtin_mcps.py` | `mcp/custom/*.py` | duck-type（有 `name` + `mcp_config`） |

**核心原则**：在 `custom/` 目录下新建 `.py` 文件，导出组件实例，重启自动注册。

### 加一个自定义工具

```python
# bocomadp/tools/custom/my_tool.py
from agentscope.tool import tool

@tool
def my_tool(query: str) -> str:
    """工具描述。"""
    return f"result: {query}"
```

### 加一个 Agent 级中间件

```python
# bocomadp/middleware/custom/audit_mw.py
from agentscope.middleware import MiddlewareBase

class AuditMiddleware(MiddlewareBase):
    async def pre_process(self, msg):
        return msg

audit_mw = AuditMiddleware()  # 模块级实例导出，自动注册
```

### 加一个 MCP 连接器

```python
# bocomadp/mcp/custom/amap.py
from agentscope.mcp import MCPClient, HttpMCPConfig

amap = MCPClient(
    name="amap",
    mcp_config=HttpMCPConfig(url="https://mcp.amap.com/mcp?key=xxx"),
)
```

### 加一个 ASGI 中间件

在 `build_asgi_middlewares()` 中注册：

```python
def build_asgi_middlewares(trace_enabled: bool) -> list[Middleware]:
    return [
        Middleware(TraceMiddleware, enabled=trace_enabled),
        Middleware(AccessLogMiddleware, skip_paths=("/healthz",)),
        Middleware(ErrorHandlingMiddleware),
        Middleware(MyMiddleware, enabled=True),          # ← 新加
        Middleware(CORSMiddleware, allow_origins=["*"]),
    ]
```

**顺序原则**：`TraceMiddleware` 最内层，`ErrorHandlingMiddleware` 最外层。

### 加一个路由

```python
# bocomadp/routers/custom/orders.py
from fastapi import APIRouter
orders_router = APIRouter(prefix="/orders", tags=["orders"])
```

```python
# main.py
from bocomadp.routers.custom.orders import orders_router
app.include_router(orders_router)
```

---

## 架构概览

### main.py 组装流程

1. **配置加载** — `get_app_config()` 读 config.yaml + `.env` + `BOCOMADP_*` 环境变量
2. **日志初始化** — `configure_logging(config)`
3. **框架模块初始化** — ToolRegistry → MiddlewareRegistry → McpRegistry → ProviderManager → RunManager → BusBridge
4. **模型注册** — `load_models_from_yaml("config.yaml")` 自动注册到 ProviderManager
5. **工作区与消息总线** — K8s 沙箱模式（默认：K8s/共享 PVC 工作区 + RedisMessageBus）或本地模式（LocalWorkspaceManager + InMemoryMessageBus）
6. **构建 App** — `create_app()` 自动注册内置路由
7. **注入 ASGI 中间件** — Trace → AccessLog → Error → CORS
8. **挂载自定义路由** — health / stats / deerflow / models / platform_health / agent_tools / session_usage / uploads 等
9. **企业扩展接入** — `extra_agent_middlewares`（审计）、`extra_agent_tools`（企业工具）

### DeerFlow 风格 SSE 链路

```
POST /api/threads/{tid}/runs/stream
  → _ensure_session（懒建会话 + 模型凭证自动供给：deerflow-<user_id>-<provider_id>）
  → custom_params 解析（带值落盘 workspace / 不带值回退加载）
  → RunManager 记账（409 并发拒绝）→ 原生 ChatService.run(run_id=...) 后台任务
  → BusBridge 订阅 MessageBus（Redis Stream 回放 + pub/sub live）
  → Formatter 翻译 → protocol 帧序列化（event/data/id + 心跳 + end 哨兵）
GET  /api/threads/{tid}/runs/{rid}/stream  → join（Last-Event-ID 断线续传）
POST /api/threads/{tid}/runs/{rid}/cancel  → 原生 session 级 interrupt
```

### DeerFlow SSE 实现模块映射（P0 模块）

| 模块 | 功能 | 实现文件 |
|------|------|---------|
| 1 | DeerFlow SSE 流式对话 | [deerflow/routers/deerflow_chat.py](../bocomadp/deerflow/routers/deerflow_chat.py)（4 端点） |
| 2 | SSE 帧序列化 / 事件翻译 | [deerflow/protocol.py](../bocomadp/deerflow/protocol.py) + [deerflow/formatter.py](../bocomadp/deerflow/formatter.py) |
| 3 | 总线适配（回放 + 订阅） | [deerflow/bridge.py](../bocomadp/deerflow/bridge.py)（复用原生 MessageBus） |
| 4 | run 记账与状态机 | [deerflow/runs.py](../bocomadp/deerflow/runs.py)（RunManager） |
| 5 | Agent 执行 | 原生 `ChatService`（与 `/chat/` 配置完全一致，无自研执行器） |
| 6 | 聊天会话管理 | AgentScope 内置 `/sessions` 路由 + [deerflow/routers/deerflow_chat.py](../bocomadp/deerflow/routers/deerflow_chat.py) |
| 7 | 多模型路由 | [providers/provider_manager.py](../bocomadp/providers/provider_manager.py) + [routers/models.py](../bocomadp/routers/models.py) |
| 8 | 场景种子 | config.yaml agents 段 → lifespan 幂等同步进框架 StorageBase |
| 9 | 请求级运行时配置 | [deerflow/custom_params.py](../bocomadp/deerflow/custom_params.py) + [tools/cross_search.py](../bocomadp/tools/cross_search.py) + [middleware/custom_prompt.py](../bocomadp/middleware/custom_prompt.py) |

---

## Docker 部署

```bash
docker build -f examples/agent_service/Dockerfile -t bocomadp-service . --network=host

# 或 Docker Compose
cd Docker-agentscope && docker compose up -d
```
