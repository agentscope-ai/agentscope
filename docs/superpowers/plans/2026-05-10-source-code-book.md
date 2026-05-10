# AgentScope 源码分析书 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 写一本 34 章的 AgentScope 源码分析书，读者从 bug 修理工成长为架构贡献者。

**Architecture:** 4 卷递进结构。卷一用单次请求追踪线叙事（ch01-ch10），卷二拆解设计模式（ch11-ch18），卷三手把手扩展实战（ch19-ch26），卷四设计权衡讨论（ch27-ch34）。全书以"天气查询 Agent"为贯穿示例。

**Tech Stack:** Markdown + Mermaid 图表。源码引用基于 `src/agentscope/` 当前版本。每章写前必须用 `grep -n` 验证行号。

**Spec:** `docs/superpowers/specs/2026-05-10-source-code-book-design.md`

---

## 文件结构

```
teaching/book/
├── README.md
├── volume-1-journey/
│   ├── ch01-toolbox.md
│   ├── ch02-message-born.md
│   ├── ch03-agent-receives.md
│   ├── ch04-memory-store.md
│   ├── ch05-retrieval-knowledge.md
│   ├── ch06-formatter.md
│   ├── ch07-model.md
│   ├── ch08-toolkit.md
│   ├── ch09-loop-return.md
│   └── ch10-journey-review.md
├── volume-2-patterns/
│   ├── ch11-module-system.md
│   ├── ch12-inheritance.md
│   ├── ch13-metaclass-hooks.md
│   ├── ch14-formatter-strategy.md
│   ├── ch15-schema-factory.md
│   ├── ch16-middleware.md
│   ├── ch17-pubsub.md
│   └── ch18-observability.md
├── volume-3-building/
│   ├── ch19-dev-setup.md
│   ├── ch20-new-tool.md
│   ├── ch21-new-model.md
│   ├── ch22-new-memory.md
│   ├── ch23-new-agent.md
│   ├── ch24-mcp-server.md
│   ├── ch25-advanced-extension.md
│   └── ch26-integration-capstone.md
├── volume-4-why/
│   ├── ch27-msg-interface.md
│   ├── ch28-no-decorator.md
│   ├── ch29-god-class.md
│   ├── ch30-compile-time-hooks.md
│   ├── ch31-typedict-union.md
│   ├── ch32-contextvar.md
│   ├── ch33-formatter-separate.md
│   └── ch34-panorama.md
└── appendix/
    ├── python-primer.md
    ├── glossary.md
    └── source-map.md
```

---

## 写作流程（每章通用）

每章执行以下 4 步：

1. **源码验证** — `grep -n` / `sed -n` 确认所有将引用的文件路径、类名、方法名、行号
2. **写作** — 按 spec 定义的章节内部结构撰写，所有代码段标注真实行号范围
3. **自检** — 对照 spec 的章节清单检查：路线图/源码入口/逐行阅读/设计一瞥/补充知识/调试实践/检查点+练习题/下一站预告（卷一）、开场场景/设计模式/难度标注（卷二）、PR 检查清单（卷三）、决策回顾/被否方案/后果/横向对比/开放问题（卷四）
4. **提交** — `git add` + `git commit`

---

## Phase 0: 基础设施

### Task 0: 创建目录结构和 README

