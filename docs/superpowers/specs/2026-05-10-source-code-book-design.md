# AgentScope 源码分析书：设计规格

> **状态**: 已批准（含专家检视修订）
> **日期**: 2026-05-10
> **风格参考**: 《网络是怎么连接的》
> **写作语言**: 中文为主，源码引用保留英文，关键术语附英文原文

---

## 目标

写一本源码分析书，读者读完后能分阶段成为 AgentScope 项目贡献者：

| 卷 | 读者能力 | 核心 |
|----|---------|------|
| 卷一 | 能追踪请求流程、定位 bug、修小问题 | 跟随一次 agent() 调用走完全程 |
| 卷二 | 能理解设计模式、读懂任意模块的代码组织 | 拆开每个模块看设计模式 |
| 卷三 | 能独立添加新功能模块 | 手把手扩展实战 |
| 卷四 | 能参与架构讨论、理解设计权衡 | 设计决策的前因后果 |

---

## 读者画像

广泛的开发者读者。会 Python 基础，不要求熟悉 Agent 框架。Python 进阶知识（async、TypedDict、元类等）作为侧边栏在正文中补充。

---

## 叙事线索

单次请求追踪线。贯穿全书的示例是"天气查询 Agent"：

```python
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit
from agentscope.memory import InMemoryMemory

agentscope.init(project="weather-demo")

model = OpenAIChatModel(model_name="gpt-4o", stream=True)
toolkit = Toolkit()
toolkit.register_tool_function(get_weather)

agent = ReActAgent(
    name="assistant",
    sys_prompt="你是天气助手。",
    model=model,
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
)

result = await agent(Msg("user", "北京今天天气怎么样？", "user"))
```

全书追踪这最后一行 `await agent(...)` 从执行到返回的完整旅程。

---

## 与现有素材的关系

现有 `teaching/book/` 有 93 文件（~48K 行），存在大量源码虚构问题（编造不存在的 API、简化实现与真实源码不符）。本书**全部重写**，不沿用现有内容。所有源码引用必须在写作时通过 `grep -n` 验证。

现有文件处理：写完后替换 `teaching/book/` 目录。

---

## 全书结构

### 卷一：一次 agent() 调用的旅程

跟随请求走，逐站读源码。每章 = 一个"站"。

| 章 | 标题 | 内容 | 核心文件 |
|----|------|------|---------|
| 1 | 出发前：准备你的工具箱 | init / install / 第一个 agent 跑起来 | `__init__.py` |
| 2 | 第 1 站：消息诞生 | Msg 创建与内部结构、ContentBlock 7 种类型 | `message/_message_base.py`, `message/_message_block.py` |
| 3 | 第 2 站：Agent 收信 | `__call__()` → `reply()` 入口、Hook 初见、广播 | `agent/_agent_base.py`, `agent/_agent_meta.py` |
| 4 | 第 3 站：记忆存入 | 工作记忆的 add/get_memory/delete 机制 | `memory/_working_memory/_base.py`, `memory/_working_memory/_in_memory_memory.py` |
| 5 | 第 4 站：检索与知识 | 长期记忆检索（Mem0/ReMe）、RAG 知识库查询、embedding 流程 | `memory/_long_term_memory/`, `rag/`, `embedding/` |
| 6 | 第 5 站：格式转换 | Msg 列表 → API messages 格式、Token 截断 | `formatter/_openai_formatter.py`, `token/` |
| 7 | 第 6 站：调用模型 | HTTP 请求、流式响应解析、ThinkingBlock 处理 | `model/_openai_model.py` |
| 8 | 第 7 站：执行工具 | ToolUseBlock → tool function → ToolResultBlock、中间件链 | `tool/_toolkit.py`, `_utils/_common.py` |
| 9 | 第 8 站：循环与返回 | ReAct 循环终止条件、规划子系统（PlanNotebook）、Token 压缩、TTS 输出、结构化输出 | `agent/_react_agent.py`, `plan/`, `tts/` |
| 10 | 旅程复盘 | 回顾完整调用链的全景图、各站串联、从追踪到理解 | 全部 |

每章内部结构（统一）：

1. **路线图** — 流程图高亮当前站，我们的请求走到哪了
2. **源码入口** — 文件路径、类名、关键方法行号
3. **逐行阅读** — 按真实调用链读源码，关键代码段 + 行间注释
4. **设计一瞥** — 侧边栏，简短的设计对比（如"为什么用 TypedDict 而不是 dataclass"），嵌入设计推理
5. **补充知识** — 必要的 Python 前置（TypedDict、ContextVar 等）侧边栏
6. **调试实践** — 断点位置、日志方法、定位问题的技巧
7. **检查点** — "你现在已经理解了 X"、1-2 道自检练习题
8. **下一站预告** — 一句话引向下一章

### 过渡桥：从追踪到拆解

