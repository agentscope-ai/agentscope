# AgentScope 源码分析书 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 写一本 36 章的 AgentScope 源码分析书，新手从零基础成长为能修改源码、提交 PR 的架构贡献者。

**Architecture:** 5 卷递进结构。卷零讲 LLM/Agent 基础概念（ch01-ch02），卷一用单次请求追踪线叙事（ch03-ch12），卷二拆解设计模式（ch13-ch20），卷三手把手扩展实战（ch21-ch28），卷四设计权衡讨论（ch29-ch36）。全书以"天气查询 Agent"为贯穿示例。每章都有"试一试"环节，读者从第一章就开始动手改源码。

**Tech Stack:** Markdown + Mermaid 图表。源码引用基于 `src/agentscope/` 当前版本。每章写前必须用 `grep -n` 验证类名和方法名存在。正文中不硬编码精确行号，改用 `ClassName.method_name` 引用。

**Spec:** `docs/superpowers/specs/2026-05-10-source-code-book-design.md`

---

## 文件结构

```
teaching/book/
├── README.md
├── volume-0-basics/
│   ├── ch01-what-is-llm.md
│   └── ch02-what-is-agent.md
├── volume-1-journey/
│   ├── ch03-toolbox.md
│   ├── ch04-message-born.md
│   ├── ch05-agent-receives.md
│   ├── ch06-memory-store.md
│   ├── ch07-retrieval-knowledge.md
│   ├── ch08-formatter.md
│   ├── ch09-model.md
│   ├── ch10-toolkit.md
│   ├── ch11-loop-return.md
│   └── ch12-journey-review.md
├── volume-2-patterns/
│   ├── ch13-module-system.md
│   ├── ch14-inheritance.md
│   ├── ch15-metaclass-hooks.md
│   ├── ch16-formatter-strategy.md
│   ├── ch17-schema-factory.md
│   ├── ch18-middleware.md
│   ├── ch19-pubsub.md
│   └── ch20-observability.md
├── volume-3-building/
│   ├── ch21-dev-setup.md
│   ├── ch22-new-tool.md
│   ├── ch23-new-model.md
│   ├── ch24-new-memory.md
│   ├── ch25-new-agent.md
│   ├── ch26-mcp-server.md
│   ├── ch27-advanced-extension.md
│   └── ch28-integration-capstone.md
├── volume-4-why/
│   ├── ch29-msg-interface.md
│   ├── ch30-no-decorator.md
│   ├── ch31-god-class.md
│   ├── ch32-compile-time-hooks.md
│   ├── ch33-typedict-union.md
│   ├── ch34-contextvar.md
│   ├── ch35-formatter-separate.md
│   └── ch36-panorama.md
└── appendix/
    ├── python-primer.md
    ├── glossary.md
    └── source-map.md
```

---

## 写作流程（每章通用）

每章执行以下步骤（卷零跳过源码验证）：

1. **源码验证**（卷一~四）— `grep -n "class \|def "` 确认所有将引用的文件路径、类名、方法名存在。不硬编码行号到正文，改用 `ClassName.method_name`
2. **获取官方文档 + 外部资源** — 用 `mcp__tavily__tavily_extract` 获取该章对应的 `docs.agentscope.io` 页面内容。同时参考高质量外部资源：AgentScope 1.0 论文 (arXiv:2508.16279)、Bilibili 源码带读系列、cnblogs/MarkTechPost 教程
3. **写作** — 按 spec 定义的章节内部结构撰写，代码段引用 `ClassName.method_name`。每章至少包含：1 个"试一试"环节、1 个 Mermaid 图表、1 个"源码验证日期 + commit hash"标注。卷一和卷二每章至少 1 个"设计一瞥"侧边栏。**所有参考资料（论文、官方文档、教程）直接融入正文**，不使用侧边栏+链接格式
4. **自检** — 对照 spec 检查：(a) 章节内部结构元素是否齐全 (b) "试一试"是否可执行（含无 API key 替代方案） (c) Mermaid 图表是否存在 (d) 设计一瞥侧边栏是否存在（卷一、卷二） (e) 源码引用是否使用符号名而非硬编码行号 (f) 参考资料是否全部融入正文（无悬空的外部链接侧边栏）
5. **提交** — `git add` + `git commit`（每章独立提交）

---

## Phase 0: 基础设施

### Task 0: 创建目录结构和 README

