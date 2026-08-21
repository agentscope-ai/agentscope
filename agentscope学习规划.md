# 企业级 Agent 全体系知识 —— 深度学习与面试手册

> 以 AgentScope 2.0 源码为锚点，辐射整个企业级 Agent 知识体系。
> 每个模块包含：**知识全景 → 设计决策与权衡 → 方案对比 → 面试高频考点 → 易忽略的陷阱**

---

## 〇、知识体系总图

```
企业级 Agent 全体系
│
├── 第一层：核心引擎（Agent 是怎么"思考"的）
│   ├── 1. ReAct 循环 —— 推理-行动的核心引擎
│   ├── 2. 消息系统 —— Agent 的"语言"
│   └── 3. 事件系统 —— Agent 的"神经系统"
│
├── 第二层：能力扩展（Agent 是怎么"做事"的）
│   ├── 4. 工具系统 —— Agent 的"手"
│   ├── 5. 模型适配层 —— Agent 的"大脑接口"
│   └── 6. 中间件系统 —— Agent 的"可插拔能力"
│
├── 第三层：安全与治理（Agent 是怎么"守规矩"的）
│   ├── 7. 权限管理 —— 谁能做什么
│   ├── 8. 工作空间与沙箱 —— 在哪里做
│   └── 9. 安全与防御 —— 防止被恶意利用
│
├── 第四层：生产级工程（Agent 是怎么"上线"的）
│   ├── 10. 上下文与记忆管理 —— 长对话不丢信息
│   ├── 11. 多 Agent 编排 —— 团队协作
│   ├── 12. 服务化与多租户 —— 规模化部署
│   └── 13. 可观测性与评估 —— 怎么知道好不好
│
└── 第五层：前沿协议与生态
    ├── 14. MCP 协议 —— 工具标准化接入
    ├── 15. A2A 协议 —— Agent 间互操作
    └── 16. 行业框架横向对比
```

---

## 一、ReAct 循环 —— 推理-行动的核心引擎

### 1.1 知识全景：Agent 核心循环的演进

```
CoT (2022)          → 只思考不行动，纯推理链
  ↓
ReAct (2022.10)     → 思考+行动交替，但串行
  ↓
Reflexion (2023.03) → ReAct + 自我反思 + 经验记忆
  ↓
LATS (2023.12)      → ReAct + 树搜索（MCTS），多路径探索
  ↓
Plan-and-Solve (2023) → 先全局规划，再逐步执行
  ↓
Graph-based (2024+)  → DAG/状态机编排，支持并行+条件分支
```

### 1.2 AgentScope 的 ReAct 实现

AgentScope 的 ReAct 循环位于 `src/agentscope/agent/_agent.py` 的 `_reply_impl`：

```
Step 1: 输入校验（新消息 or 继续暂停的回复）
Step 2: 处理输入 → 追加到 state.context → 发送 ReplyStartEvent
Step 3: ReAct 循环 (while cur_iter < max_iters)
  ├── 3.1 _check_next_action(): 无待执行 tool calls → 退出
  ├── 3.2 _reasoning(): 调用 LLM
  │     ├── 前：compress_context() 上下文压缩
  │     ├── 中：流式返回 text/thinking/tool_calls
  │     └── 后：无 tool calls → 发送 ReplyEndEvent → 退出
  ├── 3.3 _batch_tool_calls(): 执行工具
  │     ├── 分类：concurrent vs sequential
  │     └── 拦截：遇 RequireUserConfirmEvent → 暂停
  └── 3.4 cur_iter += 1
Step 4: 达到 max_iters → ExceedMaxItersEvent
```

### 1.3 设计决策与权衡

**Q: 为什么选 ReAct 而不是 Plan-and-Execute？**

| 维度 | ReAct | Plan-and-Execute |
|------|-------|-----------------|
| 规划方式 | 隐式（每步都重新推理） | 显式（先出完整计划） |
| 适应性 | 高（每步可根据结果调整） | 低（计划可能过时） |
| Token 消耗 | 高（每轮都带完整上下文） | 中（计划可复用） |
| 延迟 | 高（串行多轮） | 中（可并行执行子任务） |
| 适合场景 | 探索性任务、工具调用密集 | 目标明确、步骤可预测 |

**AgentScope 的选择理由**：ReAct 更适合通用场景，因为企业级 Agent 面对的任务类型不可预测，需要每步根据工具返回动态调整。Plan-and-Execute 更适合固定流程的自动化。

**Q: 为什么 max_iters 是必要的？**

- 防止死循环（Agent 反复调用同一工具、互相推诿）
- 控制成本（每轮消耗 Token）
- 生产环境必须有"兜底退出"机制

### 1.4 方案对比：循环检测与熔断

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **max_iters 硬限制** | 设定最大循环次数 | 简单可靠 | 粗暴，可能误杀 |
| **动作去重** | 检测连续 N 次相同动作 | 精准 | 需要定义"相同" |
| **Token 预算** | 限制总 Token 消耗 | 控制成本 | 复杂任务可能不够 |
| **超时熔断** | 总时间超过阈值退出 | 保护资源 | 长任务被截断 |
| **LLM 自判断** | 让模型自己决定是否继续 | 灵活 | 不可靠，可能误判 |

**最佳实践**：组合使用 max_iters + Token 预算 + 超时熔断，三层防护。

### 1.5 面试高频考点

> **🎯 Q1: 请详细解释 ReAct 框架的工作原理。它比 CoT 有什么优势？**
>
> 答题要点：CoT 只推理不行动，无法与外部环境交互；ReAct 将 Thought→Action→Observation 交替执行，能根据工具返回动态调整策略。

> **🎯 Q2: Agent 陷入死循环怎么办？你在架构上如何设计熔断机制？**
>
> 答题要点：三层防护（max_iters + Token 预算 + 超时），动作去重检测，错误信息作为 Observation 返回让模型自修正。

> **🎯 Q3: ReAct 的串行执行导致延迟很高，如何优化？**
>
> 答题要点：① 并行工具调用（无依赖的工具同时执行）② 模型路由（简单意图用小模型拦截）③ 缓存（相同参数的工具结果缓存）④ 流式输出（减少感知延迟）

> **⚠️ 易忽略点**：ReAct 的"每步重新推理"意味着每轮都要把完整上下文发给 LLM，这是 Token 成本高的根本原因。上下文压缩不是优化，是必需品。

---

## 二、消息系统 —— Agent 的"语言"

### 2.1 知识全景：Agent 消息建模的三种范式

