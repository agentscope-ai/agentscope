create_app 是什么
create_app 是 AgentScope 应用服务层（App Layer）的唯一装配入口，定义在 src/agentscope/app/_app.py:76。它是一个工厂函数，负责构造并装配一个完整、可部署的 FastAPI 智能体服务实例——把底层抽象组件（存储、消息总线、模型、工具、权限等）注入、接线、并挂载所有 REST 路由，最终返回可 uvicorn 运行的 FastAPI 应用。
一、核心职责：依赖装配 + 生命周期管理
create_app 本身只做配置注入与路由挂载；真正的资源启停交给 FastAPI 的 lifespan（_lifespan.py:32），通过单个 AsyncExitStack 按序进入、逆序销毁所有带生命周期的资源（storage、message_bus、workspace、hubs、各 Dispatcher 等），部分启动失败时也不泄漏。
def create_app(
    # —— 基础设施（必填其一或默认内存）——
    storage: StorageBase,                       # 持久化后端（本地/Redis/SQL）
    message_bus: MessageBase,                   # 跨会话消息总线
    workspace_manager: WorkspaceManagerBase,     # 工作区（文件沙箱）
    knowledge_base_manager: KnowledgeBaseManager | None,  # RAG 向量库
    blob_store: BlobStoreBase | None,           # 大文件存储
    resource_access_policy: ResourceAccessPolicy = DenyAllResourceAccessPolicy(),  # 默认租户隔离

    # —— 凭据 / 模型 ——
    model_configs: ... / credential_configs: ...,   # LLM 凭据（加解密）
    default_chat_model_config / fallback_chat_model_config: ...,

    # —— 可插拔扩展点（注册自定义行为）——
    extra_agent_tools: AgentToolFactory | None,        # 注册额外工具
    extra_agent_middlewares: AgentMiddlewareFactory | None,  # 中间件工厂
    custom_subagent_templates: dict[str, SubAgentTemplate] | None,  # 团队 worker 模板
    custom_agent_cls: type[Agent] | None,              # 替换默认 Agent 类
    builtin_toolkits: ...,                             # 内置工具开关

    # —— 部署模式 ——
    enable_index_worker: bool = True,   # True=嵌入式索引 / False=分离子进程索引
    ...
) -> FastAPI
这些参数在 create_app 里先写入 app.state.*（如 app.state.storage、app.state.extra_agent_middlewares、app.state.custom_subagent_templates），再由 lifespan 在启动时消费构建各 Service。


AgentScope 智能体创建接口分析
AgentScope 提供两套互补的智能体创建接口：一套是面向用户的 REST 持久化 CRUD（创建可长期保存、可编辑的配置型 Agent），另一套是运行时工具 AgentCreate（由 Leader Agent 在团队中动态派生 Worker Agent）。
一、REST 接口：POST /agent（用户态创建）
入口在 src/agentscope/app/_router/_agent.py:165，由 create_agent 处理。

请求/响应契约
# 请求 (CreateAgentRequest, _schema/_agent.py:12)
{
  "name": str,                       # 必填，显示名
  "system_prompt": str = "You're a helpful assistant.",
  "context_config": ContextConfig,   # 上下文窗口管理（默认构造）
  "react_config": ReActConfig,       # ReAct 循环配置（默认构造）
  "invite_config": InviteConfig,     # 是否可被其他 Agent 邀请入队
}

# 响应 (CreateAgentResponse, _router/_agent.py:213)
{ "agent_id": "<server-assigned id>" }   # HTTP 201
创建流程
用 CreateAgentRequest 字段构造 AgentData（Pydantic 模型），跨字段校验失败返回 422（如 invitable=True 但 invite_description 为空，由 InviteConfig._check_invitable_has_description 强制）。
包装成 AgentRecord(user_id=..., data=..., source="user")，id 由 _generate_id() 服务端生成（SkipJsonSchema，不出现在表单 schema 但持久化保留）。
await storage.upsert_agent(user_id, record) 落库。
配套接口（同路由 agent_router）
方法	路径	功能
GET	/agent/schema/v2	返回完整 AgentData 的 JSON Schema，前端据此自动渲染表单（新增字段无需改路由）
GET	/agent/	列出当前用户可见 Agent（含被共享的），带 editable 标记
PATCH	/agent/{agent_id}	部分更新（排除 None 字段），经 access.resolve_for_edit 鉴权
DELETE	/agent/{agent_id}	级联删除该 Agent 拥有的所有 session/team worker，取消在途聊天
数据模型分层
AgentData（storage/_model/_agent.py:65）：纯配置（name / system_prompt / context_config / react_config / invite_config）。
AgentRecord（_model/_agent.py:112）：AgentData + user_id + source: Literal["user","team"]。
关键区分：source="user" 出现在用户 Agent 列表、可多 session；source="team" 为团队 worker、仅一个 session、不出现在全局列表。