**Files:**
- Create: `teaching/book/README.md`
- Create: all directories under `teaching/book/`

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p teaching/book/{volume-0-basics,volume-1-journey,volume-2-patterns,volume-3-building,volume-4-why,appendix}
```

注意：如果 `teaching/book/` 下已有旧文件（93 个），先备份再清空，新书将逐步写入：

```bash
mv teaching/book teaching/book_old
mkdir -p teaching/book/{volume-0-basics,volume-1-journey,volume-2-patterns,volume-3-building,volume-4-why,appendix}
```

- [ ] **Step 2: 写 README.md**

写 `teaching/book/README.md`，包含：
- 书名：「AgentScope 源码之旅——从零基础到架构贡献者」
- 五卷简介（每卷一句话 + 读者能力）
- 章节目录（36 章 + 3 附录，每章一行标题）
- 阅读路径建议（线性 vs 按需跳读）
- 源码版本声明（基于当前 main 分支）
- 前置知识要求：只需要 Python 基础（函数、类、列表/字典）
- 如何使用本书：clone 仓库、pip install -e、跟着"试一试"动手

- [ ] **Step 3: 提交**

```bash
git add teaching/book/README.md
git commit -m "docs: create book directory structure and README"
```

### Task 0.5: 创建源码验证辅助脚本

- [ ] **Step 1: 创建 `teaching/book/scripts/verify_references.sh`**

脚本功能：
1. 从 Markdown 文件中提取所有 `src/agentscope/...py` 路径引用
2. 对每个路径执行 `ls` 确认文件存在
3. 提取所有反引号包裹的 `ClassName.method_name` 引用，用 `grep -rn` 确认存在于源码
4. 输出验证报告（通过/失败/警告）
5. Task 38（最终审查）可复用此脚本

---

## Phase 0.5: 卷零 — 出发前的地图（ch01-ch02）

> 不需要任何前置知识。用生活类比和动手实验讲清楚 LLM 和 Agent 是什么。卷零不涉及源码引用，跳过源码验证步骤。

### Task 1: ch01-what-is-llm.md — 什么是大模型（LLM）

**Files:**
- Create: `teaching/book/volume-0-basics/ch01-what-is-llm.md`

**知识补全：** HTTP 请求基础（发请求→收 JSON 响应）、JSON 数据结构

- [ ] **Step 1: 写 ch01-what-is-llm.md**

章节内部结构：
1. **生活类比** — "LLM 像一个超级预测下一个字的输入法"
2. **动手试试** — 用 curl 或 Python 几行代码调用 OpenAI API。如果没有 API key，提供模拟响应示例，跟随阅读即可
3. **核心概念** — 大模型是什么、Chat API、Token、流式响应、Tool Calling
4. **试一试** — 分两级：基础级（pip install agentscope，3 行脚本验证安装）、完整级（clone + pip install -e，为后续改源码做准备）
5. **检查点** — "你现在已经理解了：什么是 LLM、Chat API、Token、Tool Calling"

目标篇幅：300-500 行

- [ ] **Step 2: 自检 + 提交**

对照 spec 检查 5 项内部结构元素 + 试一试 + Mermaid 图表。

```bash
git add teaching/book/volume-0-basics/ch01-what-is-llm.md
git commit -m "docs: write ch01 - what is LLM"
```

### Task 2: ch02-what-is-agent.md — 什么是 Agent

**Files:**
- Create: `teaching/book/volume-0-basics/ch02-what-is-agent.md`

- [ ] **Step 1: 写 ch02-what-is-agent.md**

章节内部结构：
1. **生活类比** — "Agent 像一个有记忆、会查资料、能用工具的助手"
2. **动手试试** — 用天气 Agent 的完整代码跑一次，看到 Agent 自动调用工具
3. **核心概念** — Agent = 大模型 + 记忆 + 工具 + 循环；ReAct 模式；Memory/Tool/RAG
4. **试一试** — 分两级：纯本地级（用 input + if/else 模拟最简 Agent 循环，不依赖 API key）、完整级（修改天气 Agent 的 sys_prompt，观察行为变化）
5. **检查点** — "你现在已经理解了：什么是 Agent / ReAct / Memory / Tool / RAG"

目标篇幅：300-400 行

- [ ] **Step 2: 自检 + 提交**

```bash
git add teaching/book/volume-0-basics/ch02-what-is-agent.md
git commit -m "docs: write ch02 - what is Agent"
```

---

## Phase 1: 卷一 — 一次 agent() 调用的旅程（ch03-ch12）

> 跟随请求走，逐站读源码。Python 进阶知识在遇到时自然引入。每章都有"试一试"环节。

### Task 3: ch03-toolbox.md — 准备工具箱

**Files:**
- Create: `teaching/book/volume-1-journey/ch03-toolbox.md`
- Verify: `src/agentscope/__init__.py`, `src/agentscope/_run_config.py`

**源码验证目标：**
- `agentscope.init()` 的位置和参数
- `_ConfigCls` 的 ContextVar 用法
- 主入口点（ReActAgent, Msg, OpenAIChatModel 等 from imports）

**知识补全：** async/await 基础（pip install -e 已移至 ch01 试一试）

- [ ] **Step 1: 验证源码**

```bash
grep -n "def init" src/agentscope/__init__.py
grep -n "class _ConfigCls" src/agentscope/_run_config.py
grep -n "ContextVar" src/agentscope/_run_config.py
```

- [ ] **Step 2: 写 ch03-toolbox.md**

章节内部结构：
1. **路线图** — 全书全景图，高亮"出发前"阶段
2. **知识补全** — async/await 基础（`await agent(...)` 中的 await 是什么意思；事件循环在 ch07 详解）
3. **源码入口** — `src/agentscope/__init__.py`, `src/agentscope/_run_config.py`
4. **逐行阅读** — `init()` 函数、`_ConfigCls`、ContextVar 初始化
5. **调试实践** — `agentscope.init(logging_level="DEBUG")`
6. **试一试** — 修改 `init()` 的参数（如 logging_level），观察输出变化
7. **检查点** — "你现在已经理解了：AgentScope 的初始化流程和配置机制"
8. **下一站预告** — "消息即将诞生"

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch03-toolbox.md
git commit -m "docs: write ch03 - toolbox and dev setup"
```

### Task 4: ch04-message-born.md — 第 1 站：消息诞生

**Files:**
- Create: `teaching/book/volume-1-journey/ch04-message-born.md`
- Verify: `src/agentscope/message/_message_base.py`, `src/agentscope/message/_message_block.py`, `src/agentscope/_utils/_mixin.py`