| 范式 | 代表 | 特点 |
|------|------|------|
| **纯字符串** | 早期 ChatGPT API | 简单，但无法表达结构化信息 |
| **ContentBlock 联合类型** | AgentScope、Anthropic API | 类型安全，支持多模态 |
| **自定义 DSL** | LangChain Message 体系 | 灵活但学习成本高 |

### 2.2 AgentScope 的消息模型

```
Msg (基类)
├── UserMsg      → content: list[TextBlock | DataBlock]
├── AssistantMsg  → content: list[TextBlock | DataBlock | ToolCallBlock | ToolResultBlock | ThinkingBlock]
├── SystemMsg     → content: list[TextBlock]
└── 字段: id, name, role, metadata, created_at, finished_at, usage
```

**关键设计**：
- 每种 Msg 子类通过 `model_validator` 限制可包含的 Block 类型（类型安全）
- `ToolCallBlock` 有完整的状态机：`NEW → ASKING → ALLOWED → SUBMITTED → FINISHED`
- 每条 Msg 可选携带 `Usage`（input_tokens, output_tokens），支持 Token 消耗追溯

### 2.3 设计决策与权衡

**Q: 为什么用 ContentBlock 联合类型而不是纯字符串？**

| 需求 | 纯字符串 | ContentBlock |
|------|---------|-------------|
| 多模态（图片/音频） | 需要 hack（base64 嵌入文本） | 原生 DataBlock 支持 |
| 工具调用 | 需要正则解析 | ToolCallBlock 结构化 |
| 流式渲染 | 无法区分文本/思考/工具 | 每种 Block 有独立事件流 |
| 类型安全 | 无 | Pydantic 校验 |
| 前端渲染 | 困难 | 按 Block 类型分别渲染 |

**Q: 为什么 ToolCallBlock 需要状态机？**

因为工具调用不是"调用→返回"这么简单，企业级场景需要：
- 人工确认（HITL）：调用前暂停等待审批
- 外部执行：工具不在本地，需要提交到远程系统
- 断点续传：服务重启后能恢复未完成的调用
- 审计追踪：每个状态转换都有记录

### 2.4 面试高频考点

> **🎯 Q4: 如何设计 Agent 的消息系统以支持多模态和工具调用？**
>
> 答题要点：ContentBlock 联合类型，每种 Block 有独立的 type 标识和字段；通过 Pydantic 的 model_validator 在模型层做类型约束。

> **🎯 Q5: 工具调用的生命周期如何管理？如果服务重启了怎么办？**
>
> 答题要点：ToolCallState 状态机（NEW→ASKING→ALLOWED→SUBMITTED→FINISHED），状态持久化到存储层，重启后从持久化状态恢复。

> **⚠️ 易忽略点**：ToolCallBlock 和 ToolResultBlock 必须成对出现，上下文压缩时不能拆散这对关系，否则 LLM 会看到"调用了工具但没有结果"的幻觉。

---

## 三、事件系统 —— Agent 的"神经系统"

### 3.1 知识全景：为什么需要细粒度事件？

Agent 的执行不是"输入→输出"的黑盒，企业级场景需要：
- **实时渲染**：前端展示打字机效果、思考过程、工具进度
- **可观测性**：追踪每一步的耗时、Token 消耗
- **人机协作**：在特定点暂停等待人工输入
- **断点续传**：从任意事件点恢复执行

### 3.2 AgentScope 的 30+ 事件类型

```
【回复级】     REPLY_START / REPLY_END
【模型调用级】 MODEL_CALL_START / MODEL_CALL_END
【内容块级】   TEXT_BLOCK_START/DELTA/END, THINKING_BLOCK_START/DELTA/END, DATA_BLOCK_START/DELTA/END
【工具调用级】 TOOL_CALL_START/DELTA/END, TOOL_RESULT_START/TEXT_DELTA/DATA_DELTA/END
【人机协作级】 REQUIRE_USER_CONFIRM / USER_CONFIRM_RESULT
              REQUIRE_EXTERNAL_EXECUTION / EXTERNAL_EXECUTION_RESULT
【生命周期级】 EXCEED_MAX_ITERS / HINT_BLOCK / CUSTOM
```

**设计模式**：
- 统一 `EventBase` 基类（id, created_at, metadata）
- `type: Literal[EventType.XXX]` 实现穷举模式匹配
- `reply_stream` 返回 `AsyncGenerator[AgentEvent, None]`，支持 SSE 推送

### 3.3 设计决策与权衡

**Q: 为什么不用 WebSocket 而用 SSE（Server-Sent Events）？**

| 维度 | SSE | WebSocket |
|------|-----|-----------|
| 方向 | 服务端→客户端（单向） | 双向 |
| 复杂度 | 低（HTTP 协议） | 高（需要握手、心跳） |
| 重连 | 自动重连 | 需要手动实现 |
| 适用场景 | 事件流推送 | 实时双向通信 |

Agent 的事件流本质上是**服务端单向推送**，SSE 更简单且天然支持重连。人工输入通过独立的 HTTP POST 接口实现，不需要双向通道。

**Q: 为什么事件粒度这么细（START/DELTA/END）？**

- 前端需要逐字渲染（DELTA），不是等完整文本
- 可观测性需要精确计时（从 START 到 END 的耗时）
- 中间件需要在特定点拦截（如 TEXT_BLOCK_END 后做内容过滤）

### 3.4 面试高频考点

> **🎯 Q6: 如何实现 Agent 执行过程的实时可视化？**
>
> 答题要点：细粒度事件系统 + AsyncGenerator + SSE 推送。每个内容块有 START/DELTA/END 事件，前端按事件类型分别渲染。

> **🎯 Q7: Agent 需要人工确认时，架构上怎么处理？**
>
> 答题要点：发送 REQUIRE_USER_CONFIRM 事件 → 暂停执行（挂起协程）→ 前端展示确认 UI → 用户操作后通过 HTTP POST 提交 → 发送 USER_CONFIRM_RESULT 事件 → 恢复执行。关键是状态持久化，确保服务重启后能恢复。

> **⚠️ 易忽略点**：事件系统不仅仅是"通知前端"，它是 Agent 内部各模块解耦的关键。中间件通过监听事件实现拦截，权限系统通过事件实现暂停/恢复，可观测性通过事件实现全链路追踪。

---

## 四、工具系统 —— Agent 的"手"

### 4.1 知识全景：工具系统的演进

```
硬编码函数调用    → 每个任务写死调用逻辑
  ↓
Function Calling  → LLM 输出结构化调用，系统解析执行
  ↓
Tool Registration → 工具动态注册，LLM 根据描述选择
  ↓
MCP 协议 (2024+)  → 标准化工具接入协议，一次集成到处使用
  ↓
Agentic Tool Use  → Agent 自主决定何时需要新工具、动态加载
```