一个用户能否创建多个智能体？智能体之间如何隔离？
一、一个用户能创建多个智能体吗？
能。 POST /agent/ 没有数量上限约束，每个认证用户都可以创建任意多个 source="user" 的智能体，每个智能体拥有独立的 agent_id、独立的配置（system_prompt / context_config / react_config / invite_config）和独立的多会话。
# 同一个 user_id 下，可反复调用
POST /agent/  →  agent_1 ("代码助手")
POST /agent/  →  agent_2 ("翻译助手")
POST /agent/  →  agent_3 ("会议纪要") ...

二、智能体之间的隔离分几层
1. 归属隔离（谁拥有谁）—— 靠 user_id 字段
每条 AgentRecord 都带 user_id（storage/_model/_agent.py:112）。所有列表、读取、编辑、删除操作都先解析出调用者身份 get_current_user_id，再按 user_id 过滤。
而是靠 user_id 归属 + resolve_for_edit 鉴权 + 默认拒绝的 ResourceAccessPolicy + 会话绑定 这一套贯穿存储层与访问服务层的多租户数据隔离实现的。需要跨用户协作时，才通过显式的"共享引用（ref）"机制临时开放可见/可编辑权限，且写入仍归原所有者。

创建的 Agent 配置保存在哪里？
Agent 的配置不是硬编码在代码或内存里，而是序列化后交给可插拔的 StorageBase 后端持久化。具体地点由你 create_app(storage=...) 时传入的存储实现决定。
一、保存的数据结构
POST /agent 时，CreateAgentRequest 的字段被构造成一个 Pydantic 对象：
AgentData(name, system_prompt, context_config, react_config, invite_config)
         ↓ 包装
AgentRecord(user_id=<当前用户>, data=AgentData, source="user", id=<服务端生成>)
这个 AgentRecord 就是落库的最小持久化单元——它包含了该 Agent 的全部配置 + 归属用户 + 来源标记。

二、保存动作
调用链：
create_agent()  →  storage.upsert_agent(user_id, agent_record)   # _agent.py:211
upsert_agent 是 StorageBase（storage/_base.py:301）定义的抽象方法，具体写到哪由实现类决定。
三、三种可选存储后端
src/agentscope/app/storage/ 提供了多种实现，部署时选其一：

后端	文件	实际物理存储位置
SQL（生产推荐）	storage/_sql/	关系型数据库（PostgreSQL/SQLite 等，由 py.typed + alembic 迁移 .ini/.mako 可见）
Redis	storage/_redis_storage.py	Redis 实例（键值/哈希结构）
本地默认	storage/__init__.py 暴露的默认实现	本地文件系统（JSON/文件）
框架并不关心"配置在不在 JSON 里"——它只定义 StorageBase 接口（upsert_agent / get_agent / list_agents / delete_agent），各后端把 AgentRecord 序列化成自己格式存好。这种抽象让你从"本地测试"切到"多用户线上 Redis/SQL"时，业务代码零改动。

四、配置与"运行时状态"是分开存的
一个重要区分：Agent 配置（system_prompt 等静态定义）存在 AgentRecord 里；而每次聊天的对话历史与运行状态存在 SessionRecord 里，通过 upsert_session 落库：
upsert_session(user_id, agent_id, config, state=AgentState)   # _base.py:364
所以一个 Agent 可以拥有多个 Session（多次对话），配置只有一份，状态按会话隔离。