**源码验证目标：**
- `class Msg`: `__init__`, `to_dict`, `from_dict`, `get_content_blocks`
- 7 种 ContentBlock TypedDict 定义
- `Base64Source`, `URLSource` 定义
- `DictMixin` 在 `_utils/_mixin.py` 中（Msg 继承此 mixin）

**知识补全：** TypedDict

- [ ] **Step 1: 验证源码**

```bash
grep -n "class Msg\|def __init__\|def to_dict\|def from_dict\|def get_content_blocks" src/agentscope/message/_message_base.py
grep -n "class TextBlock\|class ThinkingBlock\|class ImageBlock\|class AudioBlock\|class VideoBlock\|class ToolUseBlock\|class ToolResultBlock\|class Base64Source\|class URLSource" src/agentscope/message/_message_block.py
grep -n "class DictMixin" src/agentscope/_utils/_mixin.py
```

- [ ] **Step 2: 写 ch04-message-born.md**

内容重点：
1. 路线图：高亮"消息诞生"站
2. 知识补全：TypedDict 是什么
3. `Msg("user", "北京今天天气怎么样？", "user")` 的创建过程
4. Msg 的 5 个属性、content 的两种形态
5. 7 种 ContentBlock
6. 试一试：创建一个包含 TextBlock 和 ImageBlock 的 Msg，打印其结构
7. 检查点 + 练习

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch04-message-born.md
git commit -m "docs: write ch04 - message born"
```

### Task 5: ch05-agent-receives.md — 第 2 站：Agent 收信

**Files:**
- Create: `teaching/book/volume-1-journey/ch05-agent-receives.md`
- Verify: `src/agentscope/agent/_agent_base.py`, `src/agentscope/agent/_agent_meta.py`

**源码验证目标：**
- `class AgentBase` at line 30
- `__call__` at line 448
- `reply` at line 197
- `class _AgentMeta` at line 159: `_wrap_with_hooks`
- `_subscribers` 和 `_broadcast_to_subscribers` 广播机制

**知识补全：** 元类（只需知道"它可以自动包装方法"）

- [ ] **Step 1: 验证源码**

```bash
grep -n "class AgentBase\|async def __call__\|async def reply\|async def observe\|async def print\|_subscribers\|_broadcast_to_subscribers" src/agentscope/agent/_agent_base.py
grep -n "class _AgentMeta\|_wrap_with_hooks" src/agentscope/agent/_agent_meta.py
```

- [ ] **Step 2: 写 ch05-agent-receives.md**

内容重点：
1. `await agent(msg)` 的第一站
2. 元类基础：自动包装方法
3. Hook 系统初见
4. 广播机制
5. 试一试：在 `__call__` 加一行 print，观察调用链
6. 检查点 + 练习

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch05-agent-receives.md
git commit -m "docs: write ch05 - agent receives"
```

### Task 6: ch06-memory-store.md — 第 3 站：工作记忆

**Files:**
- Create: `teaching/book/volume-1-journey/ch06-memory-store.md`
- Verify: `src/agentscope/memory/_working_memory/_base.py`, `src/agentscope/memory/_working_memory/_in_memory_memory.py`

**源码验证目标：**
- `class MemoryBase` at line 11: `add`, `delete`, `size`, `clear`, `get_memory`
- `class InMemoryMemory` at line 10: `list[tuple[Msg, list[str]]]` 内部存储
- `_compressed_summary` 状态变量
- mark 机制：`delete_by_mark`, `update_messages_mark`

- [ ] **Step 1: 验证源码**

```bash
grep -n "class MemoryBase\|async def add\|async def delete\|def size\|async def clear\|async def get_memory\|_compressed_summary" src/agentscope/memory/_working_memory/_base.py
grep -n "class InMemoryMemory\|def add\|def get_memory\|def delete\|def delete_by_mark\|def update_messages_mark\|def state_dict\|def load_state_dict" src/agentscope/memory/_working_memory/_in_memory_memory.py
```

- [ ] **Step 2: 写 ch06-memory-store.md**

内容重点：
1. 工作记忆的 5 个抽象方法
2. InMemoryMemory 的内部结构
3. mark 机制和压缩摘要
4. 试一试：手动创建 InMemoryMemory，add 几条消息，用 get_memory 观察 mark 过滤效果
5. 检查点 + 练习

目标篇幅：350-450 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch06-memory-store.md
git commit -m "docs: write ch06 - working memory"
```

### Task 7: ch07-retrieval-knowledge.md — 第 4 站：检索与知识

**Files:**
- Create: `teaching/book/volume-1-journey/ch07-retrieval-knowledge.md`
- Verify: `src/agentscope/memory/_long_term_memory/_long_term_memory_base.py`, `src/agentscope/rag/_knowledge_base.py`, `src/agentscope/embedding/_embedding_base.py`

**源码验证目标：**
- `LongTermMemoryBase` 的 `retrieve` 和 `record` 方法
- `KnowledgeBase` 的 retrieve 流程
- `EmbeddingModelBase` 的接口

**知识补全：** 事件循环（为什么 async 代码需要事件循环）

- [ ] **Step 1: 验证源码**

```bash
grep -n "class LongTermMemoryBase\|async def retrieve\|async def record" src/agentscope/memory/_long_term_memory/_long_term_memory_base.py
grep -n "class KnowledgeBase\|async def retrieve" src/agentscope/rag/_knowledge_base.py
grep -n "class EmbeddingModelBase" src/agentscope/embedding/_embedding_base.py
```

- [ ] **Step 2: 写 ch07-retrieval-knowledge.md**

内容重点：
1. 长期记忆的两条路径：agent_control vs static_control
2. RAG 知识库：Document → Embedding → 向量存储 → 检索
3. 知识补全：事件循环
4. 试一试：打印检索结果，观察长期记忆如何影响推理
5. 检查点 + 练习

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch07-retrieval-knowledge.md
git commit -m "docs: write ch07 - retrieval and knowledge"
```

