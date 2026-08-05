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
- **企业扩展案例**（`bankcomm_adp/`）：审计留痕、企业内部工具、平台健康检查

## 目录结构

```
examples/agent_service/
├── main.py                              # 入口：组装 create_app + 框架模块 + 中间件 + 路由
├── config.yaml                          # 单一配置文件（模型 + 企业扩展共享）
├── .env                                 # 环境变量（可选，自动加载）
├── README.md
├── Dockerfile
│
├── bocomadp/                            # 核心扩展包
│   ├── config.py                        # 核心配置（pydantic-settings，环境变量驱动）
│   │
│   ├── logging/                         # 日志三件套
│   │   ├── logging_config.py            # TraceContextFilter + JsonTraceFormatter
│   │   └── trace_middleware.py          # ASGI TraceMiddleware (X-Trace-Id)
│   │
│   ├── runtime/                         # 8 阶段请求编排引擎
│   │   ├── phases.py                    # 8 阶段枚举
│   │   ├── hooks.py                     # 生命周期钩子注册表
│   │   ├── envelope.py                  # SSE 事件信封状态机
│   │   ├── executor.py                  # 心跳包裹的 Agent 执行器
│   │   ├── builder.py                   # 每请求动态组装 Agent
│   │   └── runtime.py                   # 8 阶段编排器主入口
│   │
│   ├── providers/                       # 多模型路由
│   │   └── provider_manager.py          # ProviderManager
│   │
│   ├── tools/                           # 自定义工具
│   │   ├── registry.py                  # ToolRegistry (自动扫描)
│   │   ├── builtin_tools.py             # 内置示例工具
│   │   └── custom/                      # 你的产品工具放这里
│   │
│   ├── middleware/                      # 中间件
│   │   ├── registry.py                  # MiddlewareRegistry (自动扫描)
│   │   ├── agent_middleware.py          # 内置示例
│   │   ├── error_handler.py             # ASGI 错误处理
│   │   ├── request_log.py               # ASGI 访问日志
│   │   └── custom/                      # 你的产品中间件放这里
│   │
│   ├── mcp/                             # MCP 连接器
│   │   ├── registry.py                  # McpRegistry (自动扫描)
│   │   ├── builtin_mcps.py              # 内置 MCP 示例
│   │   └── custom/                      # 你的产品 MCP 放这里
│   │
│   ├── routers/                         # 自定义路由
│   │   ├── chat_sse.py                  # SSE 流式对话
│   │   ├── agent_manage.py              # 多 Agent CRUD
│   │   ├── models.py                    # 模型列表 + 切换
│   │   ├── health.py                    # 健康检查 (/healthz /readyz)
│   │   └── stats.py                     # 统计示例
│   │
│   └── agents/
│       └── templates.py                 # subagent 模板
│
├── bankcomm_adp/                        # 企业扩展案例包
│   ├── config/                          # 企业配置（dataclass + from_yaml）
│   │   ├── base.py                      # 公共基座：.env 加载、YAML 解析、环境变量展开
│   │   ├── settings_config.py           # 全局配置（app_name / workspace_dir）
│   │   ├── audit_config.py              # 审计配置（enabled / log_path）
│   │   └── cross_search_config.py       # 跨知识搜索配置
│   ├── middlewares/
│   │   ├── audit.py                     # 审计留痕中间件
│   │   └── factory.py                   # 中间件工厂
│   ├── routers/
│   │   └── health.py                    # GET /platform/health
│   └── tools/
│       └── enterprise.py               # 企业工具工厂
│
└── tests/
    ├── test_logging.py
    └── test_registry_scan.py
```

---

## 配置体系

项目采用 **双配置源** 架构，共享同一个 `config.yaml` 文件：

| 配置源 | 包 | 驱动方式 | 职责 |
|--------|-----|---------|------|
| **核心配置** | `bocomadp/config.py` | `pydantic-settings`，环境变量 + `.env` | 框架运行参数（日志、Redis、注册表开关等） |
| **企业配置** | `bankcomm_adp/config/` | `@dataclass + from_yaml()`，读取 `config.yaml` | 业务参数（应用名、审计、工作区路径、工具配置等） |

### 配置读取优先级

两个包的读取方式不同：

**bocomadp（核心配置）**：
- 框架参数（日志、Redis、注册表开关等）→ **环境变量**（`BOCOMADP_*`）+ `.env` 文件
- 模型 Provider → **config.yaml** 的 `models` 节点

**bankcomm_adp（企业配置）**：
```
config.yaml（含 $VAR 环境变量展开）> 代码默认值
```
其中 `$VAR` 的取值来源：进程环境变量 > `.env` 文件（首次访问时自动加载，`setdefault` 不覆盖已有值）

### config.yaml 结构

`config.yaml` 是唯一的 YAML 配置文件，被两个包按节点分工读取：

```yaml
# ===== bocomadp 读取 models 节点 =====
# 由 load_models_from_yaml() 加载，启动时自动注册到 ProviderManager
models:
  - provider_id: deepseek
    provider_type: deepseek
    model_name: deepseek-chat
    api_key: ${DEEPSEEK_API_KEY}     # 支持 ${ENV_VAR} 展开
    base_url: https://api.deepseek.com
    is_active: true
    parameters:
      temperature: 0.7

# ===== bankcomm_adp 读取以下节点 =====
app_name: "交通银行智能体平台"        # Settings.app_name
workspace_dir: "./workspaces"        # Settings.workspace_dir

audit:                               # AuditConfig
  enabled: true
  log_path: "./logs/audit.jsonl"

cross_search:                        # CrossSearchConfig
  api_url: $CROSS_SEARCH_API_URL     # 支持 $VAR 展开
  timeout: 30
  ...
```