### 4.2 AgentScope 的工具三层架构

```
Toolkit (注册中心 + 调度器)
├── 内置工具: Bash, Read, Write, Edit, Glob, Grep
├── Task 工具: TaskCreate, TaskList, TaskGet, TaskUpdate
├── MCP 工具: 通过 MCPClient 从 MCP Server 动态注册
├── Function 工具: FunctionTool 适配器包装 Python 函数
├── Skill 系统: 通过 SkillViewer 工具间接调用
└── ToolGroup: 工具分组管理，支持动态激活/停用
```

**ToolBase 关键属性**：
```python
name: str                    # 工具名称（给 LLM 看）
description: str             # 工具描述（给 LLM 看，决定何时调用）
input_schema: dict           # JSON Schema 参数定义
is_concurrency_safe: bool    # 是否可并发执行
is_read_only: bool           # 是否只读（影响权限判断）
is_external_tool: bool       # 是否需要外部执行
```

### 4.3 设计决策与权衡

**Q: 为什么工具要区分 concurrent 和 sequential？**

| 类型 | 例子 | 原因 |
|------|------|------|
| concurrent | Read, Grep, Glob | 只读操作，无副作用，可并行 |
| sequential | Write, Edit, Bash | 有副作用，顺序敏感（如先写后读） |

AgentScope 的实现：`_batch_tool_calls` 将一批工具调用按 `is_concurrency_safe` 分组，concurrent 组用 `asyncio.gather` 并行，sequential 组逐个执行。

**Q: FunctionTool vs MCPTool 的区别？**

| 维度 | FunctionTool | MCPTool |
|------|-------------|---------|
| 来源 | 本地 Python 函数 | 远程 MCP Server |
| Schema 提取 | 从函数签名+docstring 自动提取 | 从 MCP 协议的 inputSchema 获取 |
| 执行方式 | 直接调用 | 通过 MCP 协议远程调用 |
| 连接管理 | 无 | Stateful（持久 session）/ Stateless（每次新建） |
| 命名 | 函数名 | `mcp__{server}__{tool}` 格式化 |

**Q: 为什么需要 Skill 系统？不直接给 Agent 更多工具？**

Skill 是"元工具"——它不直接执行操作，而是给 Agent 一份"操作手册"。Agent 先读取 Skill 的指令，然后按指令组合使用其他工具。

好处：
- 工具数量不膨胀（100 个 Skill ≠ 100 个工具，Skill 复用已有工具）
- 指令可更新（改 Skill 文件即可，不改代码）
- 上下文友好（只在需要时加载 Skill 指令到上下文）

### 4.4 方案对比：工具发现与路由

当工具库庞大（100+ 工具）时，全部放入 Prompt 会超出上下文限制。

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **全量注入** | 所有工具描述放入 Prompt | 简单，LLM 有完整信息 | Token 消耗大，选择困难 |
| **ToolGroup 动态激活** | 按阶段激活不同工具组 | 控制 Token，聚焦 | 需要预定义分组 |
| **RAG 检索** | 根据用户意图检索 Top-K 工具 | 动态，精准 | 检索可能遗漏 |
| **小模型路由** | 先用小模型判断用哪些工具 | 精准 | 额外延迟和成本 |
| **层级路由** | 先选类别，再选具体工具 | 可扩展 | 需要设计层级 |

### 4.5 面试高频考点

> **🎯 Q8: Tool Calling 的底层实现逻辑是什么？如果 LLM 传了错误参数怎么办？**
>
> 答题要点：LLM 根据 JSON Schema 输出结构化参数 → 系统解析（json_repair 容错）→ jsonschema 验证 → 执行。参数错误处理：① json_repair 自动修复格式错误 ② Schema 验证拦截类型错误 ③ 将错误信息作为 ToolResult 返回让 LLM 自修正。

> **🎯 Q9: 工具库有 100+ 个 API，超出 Context Window 怎么办？**
>
> 答题要点：动态工具选择——根据用户意图用 RAG 或语义检索选出 Top-K 相关工具注入 Prompt。AgentScope 的 ToolGroup 机制支持按阶段动态激活/停用工具组。

> **🎯 Q10: 如何设计工具的安全执行？**
>
> 答题要点：① 权限引擎（allow/deny/ask 规则）② 沙箱隔离（Docker/E2B）③ 只读工具自动放行，写入工具需确认 ④ 高危操作 Human-in-the-loop。

> **⚠️ 易忽略点**：工具的 `description` 质量直接决定 LLM 能否正确选择和使用工具。描述模糊会导致误调用，描述过短会导致 LLM 不知道何时该用。这是生产中最常见的调优点。

> **⚠️ 易忽略点**：工具结果的截断。AgentScope 对过长的工具结果做截断（`tool_result_limit`），因为超长结果会占满上下文窗口，导致后续推理质量下降。但截断可能丢失关键信息，需要平衡。

---

## 五、模型适配层 —— Agent 的"大脑接口"

### 5.1 知识全景：多模型适配的挑战

企业级 Agent 必须支持多模型：
- 不同任务用不同模型（推理用强模型，分类用弱模型）
- 模型提供商 API 格式各异（OpenAI、Anthropic、DashScope...）
- 需要降级/容灾（主模型挂了切备用模型）
- 需要统一接口（上层代码不关心底层模型）

### 5.2 AgentScope 的适配架构

```
ChatModelBase (统一接口)
├── __call__()           → 聊天调用
├── count_tokens()       → Token 计数
├── generate_structured_output() → 结构化输出
│
├── OpenAIChatModel / AnthropicChatModel / DashScopeChatModel / ...
│   └── 各自的 Formatter
│       ├── ChatFormatter（单 Agent）
│       └── MultiAgentFormatter（多 Agent，标记发送者身份）
│
└── ModelConfig
    ├── max_retries: int        → 重试次数
    └── fallback_model: ChatModelBase → 降级模型
```

### 5.3 设计决策与权衡

**Q: 为什么 Chat 和 MultiAgent Formatter 要分开？**

单 Agent 场景：消息只需 `role` + `content`。
多 Agent 场景：需要在消息中标记"谁是发送者"，否则 LLM 不知道消息来自哪个 Agent。

例如 MultiAgentFormatter 会在 content 中注入 `[From: Agent_A]` 这样的标识。

**Q: 模型降级的策略有哪些？**