第 10 章兼作卷一→卷二的过渡。内容：
- 完整调用链的全景图（一页 Mermaid 序列图）
- "你已经走完了全程，现在回来拆开每一个齿轮" 的叙事转折
- 卷一各站 → 卷二各章的对应映射表

### 卷二：拆开每个齿轮

回到卷一经过的每一站，拆开看设计模式。每章独立可跳读。每章以具体场景开头。

| 章 | 标题 | 开场场景 | 设计模式 | 核心文件 |
|----|------|---------|---------|---------|
| 11 | 模块系统：文件的命名与导入 | "你 clone 了仓库，打开 src/ 看到一堆 _ 开头的文件" | _前缀约定、re-export、lazy import | 各 `__init__.py` |
| 12 | 继承体系：从 StateModule 到 AgentBase | "你收到一个 bug：Agent 序列化失败" | PyTorch 式状态管理 | `module/_state_module.py`, `agent/_agent_base.py` |
| 13 | 元类与 Hook：方法调用的拦截 | "你加了一行日志到 reply() 但没生效——因为 Hook 先执行了" | _AgentMeta 编译期包装、AgentHookTypes/ReActAgentHookTypes 类型约束 | `agent/_agent_meta.py`, `types/` |
| 14 | 策略模式：Formatter 的多态分发 | "你接了一个 bug：Gemini 模型的工具调用格式不对" | FormatterBase → TruncatedFormatterBase → 各 Provider | `formatter/_formatter_base.py` 及子类 |
| 15 | 工厂与 Schema：从函数到 JSON Schema | "你的工具函数有嵌套的 Pydantic 参数，Schema 生成报错了" | _parse_tool_function + pydantic.create_model | `_utils/_common.py`, `tool/_toolkit.py` |
| 16 | 中间件与洋葱模型 | "你的工具被并发调用，需要加限流" | _apply_middlewares 装饰器链、AsyncGenerator 统一接口 | `tool/_toolkit.py` |
| 17 | 发布-订阅：多 Agent 通信 | "两个 Agent 在 MsgHub 里收到重复消息" | MsgHub add/delete、广播机制 | `pipeline/_msghub.py`, `pipeline/_class.py`, `pipeline/_functional.py` |
| 18 | 可观测性与持久化 | "Agent 跑了 10 分钟，你需要知道它卡在哪" | OpenTelemetry 装饰器、state_dict 持久化、Session 管理 | `tracing/_trace.py`, `session/` |

### 卷三：造一个新齿轮

每章一个完整扩展任务，从开发到 PR 提交。

| 章 | 标题 | 实战项目 | 核心文件 |
|----|------|---------|---------|
| 19 | 扩展准备 | 开发环境、测试策略、pre-commit | `tests/`, `.github/` |
| 20 | 造一个新 Tool | 数据库查询工具（同步 + 流式） | `tool/_toolkit.py` |
| 21 | 造一个新 Model Provider | 接入 FastLLM API（非流式→流式→结构化输出三步走） | `model/`, `formatter/` |
| 22 | 造一个新 Memory Backend | SQLite Memory | `memory/_working_memory/` |
| 23 | 造一个新 Agent 类型 | Plan-Execute Agent | `agent/_agent_base.py` |
| 24 | 集成 MCP Server | 对接本地 MCP Server | `mcp/_client_base.py` |
| 25 | 高级扩展：中间件与分组 | 限流中间件 + 场景分组 + Agent Skill | `tool/_toolkit.py` |
| 26 | 终章：集成实战 | 把 ch20-ch23 造的 Tool/Model/Memory/Agent 集成为完整系统，跑通端到端测试 | 综合 |

每章末尾统一"PR 检查清单"：
- 测试覆盖正常路径和错误路径
- 更新 `__init__.py` 导出
- Docstring 符合项目规范
- pre-commit 通过

### 卷四：为什么要这样设计

每章围绕一个设计决策，呈现选择、被否方案、后果。

| 章 | 标题 | 设计决策 |
|----|------|---------|
| 27 | 消息为什么是唯一接口 | Agent/Model/Tool 全部通过 Msg 通信 |
| 28 | 为什么不用装饰器注册工具 | 显式 register_tool_function vs @tool |
| 29 | 上帝类 vs 模块拆分 | Toolkit 单文件的权衡 |
| 30 | 编译期 Hook vs 运行时 Hook | 元类注入 vs 装饰器链 |
| 31 | 为什么 ContentBlock 是 Union | TypedDict 数据优先 vs OOP 行为优先 |
| 32 | 为什么用 ContextVar | 并发安全的配置传递 |
| 33 | 为什么 Formatter 独立于 Model | 关注点分离 vs 简单性 |
| 34 | 架构的全景与边界 | 依赖图复盘、边界模糊处（_utils/_common.py）、演进方向 |

每章内部结构（统一）：