**Files:**
- Create: `teaching/book/README.md`
- Create: all directories under `teaching/book/`

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p teaching/book/{volume-1-journey,volume-2-patterns,volume-3-building,volume-4-why,appendix}
```

- [ ] **Step 2: 写 README.md**

写 `teaching/book/README.md`，包含：
- 书名：「AgentScope 源码之旅——从一次函数调用到架构贡献者」
- 四卷简介（每卷一句话 + 读者能力）
- 章节目录（34 章 + 3 附录，每章一行标题）
- 阅读路径建议（线性 vs 按需跳读）
- 源码版本声明（基于当前 main 分支）
- 前置知识要求

- [ ] **Step 3: 提交**

```bash
git add teaching/book/README.md
git commit -m "docs: create book directory structure and README"
```

---

## Phase 1: 卷一 — 一次 agent() 调用的旅程（ch01-ch10）

### Task 1: ch01-toolbox.md — 出发前：准备你的工具箱

**Files:**
- Create: `teaching/book/volume-1-journey/ch01-toolbox.md`
- Verify: `src/agentscope/__init__.py`

**源码验证目标：**
- `agentscope.init()` 的位置和参数
- `_ConfigCls` 的 ContextVar 用法
- 主入口点（ReActAgent, Msg, OpenAIChatModel 等 from imports）

- [ ] **Step 1: 验证源码**

```bash
grep -n "def init" src/agentscope/__init__.py
grep -n "class _ConfigCls" src/agentscope/_run_config.py
grep -n "ContextVar" src/agentscope/_run_config.py
```

- [ ] **Step 2: 写 ch01-toolbox.md**

章节内部结构：
1. **路线图** — 全书全景图，高亮"出发前"阶段
2. **源码入口** — `src/agentscope/__init__.py`
3. **逐行阅读** — `init()` 函数、`_ConfigCls`、ContextVar 初始化
4. **设计一瞥** — "为什么用 ContextVar 而非全局变量？"
5. **补充知识** — ContextVar 侧边栏
6. **调试实践** — `agentscope.init(logging_level="DEBUG")`
7. **检查点** — "你现在已经理解了：AgentScope 的初始化流程和配置机制"。练习：修改 init 参数观察输出变化。
8. **下一站预告** — "消息即将诞生"

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

对照 spec 检查 8 项内部结构元素。提交。

```bash
git add teaching/book/volume-1-journey/ch01-toolbox.md
git commit -m "docs: write ch01 - toolbox and setup"
```

### Task 2: ch02-message-born.md — 第 1 站：消息诞生

**Files:**
- Create: `teaching/book/volume-1-journey/ch02-message-born.md`
- Verify: `src/agentscope/message/_message_base.py`, `src/agentscope/message/_message_block.py`

**源码验证目标：**
- `class Msg` at line 21: `__init__`, `to_dict`, `from_dict`, `get_content_blocks`
- 7 种 ContentBlock TypedDict 定义和行号
- `Base64Source`, `URLSource` 定义

- [ ] **Step 1: 验证源码**

```bash
grep -n "class Msg\|def __init__\|def to_dict\|def from_dict\|def get_content_blocks" src/agentscope/message/_message_base.py
grep -n "class TextBlock\|class ThinkingBlock\|class ImageBlock\|class AudioBlock\|class VideoBlock\|class ToolUseBlock\|class ToolResultBlock\|class Base64Source\|class URLSource" src/agentscope/message/_message_block.py
```

- [ ] **Step 2: 写 ch02-message-born.md**

内容重点：
1. 路线图：高亮"消息诞生"站
2. `Msg("user", "北京今天天气怎么样？", "user")` 的创建过程
3. Msg 的 5 个属性：name, content, role, metadata, timestamp
4. content 的两种形态：str vs list[ContentBlock]
5. 7 种 ContentBlock 逐一介绍（TextBlock, ThinkingBlock, ImageBlock, AudioBlock, VideoBlock, ToolUseBlock, ToolResultBlock）
6. 设计一瞥："为什么 ContentBlock 是 TypedDict 而非 dataclass？"
7. 补充知识：TypedDict 侧边栏
8. 检查点 + 练习：构造一个包含 TextBlock 和 ImageBlock 的 Msg

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

### Task 3: ch03-agent-receives.md — 第 2 站：Agent 收信

**Files:**
- Create: `teaching/book/volume-1-journey/ch03-agent-receives.md`
- Verify: `src/agentscope/agent/_agent_base.py`, `src/agentscope/agent/_agent_meta.py`

**源码验证目标：**
- `class AgentBase` at line 30
- `__call__` at line 448: _reply_id 设置、reply 调用、广播
- `reply` at line 197: 抽象方法
- `observe` at line 185
- `class _AgentMeta` at line 159: `_wrap_with_hooks`
- `_subscribers` 字典和广播机制

- [ ] **Step 1: 验证源码**

```bash
grep -n "class AgentBase\|async def __call__\|async def reply\|async def observe\|async def print\|_subscribers\|def broadcast" src/agentscope/agent/_agent_base.py
grep -n "class _AgentMeta\|_wrap_with_hooks" src/agentscope/agent/_agent_meta.py
```

- [ ] **Step 2: 写 ch03-agent-receives.md**

内容重点：
1. 路线图：高亮"Agent 收信"站
2. `await agent(msg)` 的第一站：`__call__` 的入口逻辑
3. Hook 系统初见：`_AgentMeta` 在类定义时包装了 `reply`/`observe`/`print`
4. 广播机制：reply 完成后通知 `_subscribers`
5. 设计一瞥："为什么用元类而不是装饰器实现 Hook？"
6. 补充知识：元类（metaclass）侧边栏
7. 调试实践：在 `__call__` 打断点
8. 检查点 + 练习：追踪 AgentBase 的 `__call__` 执行路径

目标篇幅：450-550 行

- [ ] **Step 3: 自检 + 提交**

### Task 4: ch04-memory-store.md — 第 3 站：记忆存入

**Files:**
- Create: `teaching/book/volume-1-journey/ch04-memory-store.md`
- Verify: `src/agentscope/memory/_working_memory/_base.py`, `src/agentscope/memory/_working_memory/_in_memory_memory.py`

**源码验证目标：**
- `class MemoryBase` at line 11: `add`, `delete`, `size`, `clear`, `get_memory`
- `class InMemoryMemory` at line 10: `list[tuple[Msg, list[str]]]` 内部存储
- `_compressed_summary` 状态变量
- mark 机制：`delete_by_mark`, `update_messages_mark`

- [ ] **Step 1: 验证源码**

```bash
grep -n "class MemoryBase\|async def add\|async def delete\|def size\|async def clear\|async def get_memory\|_compressed_summary" src/agentscope/memory/_working_memory/_base.py
grep -n "class InMemoryMemory\|def add\|def get_memory\|def delete\|def state_dict\|def load_state_dict" src/agentscope/memory/_working_memory/_in_memory_memory.py
```

- [ ] **Step 2: 写 ch04-memory-store.md**

内容重点：
1. 路线图：消息进入 Agent 后，第一步是存入工作记忆
2. `MemoryBase` 的 5 个抽象方法
3. `InMemoryMemory` 的内部结构：`list[tuple[Msg, list[str]]]`
4. mark 机制：消息的标记和过滤
5. `_compressed_summary`：压缩摘要
6. 设计一瞥："为什么记忆用 list[tuple] 而不是 dict？"
7. 调试实践：打印 memory.get_memory() 查看当前记忆内容
8. 检查点 + 练习：手动创建 InMemoryMemory 并 add/get_memory

目标篇幅：350-450 行

- [ ] **Step 3: 自检 + 提交**

### Task 5: ch05-retrieval-knowledge.md — 第 4 站：检索与知识

**Files:**
- Create: `teaching/book/volume-1-journey/ch05-retrieval-knowledge.md`
- Verify: `src/agentscope/memory/_long_term_memory/_long_term_memory_base.py`, `src/agentscope/rag/_knowledge_base.py`, `src/agentscope/embedding/_embedding_base.py`

**源码验证目标：**
- `LongTermMemoryBase` 的 `retrieve` 和 `record` 方法
- Mem0 和 ReMe 的实现位置
- `KnowledgeBase` 的 retrieve 流程
- `EmbeddingModelBase` 的接口

- [ ] **Step 1: 验证源码**

```bash
grep -n "class LongTermMemoryBase\|async def retrieve\|async def record" src/agentscope/memory/_long_term_memory/_long_term_memory_base.py
grep -n "class KnowledgeBase\|async def retrieve" src/agentscope/rag/_knowledge_base.py
grep -n "class EmbeddingModelBase" src/agentscope/embedding/_embedding_base.py
```

- [ ] **Step 2: 写 ch05-retrieval-knowledge.md**

内容重点：
1. 路线图：记忆存入后，Agent 要检索长期记忆和知识库
2. 长期记忆的两条路径：agent_control（Agent 主动调用工具）vs static_control（自动检索）
3. RAG 知识库：Document → Embedding → 向量存储 → 检索
4. Embedding 模型的角色：将文本转为向量
5. 设计一瞥："为什么长期记忆有两种控制模式？"
6. 调试实践：观察检索结果对推理的影响
7. 检查点 + 练习：画出一个消息从存入到被检索的完整路径

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

### Task 6: ch06-formatter.md — 第 5 站：格式转换

**Files:**
- Create: `teaching/book/volume-1-journey/ch06-formatter.md`
- Verify: `src/agentscope/formatter/_formatter_base.py`, `src/agentscope/formatter/_openai_formatter.py`, `src/agentscope/formatter/_truncated_formatter_base.py`, `src/agentscope/token/`

**源码验证目标：**
- `class FormatterBase` at line 11: `async format()`
- `class TruncatedFormatterBase` at line 19: Token 截断
- `class OpenAIChatFormatter` at line 168
- Token 计数器的接口

- [ ] **Step 1: 验证源码**

```bash
grep -n "class FormatterBase\|async def format\|def convert_tool_result_to_string" src/agentscope/formatter/_formatter_base.py
grep -n "class TruncatedFormatterBase\|class OpenAIChatFormatter" src/agentscope/formatter/_openai_formatter.py src/agentscope/formatter/_truncated_formatter_base.py
grep -n "class TokenCounterBase" src/agentscope/token/_token_base.py
```

- [ ] **Step 2: 写 ch06-formatter.md**

内容重点：
1. 路线图：记忆准备完毕，消息需要转换格式才能发给模型
2. FormatterBase → TruncatedFormatterBase → OpenAIChatFormatter 的继承链
3. `format()` 的输入（list[Msg]）和输出（list[dict]）
4. Token 截断策略：当消息超过模型上下文窗口时怎么办
5. `convert_tool_result_to_string`：多模态工具结果的文本化
6. 设计一瞥："为什么 Formatter 独立于 Model？"
7. 调试实践：打印 format() 的输出看 API 请求格式
8. 检查点 + 练习：手动调用 format() 并对比输入输出

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

### Task 7: ch07-model.md — 第 6 站：调用模型

**Files:**
- Create: `teaching/book/volume-1-journey/ch07-model.md`
- Verify: `src/agentscope/model/_model_base.py`, `src/agentscope/model/_openai_model.py`, `src/agentscope/model/_model_response.py`

**源码验证目标：**
- `class ChatModelBase` at line 13: `__call__`
- `class OpenAIChatModel` at line 71: `__call__`, `_parse_openai_stream_response`
- `class ChatResponse` at line 20: content blocks (TextBlock/ToolUseBlock/ThinkingBlock/AudioBlock)
- 流式解析的 chunk 累积逻辑

- [ ] **Step 1: 验证源码**

```bash
grep -n "class ChatModelBase\|async def __call__\|def _validate_tool_choice" src/agentscope/model/_model_base.py
grep -n "class OpenAIChatModel\|async def __call__\|def _parse_openai_stream_response\|def _structured_via_tool_call" src/agentscope/model/_openai_model.py
grep -n "class ChatResponse\|class TextBlock\|class ToolUseBlock\|class ThinkingBlock" src/agentscope/model/_model_response.py
```

- [ ] **Step 2: 写 ch07-model.md**

内容重点：
1. 路线图：格式化完毕，向 LLM 发起 HTTP 请求
2. ChatModelBase 的统一接口：输入 messages + tools + tool_choice，输出 ChatResponse
3. OpenAIChatModel 的流式解析：chunk → 累积 text/thinking/tool_calls → yield ChatResponse
4. ChatResponse 的 content blocks：TextBlock、ThinkingBlock、ToolUseBlock、AudioBlock
5. 结构化输出的两种方式：response_format 直接支持 vs _structured_via_tool_call 降级
6. 设计一瞥："为什么用 AsyncGenerator 而不是一次性返回？"
7. 补充知识：AsyncGenerator 侧边栏
8. 调试实践：打印每个 ChatResponse chunk 的类型
9. 检查点 + 练习：识别 ChatResponse 中出现了哪些 ContentBlock 类型

目标篇幅：500-600 行

- [ ] **Step 3: 自检 + 提交**

### Task 8: ch08-toolkit.md — 第 7 站：执行工具

**Files:**
- Create: `teaching/book/volume-1-journey/ch08-toolkit.md`
- Verify: `src/agentscope/tool/_toolkit.py`, `src/agentscope/tool/_response.py`, `src/agentscope/_utils/_common.py`

**源码验证目标：**
- `class Toolkit` at line 117
- `register_tool_function` at line 274
- `call_tool_function` at line 853: AsyncGenerator 接口
- `_apply_middlewares` at line 57
- `class ToolResponse` 的结构
- `_parse_tool_function` at line 339 in `_utils/_common.py`

- [ ] **Step 1: 验证源码**

```bash
grep -n "class Toolkit\|def register_tool_function\|async def call_tool_function\|def _apply_middlewares\|def create_tool_group\|def register_middleware" src/agentscope/tool/_toolkit.py
grep -n "class ToolResponse\|class ToolResponseStream" src/agentscope/tool/_response.py
grep -n "def _parse_tool_function" src/agentscope/_utils/_common.py
```

- [ ] **Step 2: 写 ch08-toolkit.md**

内容重点：
1. 路线图：模型返回了 ToolUseBlock，Agent 需要执行工具
2. ToolUseBlock 的结构：name + arguments
3. `call_tool_function` 的流程：查找函数 → 执行 → 返回 AsyncGenerator[ToolResponse]
4. ToolResponse → ToolResultBlock 的转换
5. 中间件链初见：`_apply_middlewares` 的洋葱模型
6. 工具结果存回记忆
7. 设计一瞥："为什么所有工具都统一为 AsyncGenerator 接口？"
8. 调试实践：在 call_tool_function 打断点观察参数
9. 检查点 + 练习：追踪一个工具调用从 ToolUseBlock 到 ToolResultBlock 的完整路径

目标篇幅：450-550 行

- [ ] **Step 3: 自检 + 提交**

### Task 9: ch09-loop-return.md — 第 8 站：循环与返回

**Files:**
- Create: `teaching/book/volume-1-journey/ch09-loop-return.md`
- Verify: `src/agentscope/agent/_react_agent.py`, `src/agentscope/plan/_plan_notebook.py`, `src/agentscope/token/_token_base.py`, `src/agentscope/tts/_tts_base.py`

**源码验证目标：**
- `ReActAgent.reply` at line 376: ReAct 循环的 5 阶段
- `_reasoning` at line 540: TTS 集成 (578-617), plan_notebook (546-551)
- `_acting` at line 657: finish_function 检查, finally 块
- `_summarizing` at line 725
- `_compress_memory_if_needed` at line 1015: Token 计数触发
- `PlanNotebook` at line 172: 规划子系统
- Token 计数器在压缩中的作用

- [ ] **Step 1: 验证源码**

```bash
grep -n "async def reply\|max_iters\|while\|_compress_memory\|_reasoning\|_acting\|_summarizing\|structured_model\|finish_function" src/agentscope/agent/_react_agent.py
grep -n "class PlanNotebook\|class Plan\|class SubTask" src/agentscope/plan/_plan_notebook.py src/agentscope/plan/_plan_model.py
grep -n "class TokenCounterBase\|async def count" src/agentscope/token/_token_base.py
grep -n "class TTSModelBase\|async def synthesize" src/agentscope/tts/_tts_base.py
```

- [ ] **Step 2: 写 ch09-loop-return.md**

内容重点：
1. 路线图：ReAct 循环的全景——推理→行动→推理→行动→...→返回
2. `reply()` 的 5 阶段：记忆存入 → 检索 → 循环 → 压缩 → 返回
3. `_reasoning()` 的完整流程（含 TTS 输出和 plan hint 注入）
4. `_acting()` 的工具执行和 finish_function 检查
5. 循环终止条件：无 ToolUseBlock、structured_model 满足、max_iters 到达
6. `_summarizing()`：循环超限时的兜底
7. `_compress_memory_if_needed()`：Token 计数触发的记忆压缩
8. PlanNotebook：规划子系统如何嵌入 ReAct 循环
9. 设计一瞥："为什么 ReAct 循环需要 max_iters 上限？"
10. 检查点 + 练习：画出完整 ReAct 循环的状态转换图

目标篇幅：550-650 行（全卷最长的章节）

- [ ] **Step 3: 自检 + 提交**

### Task 10: ch10-journey-review.md — 旅程复盘

**Files:**
- Create: `teaching/book/volume-1-journey/ch10-journey-review.md`

- [ ] **Step 1: 写 ch10-journey-review.md**

内容重点：
1. 完整调用链的全景图（一页 Mermaid 序列图，从 `await agent(msg)` 到 return）
2. 各站串联：每站用一句话回顾"发生了什么"
3. 10 站 → 卷二 8 章的映射表
4. 叙事转折："你已经走完了全程。现在回来拆开每一个齿轮。"
5. 检查点：全卷知识自测（5 道题，覆盖消息→Agent→记忆→模型→工具→循环）

目标篇幅：250-350 行

- [ ] **Step 2: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/
git commit -m "docs: complete volume 1 - one agent() call journey"
```