| 策略 | 原理 | 适用场景 |
|------|------|---------|
| **固定降级链** | A→B→C 依次尝试 | 简单可靠 |
| **负载均衡** | 按权重分配到多个模型 | 成本优化 |
| **能力路由** | 根据任务复杂度选模型 | 成本+质量平衡 |
| **地域降级** | 就近模型→远端模型 | 延迟优化 |

### 5.4 面试高频考点

> **🎯 Q11: 如何设计一个支持多模型提供商的 Agent 系统？**
>
> 答题要点：统一接口（ChatModelBase）+ 适配器模式（Formatter）+ 凭证隔离（Credential）。上层代码只依赖抽象接口，通过配置切换具体模型。

> **🎯 Q12: 主模型 API 挂了怎么办？**
>
> 答题要点：ModelConfig 的 max_retries + fallback_model。重试策略用指数退避（Exponential Backoff），降级模型可以是不同提供商的等价模型。

> **⚠️ 易忽略点**：不同模型的 Token 计数方式不同（有的按字符，有的按 BPE），`count_tokens` 必须用对应模型的分词器，否则上下文压缩的阈值判断会不准。

---

## 六、中间件系统 —— Agent 的"可插拔能力"

### 6.1 知识全景：为什么需要中间件？

Agent 的核心逻辑（ReAct 循环）应该是稳定的，但企业级场景需要不断叠加能力：
- RAG（检索增强）
- 长期记忆
- 全链路追踪
- 回复预算控制
- 文字转语音
- ...

如果把这些都写进 Agent 核心类，代码会膨胀到不可维护。中间件模式让这些能力**可插拔**。

### 6.2 AgentScope 的洋葱模型

```
请求进入
  │
  ▼
on_reply 中间件1 → on_reply 中间件2 → ...
  │
  ▼
on_reasoning 中间件 → ...
  │
  ▼
on_model_call 中间件 → ... → [实际 LLM 调用]
  │
  ▼
on_acting 中间件 → ... → [实际工具执行]
  │
  ▼
响应返回（逐层回传）
```

**5 个钩子点**：

| 钩子 | 拦截目标 | 典型用途 |
|------|---------|---------|
| `on_reply` | 整个回复流程 | RAG、长期记忆、预算控制 |
| `on_reasoning` | 推理阶段 | 推理增强、思考链过滤 |
| `on_model_call` | 模型原始调用 | 模型路由、缓存、限流 |
| `on_acting` | 工具执行 | 工具结果过滤、日志 |
| `on_system_prompt` | 系统提示词 | 动态注入上下文信息 |

**实现方式**：递归闭包链（Recursive Closure Chain）
```python
async def execute_chain(index=0, **kwargs):
    if index >= len(middlewares):
        async for item in self._impl(**kwargs):
            yield item
    else:
        mw = middlewares[index]
        async def next_handler(**new_kwargs):
            async for item in execute_chain(index + 1, **new_kwargs):
                yield item
        async for item in mw.on_xxx(next_handler=next_handler):
            yield item
```

### 6.3 设计决策与权衡

**Q: 为什么用洋葱模型而不是 Pipeline（线性管道）？**

| 维度 | 洋葱模型 | Pipeline |
|------|---------|----------|
| 拦截能力 | 可在调用前后都做处理 | 只能在单侧处理 |
| 短路能力 | 中间件可直接返回，不调后续 | 需要特殊处理 |
| 复杂度 | 高（递归闭包） | 低（线性链） |
| 典型应用 | Koa.js, AgentScope | Express.js, LangChain |

洋葱模型的优势：**每个中间件既能"前置处理"（在 next 之前），也能"后置处理"（在 next 之后）**。例如 TracingMiddleware 在 LLM 调用前记录开始时间，调用后记录耗时。

**Q: 中间件的执行顺序重要吗？**

非常重要！例如：
- RAGMiddleware 必须在 TracingMiddleware 之前（否则追踪不到 RAG 检索）
- 预算控制必须在最外层（否则无法控制内部中间件的 Token 消耗）
- 权限检查必须在工具执行之前

### 6.4 方案对比：可扩展性架构

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **中间件/洋葱模型** | 递归闭包链 | 灵活，前后拦截 | 调试困难，顺序敏感 |
| **装饰器模式** | @decorator 包装函数 | 简洁 | 不运行时，不灵活 |
| **Hook/Listener** | 事件驱动回调 | 解耦 | 执行顺序难控制 |
| **AOP 代理** | 字节码/元编程拦截 | 无侵入 | Python 中实现复杂 |

### 6.5 面试高频考点

> **🎯 Q13: 如何在不修改 Agent 核心代码的情况下添加 RAG 能力？**
>
> 答题要点：中间件模式。实现 RAGMiddleware，在 on_reply 钩子中拦截请求，先做检索，将检索结果注入 system prompt 或 context，然后调用 next_handler 继续正常流程。

> **🎯 Q14: 中间件的执行顺序为什么重要？举例说明。**
>
> 答题要点：洋葱模型中，外层中间件包裹内层。如果预算控制中间件在内层，它就无法统计外层中间件消耗的 Token。权限检查必须在工具执行之前，否则拦截不到。

> **⚠️ 易忽略点**：`is_implemented` 惰性检测是性能优化。如果每次循环都检查中间件是否实现了某个钩子，会有大量不必要的方法查找。AgentScope 在构造时预计算，将中间件按钩子类型预分类。

---

## 七、权限管理 —— 谁能做什么

### 7.1 知识全景：Agent 权限的特殊挑战

传统软件的权限是"用户→角色→资源"，Agent 的权限更复杂：
- **LLM 决策的不确定性**：模型可能"幻觉"出危险操作
- **工具调用的链式风险**：单个安全操作组合后可能不安全
- **Prompt Injection**：恶意输入诱导 Agent 执行危险操作
- **自主性 vs 安全性**的矛盾：越自主越高效，但也越危险

### 7.2 AgentScope 的 5 种权限模式

| 模式 | 行为 | 适用场景 |
|------|------|---------|
| **DEFAULT** | 每个操作需明确允许，否则询问 | 默认模式，最安全 |
| **ACCEPT_EDITS** | 自动允许工作目录内的文件读写 | 有人在场，快速迭代 |
| **EXPLORE** | 只允许只读操作 | 探索代码库、规划 |
| **BYPASS** | 跳过所有安全检查（仅遵 deny） | 沙箱环境 |
| **DONT_ASK** | 将所有 ASK 转为 DENY | 无人值守后台 |

