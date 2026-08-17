# AgentScope `examples/` 架构分析

> 分析对象：`examples/`（相对 `agentscope/` 仓库根目录）
> 生成时间：2026-08-06；修订：2026-08-17（`memo/` 归并进 `docs/` 后修订）

---

## 目录

- [一、整体格局](#一整体格局两类截然不同的示例)
- [二、`agent_service` —— 核心示例](#二agent_service--核心示例)
- [三、`web_ui` —— pnpm monorepo](#三web_ui--pnpm-monorepo)
- [四、三个轻量示例](#四三个轻量示例)
- [五、架构总结](#五架构总结)
- [附录：完整目录树](#附录完整目录树)

---

## 一、整体格局：两类截然不同的示例

`examples/` 下共 5 个目录，实际分成两个层次：

| 目录 | 类型 | 规模 | 定位 |
|---|---|---|---|
| `agent_service/` | **完整后端应用** | Python 包 | 生产级参考实现 |
| `web_ui/` | **完整前端应用** | pnpm monorepo | 配套 Web 控制台 |
| `long_term_memory/` | 轻量脚本 | 3 子示例 × (README + 单文件) | 单点功能演示 |
| `rag/` | 轻量脚本 | 2 个单文件脚本 | 单点功能演示 |
| `workspace/` | **纯文档** | 1 个 md，无代码 | 部署说明 |

分界线很清晰：**`agent_service` + `web_ui` 是一套可跑的完整产品**（主 README 中的 "Hello Agent Service!" 即指这两者），其余三个是「一个文件说明一个特性」的教学脚本。

启动方式（来自主 `README.md`）：

```bash
# 终端 1：后端
cd agentscope/examples/agent_service
python main.py

# 终端 2：前端
cd agentscope/examples/web_ui
pnpm install
pnpm dev
```

---

## 二、`agent_service` —— 核心示例

### 2.1 定位：不是 demo，是「企业扩展层」参考架构

包名为 `bocomadp`，本质是演示 **如何在官方 `create_app()` 之上叠加企业能力而不 fork 主库**。

```python
# examples/agent_service/main.py
from agentscope.app import create_app
from agentscope.app.hub import ClawSkillHub, GitHubMCPHub
from agentscope.app.message_bus import InMemoryMessageBus, RedisMessageBus
from agentscope.app.rag.knowledge_base_manager import CollectionPerKbManager
from agentscope.app.storage import AsyncSQLAlchemyStorage, RedisStorage
from agentscope.app.workspace_manager import LocalWorkspaceManager
from agentscope.rag import QdrantStore
```

主库只暴露 `create_app` 一个装配点，所有基础设施（存储 / 消息总线 / 工作区 / 知识库 / Hub）都以**依赖注入**方式传入 —— 这是整个架构最重要的设计约定。

### 2.2 `main.py` 是唯一装配入口

模块级顺序执行（非工厂函数），各阶段严格有序：

| 阶段 | 内容 |
|---|---|
| 1 | 配置 + 日志：`get_app_config()` → `configure_logging()` |
| 2 | 注册表初始化：Tool / Middleware / Mcp Registry + ProviderManager |
| 3 | 会话 / 运行记账：RunManager + BusBridge（deerflow SSE 依赖） |
| 4 | 基础设施：storage + Qdrant + workspace manager（K8s 沙箱或本地模式二选一）+ message bus（Redis 或 InMemory） |
| 5 | `create_app()`：注入全部依赖，自动挂载内置路由 |
| 6 | `app.state` 挂载：把注册表暴露给路由层 |
| 7 | 自定义路由：deerflow / health / models / stats / agent_tools / session_usage / uploads 等叠加在内置路由之上 |

```
配置加载 → 注册表 → 运行记账 → 基础设施 → create_app → app.state → 自定义路由
```

### 2.3 `bocomadp/` 模块划分

```
bocomadp/
├── config/      配置：base(公共加载层) / app_config(唯一 schema) / audit_config
├── logging/     trace_context(ContextVar) + trace_middleware(ASGI) + formatter
├── deerflow/    ★ DeerFlow 风格 SSE：protocol / formatter / bridge / runs /
│                custom_params / auth_context / deps / routers(threads + chat + auth_stub)
├── providers/   ProviderManager —— 多模型注册与运行时切换
├── credential/  自定义凭证类型（ELLMCredential 等）
├── tools/       registry(自动扫描) + builtin + enterprise(主动 build)
│                + cross_search(空间码覆盖中间件) + agent_factory_tools + custom/
├── middleware/  registry + agent_middleware + audit + custom_prompt + factory
│                + error_handler / request_log (2 个 ASGI 中间件)
├── mcp/         registry(duck-type 扫描) + builtin_mcps + custom/
├── routers/     models / health / platform_health / stats / agent_tools /
│                session_usage / channels / credential_model / skill_router
│                / uploads / workspace_files / custom
├── skills/      BocomSkillHub 企业技能
├── uploads/     上传 staging 管理（过期清理）
├── workspace/   K8s 沙箱工作区（whitelist 代理 / factory / 共享 PVC 子目录隔离）
└── agents/      templates.py —— researcher / coder 子 agent 模板
```

其中 `deerflow/routers/deerflow_chat.py` 承担 4 端点 + 会话/凭证自动供给 + custom_params 注入，是全链路最大的单文件。

### 2.4 三个值得注意的架构模式

#### ① 扩展点分两类：被动扫描 vs 主动构建

```python
# examples/agent_service/main.py
async def build_agent_tools(user_id, agent_id, session_id):
    tools = tool_registry.list_tools()                 # 被动扫描：builtin + custom/
    tools.extend(await build_enterprise_tools(...))    # 主动 build：按会话构造
    return tools

async def build_agent_middlewares(user_id, agent_id, session_id):
    middlewares = middleware_registry.list_middlewares()
    middlewares.extend(await build_enterprise_middlewares(...))
    return middlewares
```

| 方式 | 机制 | 适用场景 |
|---|---|---|
| **被动扫描** | 在 `custom/` 目录放一个模块级实例即可，重启生效 | 无状态工具 / 中间件 / MCP |
| **主动 build** | 工厂函数可拿到 `user_id / agent_id / session_id` | 需要会话隔离的企业能力（如审计留痕、检索开关） |

两者最终在 `build_agent_tools` / `build_agent_middlewares` 汇合，通过 `create_app(extra_agent_tools=..., extra_agent_middlewares=...)` 注入。

#### ② 注册表统一挂到 `app.state`

```python
# examples/agent_service/main.py（app.state 挂载段）
app.state.provider_manager = provider_manager
app.state.tool_registry = tool_registry
app.state.mcp_registry = mcp_registry
app.state.middleware_registry = middleware_registry
app.state.run_manager = RunManager()
app.state.bus_bridge = BusBridge(message_bus)
```

路由层不 import 全局变量，而是从 `request.app.state` 取，保持可测试性。

#### ③ ASGI 中间件栈显式声明顺序

```python
# examples/agent_service/main.py
def build_asgi_middlewares(trace_enabled: bool) -> list[Middleware]:
    """构建 ASGI 中间件栈（由内到外）。"""
    return [
        Middleware(TraceMiddleware, enabled=trace_enabled),
        Middleware(AccessLogMiddleware, skip_paths=("/healthz", "/readyz")),
        Middleware(ErrorHandlingMiddleware),
        Middleware(CORSMiddleware, allow_origins=["*"], ...),
    ]
```

顺序：`Trace → AccessLog → ErrorHandler → CORS`（由内到外）。

#### ④ 请求级配置走 ContextVar（custom_params）

前端随 run 请求携带 `custom_params`，路由层 `set → spawn（create_task 复制快照）→ reset`，
run 任务内的工具中间件 / 提示词中间件 / 工具工厂经 `get_custom_params()` 消费；
带值请求同时落盘到会话 workspace（`sessions/{session_id}/custom_params.json`），
不带值请求自动回退加载。完整机制见 [custom_params.md](./custom_params.md)。

### 2.5 配置体系

- `config.yaml` —— 唯一配置源
- `config.yaml.example` —— 配置模板
- `.env` / `.env.example` —— 环境变量
- 加载链路：`config.yaml` + `.env` + 环境变量 → `AppConfig`（`config/app_config.py`，唯一 schema）
- 详细设计见 [config_load_design.md](./config_load_design.md)

模型配置从 yaml 加载并自动注册到 `ProviderManager`，支持 `is_active` 标记切换默认激活项。

---

## 三、`web_ui` —— pnpm monorepo

### 3.1 重要澄清：`backend/` 不是 agent_service

`web_ui/backend/` 只有 **28 行 Express 存根**，仅提供 `/api/health`。

前端**直连** Python 侧的 agent_service（默认 `http://localhost:8000`），容器部署时由 nginx 网关统一转发。`backend/` 仅作可选代理层占位。

```
web_ui/                     (pnpm workspace)
├── frontend/               真正的 React SPA，全部业务逻辑在此
└── backend/                28 行 Express 存根，仅 /api/health
```

- 工作区定义：`pnpm-workspace.yaml`
- 编排脚本：根 `package.json`（`concurrently` 并行起前后端）
- 代码规范：husky + lint-staged + prettier

### 3.2 技术栈

| 类别 | 选型 | 版本 |
|---|---|---|
| UI 框架 | React | `^19.2.6` |
| 构建工具 | Vite | `^8.0.12` |
| 语言 | TypeScript | `~6.0.2` |
| 路由 | react-router-dom | `^7.15.1` |
| 样式 | Tailwind CSS v4 + `@tailwindcss/vite` | `^4.3.0` |
| 组件库 | radix-ui (shadcn/ui 体系) | `^1.4.3` |
| 图标 | lucide-react | `^1.16.0` |
| 动画 | framer-motion | `^12.40.0` |
| 流式渲染 | streamdown + cjk/code/math/mermaid 插件 | `^2.5.0` |
| 国际化 | i18next + react-i18next | `^26.2.0` |

注意 Tailwind v4 使用 **Vite 插件模式**而非传统 PostCSS 模式。

#### 关键依赖：官方 SDK

```json
"@agentscope-ai/agentscope": "^0.0.15"
```

**事件类型、消息协议、事件聚合逻辑全部来自 SDK**，前端不自造协议 —— 这保证了前后端 EventType 的一致性，避免协议漂移。

### 3.3 目录结构

```
frontend/src/
├── api/           HTTP 封装层（13 个模块）
├── hooks/         数据获取 + 业务状态（26 个 hook）★ 事实上的 store 层
├── context/       跨树共享状态（仅 2 个：Audio / Upload）
├── components/    16 个子目录，按 UI 语义分类
├── pages/         7 个路由页面
├── i18n/          国际化配置 + locales
├── lib/           底层工具（api-error / utils / next-navigation-shim）
├── utils/         业务工具（common / platform / streamingAudio）
├── assets/        静态资源
└── types/         全局类型声明（仅 unidiff.d.ts）
```

`components/` 按语义而非层级切分：
`chat/`（含 `tool-renderers/` 子体系）、`team/`、`panel/`、`dialog/`、`drawer/`、`popover/`、`select/`、`form/`、`badge/`、`hub/`、`knowledge/`、`markdown/`、`layout/`、`tour/`、`error/`、`ui/`（shadcn 基元）。

> **注意**：`types/` 目录几乎是空的，**业务类型集中在 `api/types.ts`**（28KB），与常见的「独立 types 目录」约定不同。

### 3.4 状态管理：没有 Redux / Zustand / MobX / React Query

这是本项目的显著架构特征。`hooks/` 承担了通常由 store 承担的职责：

| 命名规律 | 职责 |
|---|---|
| `use<Resource>s` | 列表 CRUD（`useAgents` / `useSessions` / `useSkills`…） |
| `useMessages` | **核心事件流引擎** |

跨树共享只用了 2 个 Context（Audio / Upload）。

### 3.5 API 层设计

```
api/client.ts    底座：buildApiUrl / ApiError / get,post,patch,delete,stream
api/types.ts     28KB，全部 DTO 集中于此
api/{agent,session,chat,credential,workspace,hub,mcp,
     skill,schedule,model,knowledgeBase}.ts
api/index.ts     桶导出
```

```ts
// frontend/src/api/client.ts
const API_PREFIX = import.meta.env.VITE_API_PREFIX ?? '/api';      // :7
export const getBaseUrl = () => localStorage.getItem('server_url') ?? '';  // :9
export const getUserId  = () => localStorage.getItem('username') ?? '';    // :10
export const buildApiUrl = (path) =>
    new URL(API_PREFIX + path, getBaseUrl() || window.location.origin);    // :13-14
```

设计要点：

- **鉴权极简**：无 token，仅靠 `X-User-ID` 请求头（`client.ts:41`）。这是 example 级别的简化，**生产不可直接使用**。
- **服务地址运行时可配**：`server_url` 由用户在 SetupPage 填写并存入 localStorage，而非编译期固定；未配置时回退同源。
- **统一错误处理**：`ApiError` 类 + `extractErrorDetail` 解析后端 `detail` 字段，默认自动 `toast.error`，可用 `silent` 选项抑制。

---

## 四、三个轻量示例

| 示例 | 演示内容 | 组织形式 |
|---|---|---|
| `long_term_memory/agentic_memory` | `AgenticMemoryMiddleware` —— 纯 Markdown 记忆，**无需向量库 / embedding** | README + `main.py` |
| `long_term_memory/mem0` | `Mem0Middleware` —— 同 `user_id` 跨 session 记忆 | README + `oss_demo.py` |
| `long_term_memory/reme` | ReMe 记忆后端集成 | README + `reme_demo.py` |
| `rag/index_and_search.py` | 索引 + 检索基础流程 | 单文件 |
| `rag/integrate_with_agent.py` | RAG 接入 Agent | 单文件 |
| `workspace/` | Apple Container 工作区部署 | **纯文档，无代码** |

### 共同模式

- 单文件 + `asyncio.run(main())`
- **无依赖文件**（依赖主库 extras）
- 都通过 `agent.reply_stream()` 消费事件流并打印 —— 作为事件系统的教学载体
- `long_term_memory/` **没有顶层 README**，三个子示例各自自洽

### 示例：`agentic_memory` 的典型结构

```
main()  (asyncio.run)
  ├─ 读取 os.environ["DASHSCOPE_API_KEY"]
  ├─ RESET_DEMO_WORKSPACE → shutil.rmtree(DEMO_ROOT)
  ├─ DashScopeChatModel(model="qwen3.7-max", stream=False)
  ├─ _build_agent(model, DEMO_ROOT)
  │    ├─ AgenticMemoryMiddleware(workdir=...)
  │    ├─ Agent(..., toolkit=Toolkit(tools=[Read(), Write()]), middlewares=[memory])
  │    └─ _configure_demo_permissions()   # PermissionMode.ACCEPT_EDITS
  ├─ Turn1: _run_turn(agent, FIRST_USER_MESSAGE)    # 持久化
  ├─ _print_memory_files() / _print_soft_verification()
  └─ Turn2: _run_turn(agent, SECOND_USER_MESSAGE)   # 回忆
```

涉及的主库模块：`agentscope.agent` / `credential` / `middleware` / `model` / `tool` / `permission` / `message` / `event`。

---

## 五、架构总结

```
                    src/agentscope  (主库)
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
   create_app()      Agent + 中间件      事件系统
        │                 │                 │
        ▼                 ▼                 ▼
  agent_service     long_term_memory      web_ui
  (企业扩展层)          rag (单点演示)      (SDK 消费方)
        │                                   │
        └──────── HTTP + SSE ───────────────┘
```

### 三条设计主线

1. **主库只留装配点，不留继承点**
   `create_app()` 全参数注入，示例通过**组合**而非继承扩展。企业能力全部落在 `bocomadp` 包内，主库零改动。

2. **扩展分层**
   注册表自动扫描（无状态）+ 工厂函数主动构建（会话相关），两者在 `build_agent_tools` / `build_agent_middlewares` 汇合；请求级差异（空间码 / 提示词 / 开关 / 认证）经 custom_params 的 ContextVar 通道注入 run 任务。

3. **前后端协议由 SDK 统一**
   `@agentscope-ai/agentscope` npm 包与 Python 侧 EventType 对齐，避免手写协议漂移。

### 文档修订记录

- 2026-08-17：`memo/` 五份文档归并进 `docs/`；`README.md` 目录树更新为当前代码
  （tests 实为 `test_logging.py` + `test_deerflow_*.py` 系列，旧文所述
  `test_registry_scan.py` 不存在）；早期 `目录结构.md` 的 P0 模块映射表并入
  `docs/README.md` 后删除。

---

## 附录：完整目录树

```
examples/
├── agent_service/                    # 完整后端应用
│   ├── main.py                       # 唯一入口
│   ├── config.yaml                   # 唯一配置源
│   ├── config.yaml.example           # 配置模板
│   ├── .env / .env.example
│   ├── Dockerfile                    # python:3.14-bookworm + uv
│   ├── docs/                         # 项目文档（README / api / API接口文档 /
│   │                                 #   custom_params / config_load_design / 本文档）
│   ├── tests/                        # test_logging + test_deerflow_* 系列（14 个）
│   └── bocomadp/
│       ├── config/      (base / app_config / audit_config / ...)
│       ├── logging/     (logging_config / trace_context / trace_middleware)
│       ├── deerflow/    (protocol / formatter / bridge / runs / custom_params
│       │                 / auth_context / deps / routers)
│       ├── providers/   (provider_manager)
│       ├── credential/  (ellm)
│       ├── tools/       (registry / builtin_tools / enterprise / cross_search
│       │                 / agent_factory_tools / placeholder / custom)
│       ├── middleware/  (registry / agent_middleware / audit / custom_prompt
│       │                 / factory / error_handler / request_log / custom)
│       ├── mcp/         (registry / builtin_mcps / custom)
│       ├── routers/     (models / health / platform_health / stats / agent_tools
│       │                 / session_usage / channels / credential_model
│       │                 / skill_router / uploads / workspace_files / custom)
│       ├── skills/ uploads/ workspace/ docker/ toolkit_whitelist.py
│       └── agents/      (templates)
│
├── web_ui/                           # pnpm monorepo
│   ├── package.json                  # 根：concurrently 编排
│   ├── pnpm-workspace.yaml
│   ├── pnpm-lock.yaml
│   ├── .husky/pre-commit
│   ├── .prettierrc / .prettierignore
│   ├── backend/                      # 28 行 Express 存根
│   │   ├── src/index.ts
│   │   ├── package.json
│   │   ├── tsconfig.json
│   │   └── Dockerfile
│   └── frontend/                     # React 19 SPA
│       ├── src/  (api / hooks / context / components / pages
│       │          / i18n / lib / utils / assets / types)
│       ├── index.html
│       ├── vite.config.ts
│       ├── components.json           # shadcn 配置
│       ├── eslint.config.js
│       ├── tsconfig{,.app,.node}.json
│       ├── package.json
│       └── Dockerfile
│
├── long_term_memory/                 # 轻量脚本（无顶层 README）
│   ├── agentic_memory/  README.md + main.py
│   ├── mem0/            README.md + oss_demo.py
│   └── reme/            README.md + reme_demo.py
│
├── rag/                              # 轻量脚本（扁平）
│   ├── README.md
│   ├── index_and_search.py
│   └── integrate_with_agent.py
│
└── workspace/                        # 纯文档
    └── apple-container-workspace.md
```