---

## Phase 2: 卷二 — 拆开每个齿轮（ch11-ch18）

### Task 11: ch11-module-system.md — 模块系统：文件的命名与导入

**Files:**
- Create: `teaching/book/volume-2-patterns/ch11-module-system.md`
- Verify: 多个 `__init__.py` 文件

**开场场景：** "你 clone 了仓库，打开 src/agentscope/ 看到一堆 _ 开头的文件，不知道从哪看起"
**难度：** 入门
**设计模式：** _前缀约定、__init__.py re-export、lazy import

- [ ] **Step 1: 验证源码**

```bash
grep -n "from.*import\|import" src/agentscope/agent/__init__.py
grep -n "from.*import\|import" src/agentscope/model/__init__.py
grep -n "from.*import\|import" src/agentscope/tool/__init__.py
```

- [ ] **Step 2: 写 ch11（按卷二章节内部结构：开场场景 → 模式讲解 → 设计一瞥 → 调试实践 → 检查点 + 练习题）**

- [ ] **Step 3: 自检 + 提交**

### Task 12: ch12-inheritance.md — 继承体系：从 StateModule 到 AgentBase

**Files:**
- Create: `teaching/book/volume-2-patterns/ch12-inheritance.md`
- Verify: `src/agentscope/module/_state_module.py`, `src/agentscope/agent/_agent_base.py`