**检查流程**（以 DEFAULT 为例）：
```
1. 匹配 deny_rules → 命中则 DENY
2. 匹配 ask_rules → 命中则 ASK
3. tool.check_permissions() → 工具自检
4. 匹配 allow_rules → 命中则 ALLOW
5. 默认 → ASK（含 pass-through 逻辑）
```

### 7.3 设计决策与权衡

**Q: 为什么 deny 优先于 allow？（先检查 deny 再检查 allow）**

这是安全领域的基本原则：**拒绝优先于允许**。如果 allow 优先，那么一条宽泛的 allow 规则可能覆盖掉关键的 deny 规则，导致安全漏洞。

**Q: 为什么 BYPASS 模式还要遵守 deny 规则？**

因为 deny 规则通常包含"绝对不能做"的操作（如删除系统文件、访问敏感数据）。即使是完全信任的环境，也应该有"底线"。这是**纵深防御**的体现。

**Q: 5 种模式是否足够？有没有遗漏？**

可能的补充：
- **LEARN 模式**：记录所有操作但不拦截，事后审计（用于新场景的规则学习）
- **GRADUAL 模式**：根据操作频率自动调整权限（常用操作自动 allow）

### 7.4 面试高频考点

> **🎯 Q15: 如何防止 Agent 被 Prompt Injection 攻击后执行危险操作？**
>
> 答题要点：① 权限引擎的 deny 规则兜底 ② 指令与数据分离（XML 标签隔离）③ 高危操作强制 HITL ④ 输出过滤 ⑤ 最小权限原则。

> **🎯 Q16: 对于高风险操作（删除数据、转账），如何设计 Human-in-the-loop？**
>
> 答题要点：权限引擎判断为 ASK → 发送 RequireUserConfirmEvent → 暂停执行 → 前端展示操作详情 → 用户确认/拒绝 → 恢复执行。关键是状态持久化（支持用户关闭页面后回来继续）。

> **⚠️ 易忽略点**：权限规则中的 glob 匹配（如 `*.py`）看似简单，但路径穿越（`../../etc/passwd`）是常见漏洞。必须在匹配前做路径规范化（normalize）。

---

## 八、工作空间与沙箱 —— 在哪里做

### 8.1 知识全景：沙箱隔离的必要性

Agent 能执行代码、操作文件，如果不隔离：
- 恶意代码可能破坏宿主系统
- 多租户场景下租户间互相影响
- Agent 可能误删关键文件

### 8.2 AgentScope 的三层隔离

```
WorkspaceBase (抽象基类)
├── LocalWorkspace   → 本地文件系统（开发环境）
├── DockerWorkspace  → Docker 容器隔离（生产环境）
└── E2BWorkspace     → E2B 云端沙箱（最安全）
```

每层 Workspace 对应一个 Backend（执行引擎）：
```
BackendBase
├── LocalBackend   → 本地进程执行
├── DockerBackend  → Docker 容器内执行
└── E2BBackend     → E2B 云端执行
```

### 8.3 方案对比：沙箱技术

| 技术 | 隔离级别 | 启动速度 | 资源开销 | 安全性 |
|------|---------|---------|---------|--------|
| **进程隔离** | 低（共享内核） | 最快 | 最低 | 最低 |
| **Docker** | 中（namespace+cgroup） | 秒级 | 中 | 中 |
| **gVisor** | 高（用户态内核） | 秒级 | 中 | 高 |
| **Firecracker microVM** | 高（硬件虚拟化） | 亚秒级 | 低 | 很高 |
| **E2B 云沙箱** | 高 | 快 | 按需 | 高 |
| **WASM** | 高（沙箱指令集） | 毫秒级 | 最低 | 高 |

**选择建议**：
- 开发环境：Local（快速迭代）
- 内部生产：Docker（够用，运维成熟）
- 面向外部用户：E2B/Firecracker（强隔离）
- 极致性能：WASM（但生态不成熟）

### 8.4 面试高频考点

> **🎯 Q17: Agent 执行用户代码时如何保证安全？**
>
> 答题要点：沙箱隔离（Docker/microVM）+ 网络出站限制 + 资源配额（CPU/内存/时间）+ 文件系统只读挂载（除工作目录外）。

> **⚠️ 易忽略点**：Docker 不是真正的安全沙箱！默认配置下容器可以逃逸。生产环境需要：禁用 privileged 模式、限制 capabilities、使用 seccomp profile、只读根文件系统。

---

## 九、安全与防御 —— 防止被恶意利用

### 9.1 知识全景：Agent 的攻击面

```
攻击面
├── LLM 层
│   ├── Prompt Injection（直接/间接）
│   ├── 幻觉导致错误决策
│   └── 越狱（Jailbreak）
├── 工具层
│   ├── 越权访问（BOLA/IDOR）
│   ├── SSRF（通过 Agent 调用内网 API）
│   └── 参数污染
├── 数据层
│   ├── 数据投毒（RAG 中注入虚假信息）
│   └── 敏感信息泄露（PII 通过 RAG 检索输出）
└── 基础设施层
    ├── 资源耗尽（死循环消耗 Token/计算）
    └── 供应链攻击（恶意 MCP Server / 恶意工具）
```

### 9.2 防御矩阵

| 攻击类型 | 防御手段 |
|---------|---------|
| **Prompt Injection** | 指令与数据分离（XML 标签）、输入分类器（LLM Guard）、输出过滤 |
| **越权访问** | 最小权限原则、OAuth scope 限制、工具级权限引擎 |
| **SSRF** | 网络出站白名单、禁止访问内网 IP |
| **数据投毒** | RAG 数据源签名验证、检索结果可信度评估 |
| **资源耗尽** | max_iters + Token 预算 + 超时熔断 + 速率限制 |
| **供应链攻击** | MCP Server 签名验证、工具代码审计 |

### 9.3 面试高频考点

> **🎯 Q18: 区分 Direct Prompt Injection 和 Indirect Prompt Injection。**
>
> 答题要点：Direct 是用户直接在输入中注入恶意指令；Indirect 是通过外部数据源（网页、邮件、RAG 文档）间接注入，Agent 检索后"中毒"。Indirect 更危险，因为更难检测。

> **🎯 Q19: 如何设计 Agent 的安全架构？**
>
> 答题要点：纵深防御——① 输入层（分类器过滤）② 权限层（deny/allow 规则）③ 执行层（沙箱隔离）④ 输出层（PII 脱敏、敏感词过滤）⑤ 审计层（全链路日志）。

> **⚠️ 易忽略点**：Agent 的"记忆"也是攻击面。如果长期记忆被投毒（如通过 RAG 注入虚假信息），Agent 会在后续对话中持续使用错误信息，且很难被发现。