> bocomadp 其余配置（日志、Redis、注册表开关等）通过 **环境变量** 控制，不走 config.yaml。

### 核心配置读取流程（bocomadp）

bocomadp 的配置分两路：

**① 环境变量 → pydantic-settings**（框架运行参数）

```
main.py
  └─ load_config()                          # bocomadp/config.py
       └─ AppConfig()                       # pydantic-settings
            ├─ 读取 .env 文件
            ├─ 读取 BOCOMADP_* 环境变量
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
BOCOMADP_RUNTIME__HEARTBEAT_INTERVAL_SECONDS=15.0
```

### 企业配置读取流程（bankcomm_adp）

```
main.py
  └─ get_settings().workspace_dir           # settings_config.py
       └─ Settings.from_yaml()
            ├─ load_config_yaml()           # base.py（@lru_cache 缓存）
            │    └─ yaml.safe_load("config.yaml")
            ├─ expand_env_vars(data)        # 递归展开 $VAR / ${VAR}
            │    └─ _load_dotenv_once()     # 首次调用时加载 .env
            └─ 提取 app_name / workspace_dir

  └─ get_audit_config().enabled             # audit_config.py
       └─ AuditConfig.from_yaml()
            └─ yaml_section(data, ["audit"]) → enabled / log_path
```

企业配置按模块拆分，统一采用 `@dataclass + from_yaml()` 模式：

```
bankcomm_adp/config/
├── base.py                  # 公共基座
│   ├─ _load_dotenv_once()   # .env 加载（lru_cache 保证只一次）
│   ├─ load_config_yaml()    # YAML 加载（lru_cache 缓存）
│   ├─ expand_env_vars()     # $VAR / ${VAR} 递归展开
│   └─ resolve_path()        # 相对路径 → 绝对路径（基于 BASE_DIR）
├── settings_config.py       # Settings: app_name, workspace_dir
├── audit_config.py          # AuditConfig: enabled, log_path
└── cross_search_config.py   # CrossSearchConfig: api_url, timeout, ...
```

每个模块通过 `get_xxx_config()` 工厂函数获取，每次调用重新从 YAML 构建（支持运行时修改配置即时生效）。

### 环境变量展开机制

`config.yaml` 中的字符串值支持 `$VAR` 和 `${VAR}` 两种写法：

```yaml
# .env 文件中设置
DEEPSEEK_API_KEY=sk-xxx
CROSS_SEARCH_API_URL=https://api.example.com

# config.yaml 中引用
models:
  - api_key: ${DEEPSEEK_API_KEY}     # → sk-xxx
cross_search:
  api_url: $CROSS_SEARCH_API_URL     # → https://api.example.com
```

展开时机：
- **bocomadp**：`load_models_from_yaml()` 中调用 `_resolve_env()` 逐个展开
- **bankcomm_adp**：`expand_env_vars()` 递归展开整个 YAML 树（字符串、列表、字典中的字符串均支持）

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

### 5. 启动 Web UI

```bash
cd examples/web_ui/
pnpm install && pnpm dev
```

设置 API 端点为 `http://localhost:8000` 即可。

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/chat/run` | POST | SSE 流式对话 |
| `/api/chat/stop` | POST | 停止对话 |
| `/api/agents` | GET / POST | Agent 列表 / 创建 |
| `/api/models` | GET | 模型列表 |
| `/api/models/active` | POST | 切换活跃模型 |
| `/healthz` | GET | 存活检查 |
| `/readyz` | GET | 就绪检查 |
| `/platform/health` | GET | 平台健康检查（bankcomm_adp） |

> 上述路由叠加在 `create_app` 自动注册的 12 个内置路由之上。

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

1. **配置加载** — `load_config()` 读环境变量 / `.env`
2. **日志初始化** — `configure_logging(config)`
3. **框架模块初始化** — ToolRegistry → MiddlewareRegistry → McpRegistry → ProviderManager → HookRegistry → Runtime
4. **模型注册** — `load_models_from_yaml("config.yaml")` 自动注册到 ProviderManager
5. **构建 App** — `create_app()` 自动注册 12 个内置路由
6. **注入 ASGI 中间件** — Trace → AccessLog → Error → CORS
7. **挂载自定义路由** — health / stats / chat_sse / agent_manage / models / platform_health
8. **企业扩展接入** — `extra_agent_middlewares`（审计）、`extra_agent_tools`（企业工具）

### 8 阶段运行时

```
PRE_DISPATCH      → POST_DISPATCH     → PRE_AGENT_BUILD   →
POST_AGENT_BUILD  → PRE_EXECUTE       → [AgentExecutor]    →
POST_RESPONSE     → ON_ERROR          → FINALLY
```

---

## Docker 部署

```bash
docker build -f examples/agent_service/Dockerfile -t bocomadp-service . --network=host

# 或 Docker Compose
cd Docker-agentscope && docker compose up -d
```