### Task 8: ch08-formatter.md — 第 5 站：格式转换

**Files:**
- Create: `teaching/book/volume-1-journey/ch08-formatter.md`
- Verify: `src/agentscope/formatter/_formatter_base.py`, `src/agentscope/formatter/_openai_formatter.py`, `src/agentscope/formatter/_truncated_formatter_base.py`, `src/agentscope/token/`

**源码验证目标：**
- `class FormatterBase` at line 11
- `class TruncatedFormatterBase` at line 19
- `class OpenAIChatFormatter` at line 168
- Token 计数器接口

**知识补全：** JSON Schema

- [ ] **Step 1: 验证源码**

```bash
grep -n "class FormatterBase\|async def format\|def convert_tool_result_to_string" src/agentscope/formatter/_formatter_base.py
grep -n "class TruncatedFormatterBase\|class OpenAIChatFormatter" src/agentscope/formatter/_openai_formatter.py src/agentscope/formatter/_truncated_formatter_base.py
grep -n "class TokenCounterBase" src/agentscope/token/_token_base.py
```

- [ ] **Step 2: 写 ch08-formatter.md**

内容重点：
1. FormatterBase → TruncatedFormatterBase → OpenAIChatFormatter 继承链
2. `format()` 的输入输出
3. Token 截断策略
4. 试一试：打印 format() 的输出，对比输入输出的差异
5. 检查点 + 练习

目标篇幅：400-500 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch08-formatter.md
git commit -m "docs: write ch08 - formatter"
```

### Task 9: ch09-model.md — 第 6 站：调用模型

**Files:**
- Create: `teaching/book/volume-1-journey/ch09-model.md`
- Verify: `src/agentscope/model/_model_base.py`, `src/agentscope/model/_openai_model.py`, `src/agentscope/model/_model_response.py`, `src/agentscope/model/_model_usage.py`, `src/agentscope/message/_message_block.py`

**源码验证目标：**
- `class ChatModelBase` at line 13
- `class OpenAIChatModel` at line 71
- `class ChatResponse` at line 20
- 流式解析的 chunk 累积逻辑

**知识补全：** AsyncGenerator

- [ ] **Step 1: 验证源码**

```bash
grep -n "class ChatModelBase\|async def __call__\|def _validate_tool_choice" src/agentscope/model/_model_base.py
grep -n "class OpenAIChatModel\|async def __call__\|def _parse_openai_stream_response\|def _structured_via_tool_call" src/agentscope/model/_openai_model.py
grep -n "class ChatResponse" src/agentscope/model/_model_response.py
grep -n "class TextBlock\|class ToolUseBlock\|class ThinkingBlock" src/agentscope/message/_message_block.py
grep -n "class ChatUsage" src/agentscope/model/_model_usage.py
```

- [ ] **Step 2: 写 ch09-model.md**

内容重点：
1. ChatModelBase 统一接口
2. 流式解析
3. ChatResponse 的 content blocks
4. 试一试：打印每个 ChatResponse chunk 的类型和内容
5. 检查点 + 练习

目标篇幅：500-600 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch09-model.md
git commit -m "docs: write ch09 - model call"
```

### Task 10: ch10-toolkit.md — 第 7 站：执行工具

**Files:**
- Create: `teaching/book/volume-1-journey/ch10-toolkit.md`
- Verify: `src/agentscope/tool/_toolkit.py`, `src/agentscope/tool/_response.py`, `src/agentscope/tool/_async_wrapper.py`, `src/agentscope/tool/_types.py`, `src/agentscope/_utils/_common.py`

**源码验证目标：**
- `class Toolkit`: `register_tool_function`, `call_tool_function`, `_apply_middlewares`
- `class ToolResponse`（注意：`ToolResponseStream` 不存在于当前代码库）
- `_parse_tool_function` in `_utils/_common.py`

**知识补全：** 装饰器模式

- [ ] **Step 1: 验证源码**

```bash
grep -n "class Toolkit\|def register_tool_function\|async def call_tool_function\|def _apply_middlewares\|def create_tool_group\|def register_middleware" src/agentscope/tool/_toolkit.py
grep -n "class ToolResponse" src/agentscope/tool/_response.py
grep -n "class RegisteredToolFunction\|class ToolGroup\|AgentSkill" src/agentscope/tool/_types.py
grep -n "_sync_generator_wrapper\|_async_generator_wrapper" src/agentscope/tool/_async_wrapper.py
grep -n "def _parse_tool_function" src/agentscope/_utils/_common.py
```

- [ ] **Step 2: 写 ch10-toolkit.md**

内容重点：
1. ToolUseBlock → tool function → ToolResultBlock 流程
2. 中间件链初见
3. 试一试：注册一个自定义工具函数，用 ReActAgent 调用它
4. 检查点 + 练习