---

## 十、上下文与记忆管理 —— 长对话不丢信息

### 10.1 知识全景：记忆的三层架构

```
┌─────────────────────────────────────────┐
│  短期记忆 (Working Memory)               │
│  = 当前 Context Window                   │
│  包含：对话历史 + 系统提示 + 工具结果      │
├─────────────────────────────────────────┤
│  中期记忆 (Compressed Memory)            │
│  = 摘要 (Summary)                        │
│  包含：历史对话的压缩摘要                  │
├─────────────────────────────────────────┤
│  长期记忆 (Long-term Memory)             │
│  = 向量数据库 / 知识图谱 / 关系型数据库    │
│  包含：用户偏好、历史经验、领域知识          │
└─────────────────────────────────────────┘
```

### 10.2 AgentScope 的上下文压缩

```python
class ContextConfig:
    trigger_ratio: float = 0.8      # Token 达到 context_size 的 80% 时触发
    reserve_ratio: float = 0.1      # 保留 10% 最新上下文不压缩
    tool_result_limit: int = 50000  # 单个工具结果最大 token 数
```

**压缩流程**：
1. Token 计数 → 判断是否超过 trigger_ratio
2. 从后往前扫描，找到压缩/保留分界点
3. 边界处理：不拆散 tool_call/tool_result 对
4. LLM 结构化摘要（task_overview, current_state, important_discoveries, next_steps）
5. 可选 Offload：压缩内容持久化到文件

### 10.3 方案对比：上下文管理策略

| 方案 | 原理 | 优点 | 缺点 |
|------|------|------|------|
| **滑动窗口** | 只保留最近 N 轮 | 简单 | 丢失早期关键信息 |
| **LLM 摘要** | 用模型生成摘要 | 保留语义 | 额外成本，可能丢细节 |
| **RAG 召回** | 按需检索历史 | 精准 | 检索可能遗漏 |
| **Map-Reduce** | 分段摘要再合并 | 处理超长文本 | 多次 LLM 调用 |
| **MemGPT 虚拟内存** | Agent 自主管理"换入/换出" | 灵活 | 实现复杂 |
| **向量摘要** | 将上下文编码为向量 | 紧凑 | 信息损失大 |

### 10.4 设计决策与权衡

**Q: 为什么 AgentScope 用 LLM 摘要而不是滑动窗口？**

滑动窗口会丢失早期关键信息（如用户需求描述）。LLM 摘要能保留语义要点，虽然有一次额外调用成本，但能显著提高长对话的任务完成率。

**Q: 为什么压缩时不能拆散 tool_call/tool_result 对？**

如果 LLM 看到"调用了 search('xxx')"但没有结果，它会"幻觉"出结果，导致后续推理基于错误信息。

**Q: "Lost in the Middle" 问题是什么？**

LLM 对 Prompt 开头和结尾的信息注意力更高，中间部分容易被忽略。解决方案：重要信息放在首尾、用 Reranker 重排检索结果。

### 10.5 面试高频考点

> **🎯 Q20: 长对话场景下如何避免上下文溢出？**
>
> 答题要点：① 触发式压缩（达到阈值时 LLM 摘要）② 工具结果截断 ③ Offload（大结果持久化到文件，上下文中只保留引用）④ 保留最新部分不压缩。

> **🎯 Q21: 长上下文模型（如 Gemini 1M tokens）出现后，RAG 还有必要吗？**
>
> 答题要点：有必要。① 长上下文的"大海捞针"准确率下降 ② 推理成本与上下文长度正相关 ③ RAG 在实时性、减少幻觉方面不可替代。趋势是 Long-context RAG（两者结合）。

> **🎯 Q22: 如何设计 Agent 的长期记忆？**
>
> 答题要点：① 写入时机（对话结束时/重要事件发生时）② 存储格式（向量/知识图谱/结构化）③ 检索策略（语义检索+时间衰减）④ 记忆冲突处理（新信息覆盖旧信息 vs 保留历史）。

> **⚠️ 易忽略点**：上下文压缩本身也会引入幻觉——LLM 生成的摘要可能不准确，丢失关键细节。这是"用幻觉解决幻觉"的悖论。缓解方法：摘要时保留具体数字和实体名、用结构化 Schema 约束摘要内容。

---

## 十一、多 Agent 编排 —— 团队协作

### 11.1 知识全景：5 种编排模式

```
1. 路由/分发模式 (Router)
   用户 → Router Agent → 专家 Agent A / B / C

2. 层级/树状模式 (Hierarchical)
   Manager Agent → Worker A, Worker B → Sub-worker A1, A2

3. 流水线模式 (Sequential/Pipeline)
   Agent A → Agent B → Agent C（输出作为下一个的输入）

4. 协作/辩论模式 (Collaborative)
   Agent A ↔ Agent B（多轮对话，互相纠错）

5. 图/状态机模式 (Graph/State Machine)
   DAG 编排，支持并行、条件分支、循环
```

### 11.2 方案对比

| 模式 | 适用场景 | 优点 | 缺点 |
|------|---------|------|------|
| **Router** | 客服、综合助手 | 简单，职责清晰 | Router 是单点故障 |
| **Hierarchical** | 大型项目、复杂报告 | 可扩展，分工明确 | 通信开销大 |
| **Sequential** | 内容创作、数据处理 | 简单，可预测 | 不灵活，无法回退 |
| **Collaborative** | 代码审查、创意 | 质量高（互相纠错） | 延迟高，可能死循环 |
| **Graph** | 复杂业务逻辑 | 最灵活 | 实现复杂 |

### 11.3 AgentScope 的多 Agent 支持

AgentScope 通过消息总线（Redis Pub/Sub）实现多 Agent 通信：
- 每个 Agent 有独立的 State
- 通过 `EventProjector` 实现跨会话事件投影
- MultiAgentFormatter 在消息中标记发送者身份

### 11.4 面试高频考点

> **🎯 Q23: 多 Agent 系统中如何解决状态同步和上下文共享？**
>
> 答题要点：① 共享内存/黑板模式（Blackboard Pattern）② 消息总线（Redis Pub/Sub）③ 每个 Agent 独立 State + 事件投影。关键问题：Context Window 爆炸——用摘要机制控制共享上下文的大小。

> **🎯 Q24: Agent 之间互相推诿或死循环怎么办？**
>
> 答题要点：① 全局超时和最大轮次限制 ② 每个 Agent 有独立的 max_iters ③ 引入"裁判 Agent"判断是否陷入死循环 ④ 动作去重检测。