Agent 的 Tools / MCP / Skills 是如何隔离的？
核心结论：Agent 本身并不直接持有 tools/mcp/skills，它们通过两层间接关系实现隔离——"用户级库记录"做归属隔离，"Session 绑定的 Workspace"做生效隔离。
一、隔离的两种粒度
1. 用户级库（MCP / Skill）—— 归属在 user_id
MCPRecord 和 SkillRecord 都带 user_id，且全局唯一：

MCP 的 client.name 必须"每用户唯一"（_mcp.py:19 注释："the workspace relation is derived, not stored: it joins on the MCP name"）。
Skill 的 name 也"每用户唯一"（_skill.py:25）。
# 用户 A 安装的 MCP / Skill
MCPRecord(user_id="A", client.name="github", ...)   # A 的
MCPRecord(user_id="B", client.name="github", ...)   # B 的，互不可见

二者都是"用户安装库"，不是某 Agent 私有的——这是第一层隔离：库记录按 user_id 隔离，用户只能看到/管理自己安装的 MCP 和 Skill。

2. 生效级工具集 —— 由 Session 绑定的 Workspace 决定
真正决定"一个 Agent 这次对话能用哪些工具"的，是 Session.config.workspace_id（_session.py:116）：
class SessionConfig:
    workspace_id: str   # ← 权威绑定，创建 session 时确定
    chat_model_config / knowledge_config / ...
聊天时，ChatService 先 get_workspace(workspace_id) 解析出工作区，再调用 get_toolkit(...) 把工具集装起来（_chat.py:523,640）。所以 tools/mcp/skills 的"生效边界" = 该 Session 所用 Workspace 的边界，而非 Agent 本身。
二、工具集装配（get_toolkit）的隔离逻辑
get_toolkit（_toolkit.py:37）按来源分层装配，每一层都带 user_id/agent_id/session_id 上下文：
工具来源	隔离依据
Workspace 内建工具（Bash/Read/Write…）	workspace.list_tools() → 该 workspace 内
规划工具（Task*）	全局内置，但作用于当前 session 任务列表
后台任务（ToolStop）	background_task_manager.list_tools(session_id=...) 按 session 隔离
定时任务（Schedule*）	scheduler_manager.list_tools(user_id, agent_id, ...) 按 user+agent
团队工具（TeamCreate/AgentCreate/…）	按 session 的 team 角色（leader/worker）变体（_toolkit.py:186）
自定义扩展（extra_factory）	extra_factory(user_id, agent_id, session_id) 工厂按三元组动态产出
Skills	workspace.list_skills() → 该 workspace 内
MCPs	workspace.list_mcps() → 该 workspace 内
关键点：MCP/Skill 不是用户装了就全局可用，而是"用户库是候选池"，Workspace 的 .mcp/.skill 文件声明了实际挂载哪些（注释 _mcp.py:13：库记录是"期望状态 desired state"，workspace 文件是"实际状态 actual state"，由 app 收敛两者）。enabled 开关（_mcp.py:86）控制是否进入 workspace。

三、Agent 与 Workspace 的连接路径
Agent (配置: system_prompt/context/react)
   │ 被某个用户调用
   ▼
Session (user_id, agent_id, workspace_id)   ← 隔离的核心枢纽
   │
   ▼
Workspace (由 workspace_manager 按隔离策略分配)
   ├── 内建 tools (本 workspace 沙箱)
   ├── mcps   ← 仅挂载该 workspace 声明的、且属于该 user 的 MCP
   └── skills ← 仅挂载该 workspace 声明的、且属于该 user 的 Skill
所以隔离是"用户库 + workspace 挂载"双重过滤：即使 A 和 B 都装了同名 github MCP，A 的 workspace 只会挂 A 的 github，B 的只会挂 B 的。

Agent 的 tools/mcp/skills 不直接挂在 Agent 上，而是分两层隔离：

库级隔离：MCP/Skill 安装记录带 user_id，用户只能管理/看到自己的；
生效级隔离：实际工具集由 Session → Workspace 解析得到，workspace 只挂载"本 workspace 声明 + 属于该 user"的 MCP/Skill，配合 middlewares/extra_factory 的 (user, agent, session) 动态工厂，实现精确到会话的工具隔离。
Agent 之间要"共享工具"，只能通过团队（共享同一 workspace/leader 权限上下文）实现，无法越权访问其他用户的 MCP/Skill 库。