目标篇幅：450-550 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch10-toolkit.md
git commit -m "docs: write ch10 - tool execution"
```

### Task 11: ch11-loop-return.md — 第 8 站：循环与返回

**Files:**
- Create: `teaching/book/volume-1-journey/ch11-loop-return.md`
- Verify: `src/agentscope/agent/_react_agent.py`, `src/agentscope/agent/_react_agent_base.py`, `src/agentscope/plan/_plan_notebook.py`, `src/agentscope/token/_token_base.py`, `src/agentscope/tts/_tts_base.py`

**源码验证目标：**
- `ReActAgent.reply`, `_reasoning`, `_acting`, `_summarizing`, `_compress_memory_if_needed`
- `ReActAgentBase` 中间层（`_react_agent_base.py`）
- `PlanNotebook`
- Token 压缩接口（`token/_token_base.py`）

**知识补全：** 结构化输出（让 LLM 返回特定格式的 JSON）

- [ ] **Step 1: 验证源码**

```bash
grep -n "async def reply\|max_iters\|while\|_compress_memory\|_reasoning\|_acting\|_summarizing\|structured_model\|finish_function" src/agentscope/agent/_react_agent.py
grep -n "class ReActAgentBase" src/agentscope/agent/_react_agent_base.py
grep -n "class PlanNotebook\|class Plan\|class SubTask" src/agentscope/plan/_plan_notebook.py src/agentscope/plan/_plan_model.py
grep -n "class TokenCounterBase\|async def count" src/agentscope/token/_token_base.py
grep -n "class TTSModelBase" src/agentscope/tts/_tts_base.py
```

- [ ] **Step 2: 写 ch11-loop-return.md**

内容重点：
1. ReAct 循环的 5 阶段
2. 循环终止条件
3. Plan/TTS/Token 压缩作为辅助子系统
4. 试一试：设置 max_iters=1，观察循环只跑一轮的行为；修改后设为 5，对比差异
5. 检查点 + 练习

目标篇幅：550-650 行（全卷最长）

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/ch11-loop-return.md
git commit -m "docs: write ch11 - loop and return"
```

### Task 12: ch12-journey-review.md — 旅程复盘

**Files:**
- Create: `teaching/book/volume-1-journey/ch12-journey-review.md`

- [ ] **Step 1: 回扫验证** — 确认卷一全景图中的类名/方法名引用一致

```bash
# 抽验卷一关键类名是否全部存在
grep -rn "class Toolkit\|class InMemoryMemory\|class OpenAIChatModel\|class ReActAgent\|class Msg\b" src/agentscope/
```
- [ ] **Step 2: 写 ch12-journey-review.md**

内容重点：
1. 完整调用链的全景图（Mermaid 序列图）
2. 各站串联回顾
3. 卷一各站 → 卷二各章的映射表
4. 叙事转折
5. 检查点：全卷知识自测（5 道题）

目标篇幅：250-350 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-1-journey/
git commit -m "docs: complete volume 1 - one agent() call journey"
```

---

## Phase 2: 卷二 — 拆开每个齿轮（ch13-ch20）

> 每章独立可跳读。以具体场景开头。设计模式知识在每章开头自然引入。

### Task 13: ch13-module-system.md — 模块系统：文件的命名与导入

**Files:**
- Create: `teaching/book/volume-2-patterns/ch13-module-system.md`
- Verify: 多个 `__init__.py` 文件

**开场场景：** "你 clone 了仓库，打开 src/agentscope/ 看到一堆 _ 开头的文件"
**难度：** 入门
**知识补全：** Python 模块与包导入机制

- [ ] **Step 1: 验证源码 → Step 2: 写 ch13（开场场景 → 知识补全 → 源码分析 → 对比反思 → 试一试 → 检查点）→ Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/ch13-module-system.md
git commit -m "docs: write ch13 - module system"
```

目标篇幅：300-400 行

### Task 14: ch14-inheritance.md — 继承体系：从 StateModule 到 AgentBase

**Files:**
- Create: `teaching/book/volume-2-patterns/ch14-inheritance.md`
- Verify: `src/agentscope/module/_state_module.py`, `src/agentscope/agent/_agent_base.py`

**开场场景：** "你收到一个 bug：Agent 序列化后恢复，但记忆丢失了"
**难度：** 中等
**知识补全：** 继承与多态

- [ ] **Step 1: 验证源码 → Step 2: 写 ch14 → Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/ch14-inheritance.md
git commit -m "docs: write ch14 - inheritance hierarchy"
```

目标篇幅：350-450 行

### Task 15: ch15-metaclass-hooks.md — 元类与 Hook：方法调用的拦截

**Files:**
- Create: `teaching/book/volume-2-patterns/ch15-metaclass-hooks.md`
- Verify: `src/agentscope/agent/_agent_meta.py`, `src/agentscope/agent/_agent_base.py`, `src/agentscope/types/_hook.py`, `src/agentscope/hooks/`

**开场场景：** "你加了一行日志到 reply() 但没生效——因为 Hook 先执行了"
**难度：** 进阶

- [ ] **Step 1: 验证源码 → Step 2: 写 ch15 → Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/ch15-metaclass-hooks.md
git commit -m "docs: write ch15 - metaclass and hooks"
```

目标篇幅：350-450 行

### Task 16: ch16-formatter-strategy.md — 策略模式：Formatter 的多态分发

**Files:**
- Create: `teaching/book/volume-2-patterns/ch16-formatter-strategy.md`
- Verify: `src/agentscope/formatter/` 全部文件

**开场场景：** "你接了一个 bug：Gemini 模型的工具调用格式不对"
**难度：** 中等
**知识补全：** 策略模式