> **🎯 Q25: MCP 和 A2A 的区别是什么？（极高频）**
>
> 答题要点：MCP 解决 Agent 与工具/数据的连接（纵向集成），A2A 解决 Agent 与 Agent 的协作（横向互操作）。MCP 让 Agent 拥有"手和眼"，A2A 让 Agent 拥有"同事"。

> **⚠️ 易忽略点**：多 Agent 不是银弹。很多场景下单 Agent + 好的 Prompt + 合适的工具就够了。多 Agent 引入的通信开销、状态同步复杂度、调试难度往往被低估。

---

## 十二、服务化与多租户 —— 规模化部署

### 12.1 AgentScope 的 AgentService 架构

```python
def create_app(
    storage: StorageBase,              # 存储后端（Redis/SQL）
    message_bus: MessageBus,           # 消息总线
    workspace_manager: WorkspaceManagerBase,
    extra_middlewares: ...,            # 额外 Agent 中间件工厂
    extra_agent_tools: ...,            # 额外 Agent 工具工厂
    custom_agent_cls: ...,             # 自定义 Agent 类
) -> FastAPI:
```

**多租户架构**：
```
User (租户)
├── Session 1
│   ├── Agent State 1
│   └── Agent State 2
├── Session 2
│   └── Agent State 3
└── ...
```

### 12.2 面试高频考点

> **🎯 Q26: 如何设计一个多租户的 Agent 服务？**
>
> 答题要点：① 租户隔离（独立 State、独立 Workspace）② 存储隔离（按 tenant_id 分区）③ 资源配额（每租户的 Token/并发限制）④ 可扩展性（工厂模式动态注入中间件和工具）。

> **⚠️ 易忽略点**：多租户场景下，一个租户的 Agent 死循环可能消耗大量资源，影响其他租户。必须有 per-tenant 的速率限制和 Token 预算。

---

## 十三、可观测性与评估 —— 怎么知道好不好

### 13.1 知识全景：Agent 可观测性的特殊性

传统微服务的可观测性（Metrics/Logs/Traces）不够，Agent 需要：
- **Prompt/Response 追踪**：每次 LLM 调用的完整输入输出
- **Token 消耗追踪**：按用户/会话/任务维度
- **工具调用链路**：参数、结果、耗时
- **推理过程追踪**：思考链（CoT）、决策依据
- **评估指标**：任务完成率、工具调用准确率、推理效率

### 13.2 评估框架

| 维度 | 指标 | 工具 |
|------|------|------|
| **检索质量** | Context Precision, Context Recall | RAGAS |
| **生成质量** | Faithfulness（忠实度）, Answer Relevance | RAGAS, TruLens |
| **工具调用** | Precision, Recall, F1 | 自建评估 |
| **端到端** | 任务完成率, 平均交互轮数 | 自建评估 |
| **成本** | Token/任务, 延迟 P50/P99 | OpenTelemetry |
| **过程质量** | 中间推理步骤合理性 | LLM-as-a-Judge |

### 13.3 面试高频考点

> **🎯 Q27: 如何评估一个 AI Agent 的效果？**
>
> 答题要点：分层评估——① 检索层（Precision/Recall）② 工具调用层（选择准确率、参数正确率）③ 推理层（LLM-as-a-Judge）④ 端到端（任务完成率、用户满意度）⑤ 成本层（Token/任务、延迟）。

> **🎯 Q28: Agent 的 Bad Case 如何排查？**
>
> 答题要点：全链路追踪（OpenTelemetry/LangSmith）→ 定位是检索问题（召回率低）还是推理问题（LLM 选错工具/参数错误）还是工具问题（API 返回异常）→ 针对性优化。

> **⚠️ 易忽略点**：Agent 的评估不能只看最终结果。一个"成功"的任务可能走了 20 步（成本极高），一个"失败"的任务可能前 15 步都正确（只是最后一步工具 API 超时）。过程评估和根因分析同样重要。

---

## 十四、MCP 协议 —— 工具标准化接入

### 14.1 知识全景

MCP（Model Context Protocol）由 Anthropic 提出，被称为"AI 世界的 USB-C 接口"。

**核心架构**：
```
Host (AI 应用)
├── Client 1 ←→ MCP Server A (GitHub)
├── Client 2 ←→ MCP Server B (数据库)
└── Client 3 ←→ MCP Server C (文件系统)
```

**MCP Server 暴露的能力**：
- **Tools**：可调用的函数
- **Resources**：可读取的数据
- **Prompts**：提示词模板

### 14.2 AgentScope 的 MCP 集成

```python
class MCPTool(ToolBase):
    is_mcp: bool = True
    mcp_name: str  # MCP Server 名称

    # 工具名格式化：mcp__{server}__{tool}
    # 支持 Stateful（持久 session）和 Stateless（每次新建）两种模式
    # 从 annotations.readOnlyHint 提取 is_read_only
```

### 14.3 面试高频考点

> **🎯 Q29: MCP 协议解决了什么问题？没有它之前怎样？**
>
> 答题要点：MCP 之前，每个 AI 应用需要为每个工具单独写集成代码（M×N 问题）。MCP 标准化了接口，工具提供商只需实现一次 MCP Server，任何支持 MCP 的 AI 应用都能使用（M+N 问题）。

> **🎯 Q30: MCP 的安全模型是怎样的？**
>
> 答题要点：① 本地运行，Server 不暴露到公网 ② 细粒度权限控制（每个 Tool 可独立配置权限）③ 模型不能直接执行操作，必须通过 Server 层审批 ④ Host 控制 Client 的连接和断开。

> **⚠️ 易忽略点**：MCP 的 Stateful 模式下，Session 断开后状态丢失。如果 MCP Server 维护了会话状态（如数据库连接），需要在 Client 端实现重连和状态恢复。

---

## 十五、行业框架横向对比

### 15.1 主流框架对比

| 维度 | AgentScope | LangChain/LangGraph | AutoGen | CrewAI | Dify |
|------|-----------|---------------------|---------|--------|------|
| **核心范式** | ReAct + 中间件 | Graph 状态机 | 对话驱动 | 角色协作 | 可视化编排 |
| **多 Agent** | 消息总线 | LangGraph 状态图 | 群聊模式 | Crew 团队 | 工作流节点 |
| **工具系统** | Toolkit + MCP | Tool + StructuredTool | Function | Tool | 插件市场 |
| **部署方式** | 自建服务 | 自建/LangServe | 自建 | 自建 | SaaS/自建 |
| **可观测性** | OpenTelemetry | LangSmith | 有限 | 有限 | 内置 |
| **适用场景** | 通用企业级 | 复杂工作流 | 研究/对话 | 团队协作 | 快速搭建 |