**开场场景：** "你收到一个 bug：Agent 序列化后恢复，但记忆丢失了"
**难度：** 中等

- [ ] **Step 1: 验证源码 → Step 2: 写 ch12 → Step 3: 自检 + 提交**

### Task 13: ch13-metaclass-hooks.md — 元类与 Hook：方法调用的拦截

**Files:**
- Create: `teaching/book/volume-2-patterns/ch13-metaclass-hooks.md`
- Verify: `src/agentscope/agent/_agent_meta.py`, `src/agentscope/agent/_agent_base.py`, `src/agentscope/types/_hook.py`

**开场场景：** "你加了一行日志到 reply() 但没生效——因为 Hook 先执行了"
**难度：** 进阶

- [ ] **Step 1: 验证源码 → Step 2: 写 ch13 → Step 3: 自检 + 提交**

### Task 14: ch14-formatter-strategy.md — 策略模式：Formatter 的多态分发

**Files:**
- Create: `teaching/book/volume-2-patterns/ch14-formatter-strategy.md`
- Verify: `src/agentscope/formatter/` 全部文件

**开场场景：** "你接了一个 bug：Gemini 模型的工具调用格式不对"
**难度：** 中等

- [ ] **Step 1: 验证源码 → Step 2: 写 ch14 → Step 3: 自检 + 提交**