- [ ] **Step 1: 验证源码 → Step 2: 写 ch16 → Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/ch16-formatter-strategy.md
git commit -m "docs: write ch16 - formatter strategy"
```

目标篇幅：300-400 行

### Task 17: ch17-schema-factory.md — 工厂与 Schema：从函数到 JSON Schema

**Files:**
- Create: `teaching/book/volume-2-patterns/ch17-schema-factory.md`
- Verify: `src/agentscope/_utils/_common.py`, `src/agentscope/tool/_toolkit.py`, `src/agentscope/types/_tool.py`

**开场场景：** "你的工具函数有嵌套的 Pydantic 参数，Schema 生成报错了"
**难度：** 进阶
**知识补全：** Pydantic 基础

- [ ] **Step 1: 验证源码 → Step 2: 写 ch17 → Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/ch17-schema-factory.md
git commit -m "docs: write ch17 - schema factory"
```

目标篇幅：350-450 行

### Task 18: ch18-middleware.md — 中间件与洋葱模型

**Files:**
- Create: `teaching/book/volume-2-patterns/ch18-middleware.md`
- Verify: `src/agentscope/tool/_toolkit.py` (lines 57-114, 853-1033)

**开场场景：** "你的工具被并发调用，需要加限流"
**难度：** 进阶
**知识补全：** 装饰器链

- [ ] **Step 1: 验证源码 → Step 2: 写 ch18 → Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/ch18-middleware.md
git commit -m "docs: write ch18 - middleware"
```

目标篇幅：350-450 行

### Task 19: ch19-pubsub.md — 发布-订阅：多 Agent 通信

**Files:**
- Create: `teaching/book/volume-2-patterns/ch19-pubsub.md`
- Verify: `src/agentscope/pipeline/_msghub.py`, `src/agentscope/pipeline/_class.py`, `src/agentscope/pipeline/_functional.py`

**开场场景：** "两个 Agent 在 MsgHub 里收到重复消息"
**难度：** 中等
**知识补全：** 发布-订阅模式

- [ ] **Step 1: 验证源码 → Step 2: 写 ch19 → Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/ch19-pubsub.md
git commit -m "docs: write ch19 - pubsub"
```

目标篇幅：300-400 行

### Task 20: ch20-observability.md — 可观测性与持久化

**Files:**
- Create: `teaching/book/volume-2-patterns/ch20-observability.md`
- Verify: `src/agentscope/tracing/_trace.py`, `src/agentscope/session/`

**开场场景：** "Agent 跑了 10 分钟，你需要知道它卡在哪"
**难度：** 中等

- [ ] **Step 1: 验证源码 → Step 2: 写 ch20 → Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-2-patterns/ch20-observability.md
git commit -m "docs: write ch20 - observability"
```

目标篇幅：300-400 行

```bash
git add teaching/book/volume-2-patterns/
git commit -m "docs: complete volume 2 - design patterns"
```

---

## Phase 3: 卷三 — 造一个新齿轮（ch21-ch28）

> 每章一个完整扩展任务，从开发到 PR 提交。统一内部结构：任务目标 → 设计方案 → 逐步实现 → 测试验证 → 试一试 → PR 检查清单。

### Task 21: ch21-dev-setup.md — 扩展准备

**Files:**
- Create: `teaching/book/volume-3-building/ch21-dev-setup.md`
- Verify: `tests/`, `.github/`, `pyproject.toml`

- [ ] **Step 1: 验证源码** — tests/ 结构、pre-commit config、pytest 配置
- [ ] **Step 2: 写 ch21** — 开发环境搭建、fork/branch 工作流、pytest 运行、pre-commit
- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-3-building/ch21-dev-setup.md
git commit -m "docs: write ch21 - dev setup"
```

目标篇幅：300-400 行

### Task 22: ch22-new-tool.md — 造一个新 Tool

**Files:**
- Create: `teaching/book/volume-3-building/ch22-new-tool.md`
- Verify: `src/agentscope/tool/_toolkit.py`, `src/agentscope/_utils/_common.py`

实战：数据库查询工具（同步 + 流式版本）。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch22（含完整可运行代码示例）→ Step 3: PR 检查清单 + 提交**

```bash
git add teaching/book/volume-3-building/ch22-new-tool.md
git commit -m "docs: write ch22 - new tool"
```

目标篇幅：400-500 行

### Task 23: ch23-new-model.md — 造一个新 Model Provider

**Files:**
- Create: `teaching/book/volume-3-building/ch23-new-model.md`
- Verify: `src/agentscope/model/_model_base.py`, `src/agentscope/formatter/_formatter_base.py`

实战：接入 FastLLM API。三步走：非流式 → 流式 → 结构化输出。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch23 → Step 3: PR 检查清单 + 提交**

```bash
git add teaching/book/volume-3-building/ch23-new-model.md
git commit -m "docs: write ch23 - new model provider"
```

目标篇幅：400-500 行

### Task 24: ch24-new-memory.md — 造一个新 Memory Backend

**Files:**
- Create: `teaching/book/volume-3-building/ch24-new-memory.md`
- Verify: `src/agentscope/memory/_working_memory/_base.py`, `src/agentscope/memory/_working_memory/_in_memory_memory.py`

实战：SQLite Memory。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch24 → Step 3: PR 检查清单 + 提交**

```bash
git add teaching/book/volume-3-building/ch24-new-memory.md
git commit -m "docs: write ch24 - new memory backend"
```

目标篇幅：400-500 行

### Task 25: ch25-new-agent.md — 造一个新 Agent 类型

**Files:**
- Create: `teaching/book/volume-3-building/ch25-new-agent.md`
- Verify: `src/agentscope/agent/_agent_base.py`, `src/agentscope/agent/_react_agent_base.py`

实战：Plan-Execute Agent。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch25 → Step 3: PR 检查清单 + 提交**