### 15.2 面试高频考点

> **🎯 Q31: 你用过哪些 Agent 框架？它们的优缺点是什么？**
>
> 答题要点：不要只说"用过 LangChain"，要能说出：① 核心架构差异 ② 什么场景选什么框架 ③ 踩过的坑（如 LangChain 的抽象泄漏、AutoGen 的调试困难）。

> **🎯 Q32: 为什么不直接用 LangChain 而要自研/选 AgentScope？**
>
> 答题要点：① LangChain 的抽象层太厚，调试困难 ② 企业级需要更细粒度的控制（权限、中间件、可观测性）③ 特定场景的性能优化（如全异步架构、并发工具执行）。

---

## 十六、综合面试题与答题框架

### 16.1 系统设计题：设计一个企业级客服 Agent

**答题框架**（分层架构）：

```
1. 接入层
   - API 网关（鉴权、限流、多端适配）
   - WebSocket/SSE（实时推送）

2. 编排层
   - Router Agent（意图识别 → 分发到专家 Agent）
   - 专家 Agent（订单/退款/技术支持/通用问答）
   - ReAct 循环 + max_iters 熔断

3. 能力层
   - RAG（知识库检索）
   - 工具（订单查询 API、退款 API、工单系统）
   - 模型路由（简单问题用小模型，复杂问题用大模型）

4. 安全层
   - 权限引擎（退款操作需人工确认）
   - Prompt Injection 检测
   - PII 脱敏

5. 数据层
   - 向量数据库（知识库）
   - Redis（会话缓存、消息总线）
   - PostgreSQL（订单数据、审计日志）

6. 可观测性
   - 全链路追踪（OpenTelemetry）
   - 监控指标（任务完成率、平均响应时间、Token 消耗）
   - Bad Case 分析流程
```

### 16.2 高频追问清单

| 追问 | 考察点 |
|------|--------|
| 如果知识库检索不准怎么办？ | RAG 优化（HyDE、Reranker、Hybrid Search） |
| 如果 Agent 陷入死循环怎么办？ | 熔断机制（max_iters + Token 预算 + 超时） |
| 如果用户输入包含恶意指令怎么办？ | Prompt Injection 防御 |
| 如果退款 API 超时怎么办？ | 重试 + 降级 + 异步状态机 |
| 如何衡量这个系统的效果？ | 评估体系（任务完成率、满意度、成本） |
| 如何支持多语言？ | 模型选择 + Prompt 多语言设计 |
| 如何做灰度发布？ | A/B 测试 + 流量切分 + 回滚机制 |

---

## 十七、推荐学习路线（问题驱动）

### 阶段一：理解核心引擎
1. 运行 `examples/agent_service/main.py`，观察完整的事件流
2. 阅读 `_reply_impl`，追踪一个请求从进入到返回的完整路径
3. **问题驱动**：如果工具调用失败了，错误信息是怎么传回给 LLM 的？

### 阶段二：理解扩展机制
4. 实现一个自定义 Tool（FunctionTool），理解 Schema 提取和执行流程
5. 实现一个自定义 Middleware，理解洋葱模型的拦截机制
6. **问题驱动**：如果要加一个新的模型提供商，需要实现哪些接口？

### 阶段三：理解安全与治理
7. 阅读权限引擎，理解 5 种模式的检查逻辑
8. 阅读工作空间管理，理解沙箱隔离的实现
9. **问题驱动**：如果 Agent 被 Prompt Injection 攻击，哪些防线能拦截？

### 阶段四：理解生产级工程
10. 阅读上下文压缩流程，理解压缩/保留的边界处理
11. 阅读 AgentService，理解多租户架构
12. **问题驱动**：如果 100 个用户同时使用，系统的瓶颈在哪里？

### 阶段五：横向对比与深度思考
13. 对比 AgentScope 和 LangGraph 的架构差异
14. 理解 MCP 和 A2A 的定位和关系
15. **问题驱动**：如果让你从零设计一个 Agent 框架，你会做哪些不同的选择？

---

## 附录：面试速查表

### 必知必会概念
- ReAct (Reasoning + Acting)
- Function Calling / Tool Calling
- ContentBlock 消息建模
- 洋葱模型中间件
- Context Window 管理 / 上下文压缩
- RAG (Retrieval-Augmented Generation)
- MCP (Model Context Protocol)
- A2A (Agent-to-Agent Protocol)
- Human-in-the-Loop (HITL)
- Prompt Injection 防御
- 沙箱隔离 (Sandbox)
- 可观测性 (Observability)

### 高频面试题编号索引
| 编号 | 主题 | 关键词 |
|------|------|--------|
| Q1 | ReAct vs CoT | 推理框架 |
| Q2 | 死循环熔断 | 容错 |
| Q3 | ReAct 延迟优化 | 性能 |
| Q4 | 消息系统设计 | 多模态 |
| Q5 | 工具调用生命周期 | 状态机 |
| Q6 | 实时可视化 | 事件系统 |
| Q7 | 人工确认架构 | HITL |
| Q8 | Tool Calling 底层 | 参数容错 |
| Q9 | 工具库过大 | 动态选择 |
| Q10 | 工具安全执行 | 沙箱 |
| Q11 | 多模型适配 | 适配器模式 |
| Q12 | 模型降级 | 容灾 |
| Q13 | 无侵入添加 RAG | 中间件 |
| Q14 | 中间件顺序 | 洋葱模型 |
| Q15 | Prompt Injection 防御 | 安全 |
| Q16 | HITL 设计 | 异步状态机 |
| Q17 | 代码执行安全 | 沙箱 |
| Q18 | Direct vs Indirect Injection | 安全 |
| Q19 | 安全架构设计 | 纵深防御 |
| Q20 | 上下文溢出 | 压缩 |
| Q21 | 长上下文 vs RAG | 架构选择 |
| Q22 | 长期记忆设计 | 记忆系统 |
| Q23 | 多 Agent 状态同步 | 编排 |
| Q24 | Agent 互相推诿 | 容错 |
| Q25 | MCP vs A2A | 协议 |
| Q26 | 多租户设计 | 服务化 |
| Q27 | Agent 效果评估 | 评估 |
| Q28 | Bad Case 排查 | 可观测性 |
| Q29 | MCP 解决什么问题 | 协议 |
| Q30 | MCP 安全模型 | 安全 |
| Q31 | 框架对比 | 技术选型 |
| Q32 | 为什么不直接用 LangChain | 技术决策 |