一个 Agent 的多个 Session 是如何隔离的？
Agent 与 Session 是一对多关系：一个 Agent 配置（system_prompt 等）是"模板"，每次新开对话就生成一个独立 Session，带着各自的运行时状态。隔离体现在四个维度。

一、身份隔离：每个 Session 有独立主键
SessionRecord 的标识由三元组唯一确定（_session.py:149）：
class SessionRecord(_RecordBase):
    user_id: str       # 归属用户
    agent_id: str      # 属于哪个 Agent
    source / team_id / config / state  # 各自独立
所以即使同一个 Agent、同一个用户，每开一个 Session 都是一条独立存储记录，彼此不共享任何可变数据。
二、运行时状态隔离：state 各自独立
每个 Session 拥有自己的 AgentState（_session.py:175）：
state: AgentState = Field(default_factory=AgentState)  # 每会话独立构造
对话历史（context）：存在 state.context，session A 的消息不会跑到 session B。
任务列表 / 计划：state.tasks 也是 per-session。
迭代计数、待处理工具调用、压缩标志等运行时变量，全部封在各自的 state 里。

三、工作区与工具隔离：每个 Session 绑定独立 Workspace
SessionConfig.workspace_id 是"权威绑定"（_session.py:116）：
workspace_id: str  # 创建 session 时由 workspace_manager 按隔离策略分配
创建 session 时，workspace_manager.assign_workspace_id(...) 会为每个 session 分配独立工作目录（沙箱）。于是：

session A 的 Bash 写文件、Read/Write 操作，落在 A 的 workspace 目录，不会触达 session B 的文件。
各自的 MCP/Skill 挂载、工具集（get_toolkit 以 session_id 为键）也都是 per-session 的。
注释明确说：workspace_id 是 get_workspace、list_mcps、团队工具的缓存键（_session.py:120）——天然把工具/文件边界切到 session 粒度。
五、隔离维度总览
维度	隔离单位	存储介质
记录身份	user_id + agent_id + session_id 三元组	Storage
对话历史/状态	各自 AgentState	Storage（upsert_session）
文件/工具沙箱	各自 workspace_id	WorkspaceManager
事件流/锁/取消	session_id 分区	MessageBus（Redis）
运行并发	session 级 run-lock	MessageBus
一个 Agent 的多个 Session 通过 "独立 SessionRecord + 独立 AgentState + 独立 workspace_id + 消息总线按 session_id 分区锁" 四层隔离：配置共享、状态各自为政。无论同一用户开多少会话，还是服务扩展到多节点，每个 Session 的对话历史、文件沙箱、工具挂载、执行锁都互不干扰。
每个 Session 的工作区是怎么生成的？
工作区（Workspace）的生成由 WorkspaceManager + IsolationPolicy 共同决定，核心分两步：先算 workspace_id（纯函数，无 IO）→ 再按 id 重建/复用 LocalWorkspace（带 TTL 缓存）。
一、第一步：workspace_id 是怎么"派发"的
assign_workspace_id（_base.py:53）是个纯函数，不碰存储、不碰磁盘，它根据 IsolationPolicy 把 (user_id, agent_id, session_id) 映射成一个 id：
隔离粒度 IsolationPolicy	workspace_id 算法	含义
PER_SESSION（每会话）	_generate_id() 随机 UUID	每个 session 一个独立工作区
PER_AGENT（每 Agent，默认）	blake2b("user::agent")	同一用户同一 Agent 的所有 session 共享一个工作区
PER_USER（每用户）	blake2b("user::")	该用户所有 Agent 的所有 session 共享一个工作区
# 默认 PER_AGENT 示例
blake2b("alice::agent_1234", digest_size=8).hexdigest()  # 确定、可复现
注意这个 id 是确定性哈希（不是随机），所以"同一 user+agent"任何时候算出来都是同一个 id——这就是工作区可跨 session 复用、又能按粒度隔离的原理。
二、第二步：id → 实际工作区（缓存 + 重建）
拿到 workspace_id 后，get_workspace（_local_workspace_manager.py:70）负责变出真实 LocalWorkspace，用三层锁+缓存防并发重复创建：
workdir = os.path.join(self._basedir, agent_id)   # 本地工作区目录是确定性的
ws = LocalWorkspace(
    workspace_id=workspace_id,
    workdir=workdir,
    default_mcps=self._default_mcps,    # 注入该工作区的 MCP
    skill_paths=self._skill_paths,      # 注入该工作区的 Skill 路径
)
await ws.initialize()                   # 启动 MCP 客户端等
本地模式下 workdir 直接是 basedir/agent_id（无需查存储），所以工作区是"按需懒重建"的：