```bash
git add teaching/book/volume-3-building/ch25-new-agent.md
git commit -m "docs: write ch25 - new agent type"
```

目标篇幅：400-500 行

### Task 26: ch26-mcp-server.md — 集成 MCP Server

**Files:**
- Create: `teaching/book/volume-3-building/ch26-mcp-server.md`
- Verify: `src/agentscope/mcp/_client_base.py`, `src/agentscope/mcp/_stateful_client_base.py`, `src/agentscope/mcp/_stdio_stateful_client.py`

实战：对接一个本地 MCP Server。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch26 → Step 3: PR 检查清单 + 提交**

```bash
git add teaching/book/volume-3-building/ch26-mcp-server.md
git commit -m "docs: write ch26 - mcp server"
```

目标篇幅：400-500 行

### Task 27: ch27-advanced-extension.md — 高级扩展：中间件与分组

**Files:**
- Create: `teaching/book/volume-3-building/ch27-advanced-extension.md`
- Verify: `src/agentscope/tool/_toolkit.py`

实战：限流中间件 + 按场景分组 + Agent Skill。

- [ ] **Step 1: 验证源码 → Step 2: 写 ch27 → Step 3: PR 检查清单 + 提交**

```bash
git add teaching/book/volume-3-building/ch27-advanced-extension.md
git commit -m "docs: write ch27 - advanced extension"
```

目标篇幅：400-500 行

### Task 28: ch28-integration-capstone.md — 终章：集成实战

**Files:**
- Create: `teaching/book/volume-3-building/ch28-integration-capstone.md`

把 ch22-ch25 造的 Tool/Model/Memory/Agent 集成为完整系统，跑通端到端测试。

- [ ] **Step 0: 运行 ch22-ch25 的所有独立代码示例**，确认各自能跑通
- [ ] **Step 1: 写 ch28** — 集成代码 + 端到端测试 + 完整 PR 流程演示
- [ ] **Step 2: 自检 + 提交**

目标篇幅：450-550 行

```bash
git add teaching/book/volume-3-building/
git commit -m "docs: complete volume 3 - building new modules"
```

---

## Phase 4: 卷四 — 为什么要这样设计（ch29-ch36）

> 统一结构：决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题。每章需要源码验证。

### Task 29: ch29-msg-interface.md — 消息为什么是唯一接口

**Files:**
- Verify: `src/agentscope/message/_message_base.py`, `src/agentscope/agent/_agent_base.py`, `src/agentscope/model/_model_base.py`, `src/agentscope/tool/_toolkit.py`

- [ ] **Step 1: 验证源码**

```bash
grep -n "class Msg\|def __init__" src/agentscope/message/_message_base.py
grep -n "msg:" src/agentscope/agent/_agent_base.py src/agentscope/model/_model_base.py src/agentscope/tool/_toolkit.py
```

- [ ] **Step 2: 写 ch29**（决策回顾 → 被否方案 → 后果分析 → 横向对比 → 开放问题）

目标篇幅：250-350 行

- [ ] **Step 3: 自检 + 提交**

### Task 30: ch30-no-decorator.md — 为什么不用装饰器注册工具

**Files:**
- Verify: `src/agentscope/tool/_toolkit.py`, `src/agentscope/_utils/_common.py`

- [ ] **Step 1: 验证源码**

```bash
grep -n "def register_tool_function" src/agentscope/tool/_toolkit.py
grep -n "def _parse_tool_function" src/agentscope/_utils/_common.py
```

- [ ] **Step 2: 写 ch30**

目标篇幅：200-300 行

- [ ] **Step 3: 自检 + 提交**

### Task 31: ch31-god-class.md — 上帝类 vs 模块拆分

**Files:**
- Verify: `src/agentscope/tool/_toolkit.py`

- [ ] **Step 1: 验证源码**（Toolkit 的行数、方法数）

```bash
wc -l src/agentscope/tool/_toolkit.py
grep -c "def " src/agentscope/tool/_toolkit.py
```

- [ ] **Step 2: 写 ch31**

目标篇幅：250-350 行

- [ ] **Step 3: 自检 + 提交**

### Task 32: ch32-compile-time-hooks.md — 编译期 Hook vs 运行时 Hook

**Files:**
- Verify: `src/agentscope/agent/_agent_meta.py`, `src/agentscope/agent/_agent_base.py`

- [ ] **Step 1: 验证源码**

```bash
grep -n "class _AgentMeta\|_wrap_with_hooks" src/agentscope/agent/_agent_meta.py
grep -n "class AgentBase\|async def reply\|async def observe" src/agentscope/agent/_agent_base.py
```

- [ ] **Step 2: 写 ch32**

目标篇幅：250-350 行

- [ ] **Step 3: 自检 + 提交**

### Task 33: ch33-typedict-union.md — 为什么 ContentBlock 是 Union

**Files:**
- Verify: `src/agentscope/message/_message_block.py`

- [ ] **Step 1: 验证源码**

```bash
grep -n "class TextBlock\|class ThinkingBlock\|class ToolUseBlock\|class ToolResultBlock\|class ImageBlock\|class AudioBlock\|class VideoBlock\|ContentBlock" src/agentscope/message/_message_block.py
```

- [ ] **Step 2: 写 ch33**

目标篇幅：200-300 行

- [ ] **Step 3: 自检 + 提交**

### Task 34: ch34-contextvar.md — 为什么用 ContextVar

**Files:**
- Verify: `src/agentscope/_run_config.py`

- [ ] **Step 1: 验证源码**