1. **决策回顾** — 源码中的证据（文件 + 行号）
2. **被否方案** — 另一种设计的伪代码
3. **后果分析** — 今天的好处和麻烦
4. **横向对比** — LangChain/AutoGen/CrewAI 怎么做的
5. **你的判断** — 开放性问题

---

## 写作规范

### 源码引用

所有引用格式：`src/agentscope/<module>/_<file>.py:<line>`

写作时必须验证：
1. `ls` 确认文件存在
2. `sed -n '<line>p' <file>` 确认行号对应的内容正确
3. 类名/方法名/参数名与源码一致

禁止：编造不存在的类/方法/参数、简化实现后当作真实代码呈现。

### 代码示例

- 仅展示真实源码片段，标注行号范围
- 如需简化，明确标注 `[简化版，实际源码见 xxx]`
- 练习代码（卷三）不要求是框架内真实代码，但必须能实际运行

### 设计推理嵌入

在卷一和卷二的正文中，用侧边栏嵌入小型设计对比（"方案 A vs 方案 B"）。不把所有设计讨论都堆到卷四。格式：

```
> **设计一瞥**：为什么用 TypedDict 而不是 dataclass？
> TypedDict 直接对应 JSON dict 结构，与 OpenAI API 天然兼容。
> 如果用 dataclass，每个 Block 都需要 `.to_dict()` 转换。
> 代价：没有共享基类，无法统一添加行为。
> 详见卷四第 31 章。
```

### 术语

首次出现的术语给出中英文：如"工作记忆（Working Memory）"。后续使用统一中文。

### 图表

每章至少 1 个流程图或架构图（Mermaid 格式）。

### 章节篇幅

灵活控制，不强制统一：
- 卷一：400-600 行/章（含源码走读，内容密度高）
- 卷二：300-500 行/章（含设计分析和练习题）
- 卷三：400-500 行/章（含实战代码和 PR 清单）
- 卷四：200-400 行/章（论述为主，代码少）

### 难度标注

卷二每章标注难度（入门 / 中等 / 进阶），帮助读者管理预期和跳读。

---

## 文件组织

```
teaching/book/
├── README.md                          # 书籍入口
├── volume-1-journey/                  # 卷一
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
├── volume-2-patterns/                 # 卷二
│   ├── ch11-module-system.md
│   ├── ch12-inheritance.md
│   ├── ch13-metaclass-hooks.md
│   ├── ch14-formatter-strategy.md
│   ├── ch15-schema-factory.md
│   ├── ch16-middleware.md
│   ├── ch17-pubsub.md
│   └── ch18-observability.md
├── volume-3-building/                 # 卷三
│   ├── ch19-dev-setup.md
│   ├── ch20-new-tool.md
│   ├── ch21-new-model.md
│   ├── ch22-new-memory.md
│   ├── ch23-new-agent.md
│   ├── ch24-mcp-server.md
│   ├── ch25-advanced-extension.md
│   └── ch26-integration-capstone.md
├── volume-4-why/                      # 卷四
│   ├── ch27-msg-interface.md
│   ├── ch28-no-decorator.md
│   ├── ch29-god-class.md
│   ├── ch30-compile-time-hooks.md
│   ├── ch31-typedict-union.md
│   ├── ch32-contextvar.md
│   ├── ch33-formatter-separate.md
│   └── ch34-panorama.md
└── appendix/                          # 附录
    ├── python-primer.md               # Python 进阶知识（async、TypedDict、元类等）
    ├── glossary.md                    # 术语表
    └── source-map.md                  # 源码文件速查表
```

---

## 不做什么

- 不写 API 参考文档（那是 docstring 的职责）
- 不写"Python 入门"（附录只做进阶知识速查）
- 不写部署指南（与源码分析无关）
- 不写项目实战案例（与源码分析无关）
- 不对框架做主观评价（卷四呈现事实和权衡，不给"好/坏"判断）

---

## 专家检视修订记录

### 源码覆盖修复
- 卷一第 4 章拆分为两章：ch04（工作记忆存入）+ ch05（长期记忆检索 + RAG + embedding），各指向正确源码
- 卷一第 9 章（原第 8 章）加入 plan/（PlanNotebook）、token/（Token 压缩）、tts/（语音输出）
- 卷二 ch13 加入 types/ 模块（AgentHookTypes 类型约束）
- 卷四 ch29 标题改为"上帝类 vs 模块拆分"（去掉硬编码行数）

### 教学设计增强
- 每章增加"检查点"（你现在已经理解了 X）+ 1-2 道自检练习题
- 每章增加"设计一瞥"侧边栏，嵌入小型方案对比
- 卷二每章以具体场景开头（bug 场景 / 任务场景）
- 卷二每章标注难度（入门 / 中等 / 进阶）
- 卷一增加 ch10 旅程复盘（兼作卷一→卷二过渡桥）
- 卷三增加 ch26 集成终章（端到端测试前面造的所有模块）
- 章节篇幅改为弹性控制（卷一 400-600 行，卷四 200-400 行）