### Task 15: ch15-schema-factory.md — 工厂与 Schema：从函数到 JSON Schema

**Files:**
- Create: `teaching/book/volume-2-patterns/ch15-schema-factory.md`
- Verify: `src/agentscope/_utils/_common.py`, `src/agentscope/tool/_toolkit.py`

**开场场景：** "你的工具函数有嵌套的 Pydantic 参数，Schema 生成报错了"
**难度：** 进阶

- [ ] **Step 1: 验证源码 → Step 2: 写 ch15 → Step 3: 自检 + 提交**

### Task 16: ch16-middleware.md — 中间件与洋葱模型

**Files:**
- Create: `teaching/book/volume-2-patterns/ch16-middleware.md`
- Verify: `src/agentscope/tool/_toolkit.py` (lines 57-114, 853-1033)

**开场场景：** "你的工具被并发调用，需要加限流"
**难度：** 进阶

- [ ] **Step 1: 验证源码 → Step 2: 写 ch16 → Step 3: 自检 + 提交**

### Task 17: ch17-pubsub.md — 发布-订阅：多 Agent 通信

**Files:**
- Create: `teaching/book/volume-2-patterns/ch17-pubsub.md`
- Verify: `src/agentscope/pipeline/_msghub.py`, `src/agentscope/pipeline/_class.py`, `src/agentscope/pipeline/_functional.py`