```bash
grep -n "ContextVar\|class _ConfigCls" src/agentscope/_run_config.py
```

- [ ] **Step 2: 写 ch34**

目标篇幅：200-300 行

- [ ] **Step 3: 自检 + 提交**

### Task 35: ch35-formatter-separate.md — 为什么 Formatter 独立于 Model

**Files:**
- Verify: `src/agentscope/formatter/_formatter_base.py`, `src/agentscope/model/_model_base.py`

- [ ] **Step 1: 验证源码**

```bash
grep -n "class FormatterBase" src/agentscope/formatter/_formatter_base.py
grep -n "class ChatModelBase\|formatter" src/agentscope/model/_model_base.py
```

- [ ] **Step 2: 写 ch35**

目标篇幅：250-350 行

- [ ] **Step 3: 自检 + 提交**

### Task 36: ch36-panorama.md — 架构的全景与边界

**Files:**
- Verify: 全部 `src/agentscope/` 目录

- [ ] **Step 1: 验证源码**（扫描全部模块，确认各模块的存在和职责）

```bash
ls src/agentscope/
wc -l src/agentscope/**/*.py | sort -n
```

- [ ] **Step 2: 写 ch36**（依赖图复盘 → evaluate/realtime/a2a/tuner 等模块的存在 → 边界模糊处 → 演进方向 → 开放问题）

目标篇幅：300-400 行

- [ ] **Step 3: 自检 + 提交**

```bash
git add teaching/book/volume-4-why/
git commit -m "docs: complete volume 4 - design tradeoffs"
```

---

## Phase 5: 附录 + 最终审查

### Task 37: 附录三篇

**Files:**
- Create: `teaching/book/appendix/python-primer.md`
- Create: `teaching/book/appendix/glossary.md`
- Create: `teaching/book/appendix/source-map.md`

- [ ] **Step 1: 写 python-primer.md** — async/await、TypedDict、元类、ContextManager、dataclass、事件循环速查
- [ ] **Step 2: 写 glossary.md** — 全书术语的中英文对照表
- [ ] **Step 3: 写 source-map.md** — src/agentscope/ 下每个文件的一句话职责 + 行数（含 evaluate/realtime/a2a/tuner 等非核心模块）
- [ ] **Step 4: 提交**

```bash
git add teaching/book/appendix/
git commit -m "docs: complete appendices"
```

### Task 38: 最终审查

- [ ] **Step 1: 源码引用批量验证** — 运行 `teaching/book/scripts/verify_references.sh`（Task 0.5 创建），验证全书中所有 `src/agentscope/` 路径和 `ClassName.method_name` 引用
- [ ] **Step 2: 术语一致性检查** — 确认全书术语与 glossary.md 一致
- [ ] **Step 3: 交叉引用检查** — 确认所有章节间的"详见第 X 章"引用正确
- [ ] **Step 4: 试一试可执行性检查** — 确认全书所有"试一试"环节的修改指令能实际执行
- [ ] **Step 5: 更新 README.md** — 确认目录与实际文件一致
- [ ] **Step 6: 最终提交**

```bash
git add teaching/book/
git commit -m "docs: complete source code analysis book - final review"
```

---

## 自检

**1. Spec 覆盖：** 36 章 + 3 附录 + 1 README = 40 个文件，全部映射到 Task 0-38 + Task 0.5。spec 中的每个章节都有对应任务。

**2. 新手友好化：**
- 卷零（Task 1-2）补全 LLM/Agent 基础知识，无 API key 也可跟随
- async/await 提前到 ch03（与 spec 一致）
- ch06/ch07 拆分记忆为两章（工作记忆 vs 长期记忆/RAG），解决内容过载
- 每章都有"试一试"环节，读者从第一章就动手改源码

**3. 专家评审修复（三专家评审 2026-05-11）：**
- ch01 精简：Token/流式响应降至"提及但不展开"
- ch01/ch02 试一试分两级，增加无 API key 纯本地动手环节
- ch03 知识补全去掉 pip install -e，聚焦 async/await + 事件循环预告
- ch04 补充 `_utils/_mixin.py`（DictMixin）
- ch05 广播方法名修正为 `_broadcast_to_subscribers`
- ch10 补充 `tool/_async_wrapper.py` 和 `tool/_types.py`，移除不存在的 `ToolResponseStream`
- ch11 补充 `agent/_react_agent_base.py` 和 `token/`
- ch14 开场场景统一
- ch17 知识补全明确 Pydantic 范围 + 补充 `types/_tool.py`
- ch26 补充 `_stateful_client_base.py`
- ch29/ch35 核心文件从目录级改为具体文件
- ch36 区分 tune/ vs tuner/
- 通用写作流程补充"设计一瞥"侧边栏检查项（卷一、卷二每章至少 1 个）
- 自检流程扩展为 5 项检查（含符号名验证、设计一瞥、无 key 替代方案）
- Task 0 增加旧文件备份步骤
- 文件总数修正为 40

**4. 源码引用策略（2026-05-11 优化）：**
- 正文不硬编码精确行号，改用 `ClassName.method_name` 引用
- 每章开头标注源码验证日期和 commit hash
- Task 0.5 创建验证脚本，Task 38 复用进行批量验证
- 每章独立 git commit（方案 A）

**5. Diátaxis 框架对齐：**
- 卷零 + 卷一 = Tutorial（线性阅读）
- 卷三 = How-to Guide（按需跳读）
- 附录 = Reference（查阅）
- 卷二 + 卷四 = Explanation（按需跳读）