缓存命中：workspace_id 已在 _cache，直接返回（刷新访问时间）。
缓存未命中：在锁内 initialize() 创建，workdir 确定性 → 即便多节点重建也指向同一目录。
TTL 驱逐：空闲超过 ttl（默认 3600s）的工作区被弹出缓存；close() 时异步并行关闭（避免慢速 MCP stdio 关闭阻塞其他请求）。
三、关键点：session 与工作区是"绑定"而非"每次新建"
工作区 id 在创建 Session 时就写入 SessionConfig.workspace_id（_session.py:116），之后聊天全程用这一个 id 调 get_workspace。所以：

PER_AGENT（默认）：你同一个 Agent 开 3 个 session，如果都是同 user+agent，算出的 workspace_id 相同 → 三个 session 共享同一文件系统工作区 + 同一批 MCP/Skill。这回答了上一条：默认下"多 session 隔离"主要依赖各自的 AgentState 和消息总线锁，而文件沙箱是共享的。
PER_SESSION：每个 session 是独立随机 id → 文件、MCP、Skill 全部分开，隔离最彻底。

每个 Session 的工作区由 WorkspaceManager.assign_workspace_id 按 IsolationPolicy 算出 workspace_id（默认 PER_AGENT 用 blake2b(user::agent) 确定性哈希），再由 get_workspace 用带 TTL 的缓存懒重建出 LocalWorkspace 并注入该工作区的 MCP/Skill/工作目录；id 在创建 Session 时固化进 SessionConfig，后续聊天全程复用。因此工作区粒度（per-session / per-agent / per-user）完全由隔离策略开关控制，而非每次随机新建。

一个 Agent 的多个 Session 是如何隔离的？
Agent 与 Session 是一对多关系：一个 Agent 配置（system_prompt 等）是"模板"，每次新开对话就生成一个独立 Session，带着各自的运行时状态。隔离体现在四个维度。
一、身份隔离：每个 Session 有独立主键
essionRecord 的标识由三元组唯一确定（_session.py:149）：
class SessionRecord(_RecordBase):
    user_id: str       # 归属用户
    agent_id: str      # 属于哪个 Agent
    source / team_id / config / state  # 各自独立
所以即使同一个 Agent、同一个用户，每开一个 Session 都是一条独立存储记录，彼此不共享任何可变数据。
二、运行时状态隔离：state 各自独立
每个 Session 拥有自己的 AgentState（_session.py:175）：
state: AgentState = Field(default_factory=AgentState)  # 每会话独立构造
对话历史（context）：存在 state.context，session A 的消息不会跑到 session B。
任务列表 / 计划：state.tasks 也是 per-session。
迭代计数、待处理工具调用、压缩标志等运行时变量，全部封在各自的 state 里。
三、工作区与工具隔离：每个 Session 绑定独立 Workspace
SessionConfig.workspace_id 是"权威绑定"（_session.py:116）：
workspace_id: str  # 创建 session 时由 workspace_manager 按隔离策略分配
创建 session 时，workspace_manager.assign_workspace_id(...) 会为每个 session 分配独立工作目录（沙箱）。于是：

session A 的 Bash 写文件、Read/Write 操作，落在 A 的 workspace 目录，不会触达 session B 的文件。
各自的 MCP/Skill 挂载、工具集（get_toolkit 以 session_id 为键）也都是 per-session 的。
五、隔离维度总览
维度	隔离单位	存储介质
记录身份	user_id + agent_id + session_id 三元组	Storage
对话历史/状态	各自 AgentState	Storage（upsert_session）
文件/工具沙箱	各自 workspace_id	WorkspaceManager
事件流/锁/取消	session_id 分区	MessageBus（Redis）
运行并发	session 级 run-lock	MessageBus
六、一句话总结
一个 Agent 的多个 Session 通过 "独立 SessionRecord + 独立 AgentState + 独立 workspace_id + 消息总线按 session_id 分区锁" 四层隔离：配置共享、状态各自为政。无论同一用户开多少会话，还是服务扩展到多节点，每个 Session 的对话历史、文件沙箱、工具挂载、执行锁都互不干扰。