**开场场景：** "两个 Agent 在 MsgHub 里收到重复消息"
**难度：** 中等

- [ ] **Step 1: 验证源码 → Step 2: 写 ch17 → Step 3: 自检 + 提交**

### Task 18: ch18-observability.md — 可观测性与持久化

**Files:**
- Create: `teaching/book/volume-2-patterns/ch18-observability.md`
- Verify: `src/agentscope/tracing/_trace.py`, `src/agentscope/session/`

**开场场景：** "Agent 跑了 10 分钟，你需要知道它卡在哪"
**难度：** 中等

- [ ] **Step 1: 验证源码 → Step 2: 写 ch18 → Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/
git commit -m "docs: complete volume 2 - design patterns"
```

---

## Phase 3: 卷三 — 造一个新齿轮（ch19-ch26）

### Task 19: ch19-dev-setup.md — 扩展准备

**Files:**
- Create: `teaching/book/volume-3-building/ch19-dev-setup.md`
- Verify: `tests/`, `.github/`, `pyproject.toml`

- [ ] **Step 1: 验证源码** — tests/ 结构、pre-commit config、pytest 配置
- [ ] **Step 2: 写 ch19** — 开发环境搭建、fork/branch 工作流、pytest 运行、pre-commit
- [ ] **Step 3: 自检 + 提交**

### Task 20: ch20-new-tool.md — 造一个新 Tool

**Files:**
- Create: `teaching/book/volume-3-building/ch20-new-tool.md`
- Verify: `src/agentscope/tool/_toolkit.py`, `src/agentscope/_utils/_common.py`

实战：数据库查询工具（同步 + 流式版本）。完整的 register → call → test 流程。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch20（含完整可运行代码示例）→ Step 3: PR 检查清单 + 提交**

### Task 21: ch21-new-model.md — 造一个新 Model Provider

**Files:**
- Create: `teaching/book/volume-3-building/ch21-new-model.md`
- Verify: `src/agentscope/model/_model_base.py`, `src/agentscope/formatter/_formatter_base.py`

实战：接入 FastLLM API。三步走：非流式 → 流式 → 结构化输出。Model + Formatter + __init__.py 注册。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch21 → Step 3: PR 检查清单 + 提交**

### Task 22: ch22-new-memory.md — 造一个新 Memory Backend

**Files:**
- Create: `teaching/book/volume-3-building/ch22-new-memory.md`
- Verify: `src/agentscope/memory/_working_memory/_base.py`, `src/agentscope/memory/_working_memory/_in_memory_memory.py`

实战：SQLite Memory。从 InMemoryMemory 改造。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch22 → Step 3: PR 检查清单 + 提交**

### Task 23: ch23-new-agent.md — 造一个新 Agent 类型

**Files:**
- Create: `teaching/book/volume-3-building/ch23-new-agent.md`
- Verify: `src/agentscope/agent/_agent_base.py`, `src/agentscope/agent/_react_agent_base.py`

实战：Plan-Execute Agent。Agent 定制的三个层次：配置 / 继承 / 全新。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch23 → Step 3: PR 检查清单 + 提交**

### Task 24: ch24-mcp-server.md — 集成 MCP Server

**Files:**
- Create: `teaching/book/volume-3-building/ch24-mcp-server.md`
- Verify: `src/agentscope/mcp/_client_base.py`, `src/agentscope/mcp/_stdio_stateful_client.py`

实战：对接一个本地 MCP Server。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch24 → Step 3: PR 检查清单 + 提交**

### Task 25: ch25-advanced-extension.md — 高级扩展：中间件与分组

**Files:**
- Create: `teaching/book/volume-3-building/ch25-advanced-extension.md`
- Verify: `src/agentscope/tool/_toolkit.py`

实战：限流中间件 + 按场景分组 + Agent Skill (SKILL.md)。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch25 → Step 3: PR 检查清单 + 提交**

### Task 26: ch26-integration-capstone.md — 终章：集成实战

**Files:**
- Create: `teaching/book/volume-3-building/ch26-integration-capstone.md`

把 ch20-ch23 造的 Tool/Model/Memory/Agent 集成为完整系统，跑通端到端测试。

- [ ] **Step 1: 写 ch26** — 集成代码 + 端到端测试 + 完整 PR 流程演示
- [ ] **Step 2: 自检 + 提交**

```bash
git add teaching/book/volume-3-building/
git commit -m "docs: complete volume 3 - building new modules"
```

---

## Phase 4: 卷四 — 为什么要这样设计（ch27-ch34）

### Task 27: ch27-msg-interface.md — 消息为什么是唯一接口

- [ ] **决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题 → 提交**

### Task 28: ch28-no-decorator.md — 为什么不用装饰器注册工具

- [ ] **决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题 → 提交**

### Task 29: ch29-god-class.md — 上帝类 vs 模块拆分

- [ ] **决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题 → 提交**

### Task 30: ch30-compile-time-hooks.md — 编译期 Hook vs 运行时 Hook

- [ ] **决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题 → 提交**

### Task 31: ch31-typedict-union.md — 为什么 ContentBlock 是 Union

- [ ] **决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题 → 提交**

### Task 32: ch32-contextvar.md — 为什么用 ContextVar

- [ ] **决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题 → 提交**

### Task 33: ch33-formatter-separate.md — 为什么 Formatter 独立于 Model

- [ ] **决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题 → 提交**

### Task 34: ch34-panorama.md — 架构的全景与边界

- [ ] **依赖图复盘 → 边界模糊处 → 演进方向 → 提交**

```bash
git add teaching/book/volume-4-why/
git commit -m "docs: complete volume 4 - design tradeoffs"
```

---

## Phase 5: 附录 + 最终审查

### Task 35: 附录三篇

**Files:**
- Create: `teaching/book/appendix/python-primer.md`
- Create: `teaching/book/appendix/glossary.md`
- Create: `teaching/book/appendix/source-map.md`

- [ ] **Step 1: 写 python-primer.md** — async/await、TypedDict、元类、ContextManager、dataclass 速查
- [ ] **Step 2: 写 glossary.md** — 全书术语的中英文对照表
- [ ] **Step 3: 写 source-map.md** — src/agentscope/ 下每个文件的一句话职责 + 行数
- [ ] **Step 4: 提交**

```bash
git add teaching/book/appendix/
git commit -m "docs: complete appendices"
```

### Task 36: 最终审查

- [ ] **Step 1: 源码引用批量验证** — 从全书 grep 所有 `src/agentscope/.*:[0-9]+` 引用，逐个 sed -n 验证
- [ ] **Step 2: 术语一致性检查** — 确认全书术语与 glossary.md 一致
- [ ] **Step 3: 交叉引用检查** — 确认所有章节间的"详见第 X 章"引用正确
- [ ] **Step 4: 更新 README.md** — 确认目录与实际文件一致
- [ ] **Step 5: 最终提交**

```bash
git add teaching/book/
git commit -m "docs: complete source code analysis book - final review"
```

---

## 自检

**1. Spec 覆盖：** 34 章 + 3 附录 = 37 个文件，全部映射到 Task 0-36。spec 中的每个章节都有对应任务。设计一瞥、检查点、练习题、难度标注等增强项都写入了各 Task 的步骤描述。

**2. 占位符扫描：** 无 TBD/TODO。卷四 Task 27-34 的步骤描述较简略（因为卷四格式统一，每章都是相同的 5 段结构），但每章都明确了具体的设计决策主题。

**3. 类型一致性：** 所有源码引用已在写计划前通过 `grep -n` 验证。关键行号：Msg:21, AgentBase:30, ReActAgent:98, Toolkit:117, ChatModelBase:13, FormatterBase:11, MemoryBase:11, StateModule:20, PlanNotebook:172, MsgHub:14, OpenAIChatModel:71, OpenAIChatFormatter:168, _AgentMeta:159。