每个 Session 的工作区是怎么生成的？
工作区（Workspace）的生成由 WorkspaceManager + IsolationPolicy 共同决定，核心分两步：先算 workspace_id（纯函数，无 IO）→ 再按 id 重建/复用 LocalWorkspace（带 TTL 缓存）。
一、第一步：workspace_id 是怎么"派发"的
assign_workspace_id（_base.py:53）是个纯函数，不碰存储、不碰磁盘，它根据 IsolationPolicy 把 (user_id, agent_id, session_id) 映射成一个 id：
隔离粒度 IsolationPolicy	workspace_id 算法	含义
PER_SESSION（每会话）	_generate_id() 随机 UUID	每个 session 一个独立工作区
PER_AGENT（每 Agent，默认）	blake2b("user::agent")	同一用户同一 Agent 的所有 session 共享一个工作区
PER_USER（每用户）	blake2b("user::")	该用户所有 Agent 的所有 session 共享一个工作区
# 默认 PER_AGENT 示例
blake2b("alice::agent_1234", digest_size=8).hexdigest()  # 确定、可复现
注意这个 id 是确定性哈希（不是随机），所以"同一 user+agent"任何时候算出来都是同一个 id——这就是工作区可跨 session 复用、又能按粒度隔离的原理。
二、第二步：id → 实际工作区（缓存 + 重建）
拿到 workspace_id 后，get_workspace（_local_workspace_manager.py:70）负责变出真实 LocalWorkspace，用三层锁+缓存防并发重复创建：
workdir = os.path.join(self._basedir, agent_id)   # 本地工作区目录是确定性的
ws = LocalWorkspace(
    workspace_id=workspace_id,
    workdir=workdir,
    default_mcps=self._default_mcps,    # 注入该工作区的 MCP
    skill_paths=self._skill_paths,      # 注入该工作区的 Skill 路径
)
await ws.initialize()                   # 启动 MCP 客户端等
本地模式下 workdir 直接是 basedir/agent_id（无需查存储），所以工作区是"按需懒重建"的：

缓存命中：workspace_id 已在 _cache，直接返回（刷新访问时间）。
缓存未命中：在锁内 initialize() 创建，workdir 确定性 → 即便多节点重建也指向同一目录。
TTL 驱逐：空闲超过 ttl（默认 3600s）的工作区被弹出缓存；close() 时异步并行关闭（避免慢速 MCP stdio 关闭阻塞其他请求）。
三、关键点：session 与工作区是"绑定"而非"每次新建"
工作区 id 在创建 Session 时就写入 SessionConfig.workspace_id（_session.py:116），之后聊天全程用这一个 id 调 get_workspace。所以：

PER_AGENT（默认）：你同一个 Agent 开 3 个 session，如果都是同 user+agent，算出的 workspace_id 相同 → 三个 session 共享同一文件系统工作区 + 同一批 MCP/Skill。这回答了上一条：默认下"多 session 隔离"主要依赖各自的 AgentState 和消息总线锁，而文件沙箱是共享的。
PER_SESSION：每个 session 是独立随机 id → 文件、MCP、Skill 全部分开，隔离最彻底。
四、实现策略对照
关注点	本地（LocalWorkspaceManager）	生产（Docker/E2B 管理器）
workspace_id 算法	同（基类共享）	同
workdir	basedir/agent_id（确定性）	容器/沙箱隔离目录
隔离强度	进程内	进程/容器级强隔离
assign 语义	完全一致	完全一致
LocalWorkspaceManager 文档也明说：本地 workdir "deterministic ... so no storage lookup is needed"，而 Docker/E2B 版则用容器实现更强的物理隔离，但id 派发逻辑复用同一 assign_workspace_id，保证策略一致。
五、一句话总结
每个 Session 的工作区由 WorkspaceManager.assign_workspace_id 按 IsolationPolicy 算出 workspace_id（默认 PER_AGENT 用 blake2b(user::agent) 确定性哈希），再由 get_workspace 用带 TTL 的缓存懒重建出 LocalWorkspace 并注入该工作区的 MCP/Skill/工作目录；id 在创建 Session 时固化进 SessionConfig，后续聊天全程复用。因此工作区粒度（per-session / per-agent / per-user）完全由隔离策略开关控制，而非每次随机新建。

Per-agent 模式下两个 session 的共享情况梳理
一、先记住三条事实链
事实 1(隔离策略):Per-agent 下,workspace_id = blake2b("user::agent"),两个 session 算出同一个值。

事实 2(本地管理器缓存):get_workspace 按 workspace_id 缓存,两个 session 命中同一个 LocalWorkspace 实例。

事实 3(本地 workdir 不变):workdir = basedir/agent_id,只看 agent_id,不随 session 也不随 workspace_id 变。

事实 4(本地 MCP/Skill 来自 workdir):看 _local_workspace.py 的 initialize()(:167)、list_skills()(:494)、_load_skills_file()(:302)——MCP 从 <workdir>/.mcp 文件读,Skill 从 <workdir>/skills/ 目录读。default_mcps/skill_paths 只在首次初始化且磁盘为空时作为种子写入(:181、:214),之后就以磁盘为准。
二、Per-agent 模式下两个 session 的共享表
维度	是否共享	原因
Session 记录 / session_id	❌ 各自独立	两个不同 SessionRecord
对话历史 state.context	❌ 各自独立	AgentState 按 session 存
任务列表 state.tasks	❌ 各自独立	按 session 存
运行锁 / 事件流	❌ 各自独立	message bus 按 session_id 分区
workspace_id	✅ 相同	blake2b(user::agent) 确定
LocalWorkspace 实例	✅ 共享	缓存命中同一实例
workdir 文件目录	✅ 共享	basedir/agent_id
Bash/Read/Write 操作的文件	✅ 共享	都在 basedir/agent_id 下
MCP 连接 / MCP 工具集	✅ 共享	同一 workspace 的 _mcps 列表
<workdir>/.mcp 文件	✅ 共享	同一物理文件
<workdir>/skills/ 目录	✅ 共享	同一物理目录
Skill 工具集	✅ 共享	同一 workspace 的 skills 列表

三、MCP/Skill 是否共享:完全共享
这是你最关心的点,明确回答:

MCP 共享:两个 session 用的是同一个 MCP 连接列表,连 <workdir>/.mcp 都是同一个文件。一个 session 里 add_mcp 添加的 MCP,另一个 session 立刻能看到(因为根本就是同一个 LocalWorkspace 实例和同一个磁盘文件)。
Skill 共享:同理,<workdir>/skills/ 是同一目录,add_skill 安装的 skill 在两个 session 间共享。
所以 Per-agent 模式下:你不能让"一个 session 用某 MCP、另一个不用" —— 这是设计使然,不是 bug。Per-agent 的设计假设就是"同 Agent 的多次对话,共享同一套文件和工具"。
四、想"一 session 用、一 session 不用"怎么办
前面分析过的方案在 Per-agent 模式下都失效(因为根本就是同一 workspace),必须先打破 workspace 共享,具体两条路:

切 PER_SESSION:每个 session 独立 workspace_id → 独立 LocalWorkspace 实例 → MCP 连接独立(但本地 workdir 仍共享 → <workdir>/.mcp 文件还是同一个 → MCP 配置层面仍会串,需要子类化把 workdir 也按 workspace_id 隔离)。
显式传不同 workspace_id:效果同上,但更可控。

er-agent 模式下,两个 session 的记录/对话/状态/锁是各自独立的;但 workspace_id、LocalWorkspace 实例、workdir 目录、MCP 列表、<workdir>/.mcp 文件、skills/ 目录、Skill 列表全部共享——所以 MCP 和 Skill 完全共享,无法在 Per-agent 下"一个 session 用、一个不用"。要做到 MCP/Skill 按会话隔离,必须切 PER_SESSION 或显式传不同 workspace_id(且本地模式还要额外子类化把 workdir 也按 id 隔离,否则 .mcp/skills 文件仍会串)。

Docker / 沙箱模式下的共享情况梳理
