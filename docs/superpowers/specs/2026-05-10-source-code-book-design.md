# AgentScope 源码分析书：设计规格

> **状态**: 已批准（含专家检视修订 + 新手友好化修订）
> **日期**: 2026-05-10
> **风格参考**: 《网络是怎么连接的》
> **写作语言**: 中文为主，源码引用保留英文，关键术语附英文原文

---

## 目标

写一本源码分析书，读者读完后能分阶段成为 AgentScope 项目贡献者：

| 卷 | 读者能力 | 核心 |
|----|---------|------|
| 卷零 | 理解 LLM 和 Agent 是什么，能跑通第一个 Agent | 基础概念 + 动手实验 |
| 卷一 | 能追踪请求流程、定位 bug、**能修改源码验证理解** | 跟随一次 agent() 调用走完全程 |
| 卷二 | 能理解设计模式、读懂任意模块的代码组织 | 拆开每个模块看设计模式 |
| 卷三 | 能独立添加新功能模块、提交 PR | 手把手扩展实战 |
| 卷四 | 能参与架构讨论、理解设计权衡 | 设计决策的前因后果 |

---

## 读者画像

广泛的开发者读者。会 Python 基础（函数、类、列表/字典），不要求熟悉 Agent 框架或 LLM API。所有需要的进阶知识（LLM 概念、Agent 模式、async、TypedDict、设计模式等）都在书中逐步引入。

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

### 卷零：出发前的地图

不需要任何前置知识。读完这两章，你就能理解全书追踪的那个"天气查询 Agent"到底在做什么。

| 章 | 标题 | 内容 | 知识补全 |
|----|------|------|---------|
| 1 | 什么是大模型（LLM） | 大模型是什么、Chat API 怎么工作（发消息→收回复）、Tool Calling 让模型调用函数；Token 和流式响应提到但不展开（标注"ch08/ch09 详解"） | HTTP 请求基础（发请求→收 JSON 响应）、JSON 数据结构 |
| 2 | 什么是 Agent | Agent = 大模型 + 记忆 + 工具 + 循环；ReAct 模式（先想再做，反复循环）；Memory（记住对话）、Tool（动手做事）、RAG（查阅资料）；用天气 Agent 的例子串起来 | 无（用天气 Agent 的流程自然展示循环和分层） |

每章内部结构（统一）：
1. **生活类比** — 用日常场景解释概念（如"LLM 像一个超级预测下一个字的输入法"）
2. **动手试试** — 用 curl 或 Python 几行代码调用 OpenAI API，亲眼看到效果（如果没有 API key，提供模拟响应示例，跟随阅读即可）
3. **核心概念** — 用图解（Mermaid）展示概念之间的关系
4. **试一试** — 分两级：基础级（pip install agentscope，3 行脚本验证安装）、完整级（clone + pip install -e，为后续改源码做准备）。ch02 增加纯本地动手环节（用 input + if/else 模拟最简 Agent 循环，不依赖 API key）。需要 API key 的试一试都提供无 key 替代方案
5. **检查点** — "你现在已经理解了：什么是 LLM / Agent / Tool / Memory"

### 卷一：一次 agent() 调用的旅程

跟随请求走，逐站读源码。每章 = 一个"站"。Python 进阶知识在遇到时自然引入。**每章都有"试一试"环节：改一行源码、加一个 print、观察输出变化**，让读者从第一天就开始动手修改。

| 章 | 标题 | 内容 | 核心文件 | 知识补全 |
|----|------|------|---------|---------|
| 3 | 准备工具箱 | init / install / 第一个 agent 跑起来 / 开发环境搭建 | `__init__.py`, `_run_config.py` | async/await 基础（`await agent(...)` 中的 await 是什么意思；事件循环在 ch07 详解） |
| 4 | 第 1 站：消息诞生 | Msg 创建与内部结构、ContentBlock 7 种类型、DictMixin | `message/_message_base.py`, `message/_message_block.py`, `_utils/_mixin.py` | TypedDict（为什么不用普通 dict 或 dataclass） |
| 5 | 第 2 站：Agent 收信 | `__call__()` → `reply()` 入口、Hook 初见、`_broadcast_to_subscribers` 广播机制 | `agent/_agent_base.py`, `agent/_agent_meta.py` | 元类（只需知道"它可以自动包装方法"） |
| 6 | 第 3 站：工作记忆 | 工作记忆的 add/get_memory/delete 机制、mark 系统 | `memory/_working_memory/_base.py`, `memory/_working_memory/_in_memory_memory.py` | 无 |
| 7 | 第 4 站：检索与知识 | 长期记忆检索（Mem0/ReMe）、RAG 知识库查询、embedding 流程 | `memory/_long_term_memory/`, `rag/`, `embedding/` | 事件循环（为什么 async 代码需要事件循环） |
| 8 | 第 5 站：格式转换 | Msg 列表 → API messages 格式、Token 截断 | `formatter/_openai_formatter.py`, `token/` | JSON Schema（工具参数的描述格式） |
| 9 | 第 6 站：调用模型 | HTTP 请求、流式响应解析、ThinkingBlock 处理 | `model/_openai_model.py`, `model/_model_response.py`, `model/_model_usage.py` | AsyncGenerator（流式返回） |
| 10 | 第 7 站：执行工具 | ToolUseBlock → tool function → ToolResultBlock、同步/异步包装、中间件链 | `tool/_toolkit.py`, `tool/_async_wrapper.py`, `tool/_types.py`, `_utils/_common.py` | 装饰器模式（洋葱模型的本质） |
| 11 | 第 8 站：循环与返回 | ReAct 循环终止条件、ReActAgentBase 中间层、规划子系统、Token 压缩、TTS | `agent/_react_agent.py`, `agent/_react_agent_base.py`, `plan/`, `token/`, `tts/` | 结构化输出（让 LLM 返回特定格式的 JSON） |
| 12 | 旅程复盘 | 完整调用链全景图、各站串联、从追踪到理解 | 全部 | 无 |

每章内部结构（统一）：

1. **路线图** — 流程图高亮当前站，我们的请求走到哪了
2. **知识补全**（按需）— 本章会遇到的 Python 进阶概念，用简单例子讲清楚（如"async 就是让程序等 IO 时不闲着"）
3. **源码入口** — 文件路径、类名、关键方法行号
4. **逐行阅读** — 按真实调用链读源码，关键代码段 + 行间注释
5. **调试实践** — 断点位置、日志方法、定位问题的技巧
6. **试一试** — 实际修改源码验证理解（如"在 `_agent_base.py` 的 `__call__` 方法中加一行 print，观察调用链"）。每章至少 1 个可执行的修改任务
7. **检查点** — "你现在已经理解了 X"、1-2 道自检练习题
8. **下一站预告** — 一句话引向下一章

### 过渡桥：从追踪到拆解

第 12 章兼作卷一→卷二的过渡。内容：
- 完整调用链的全景图（一页 Mermaid 序列图）
- "你已经走完了全程，现在回来拆开每一个齿轮" 的叙事转折
- 卷一各站 → 卷二各章的对应映射表

### 卷二：拆开每个齿轮

回到卷一经过的每一站，拆开看设计模式。每章独立可跳读。每章以具体场景开头。设计模式知识在每章开头自然引入。

| 章 | 标题 | 开场场景 | 设计模式 | 核心文件 | 知识补全 |
|----|------|---------|---------|---------|---------|
| 13 | 模块系统：文件的命名与导入 | "你 clone 了仓库，打开 src/ 看到一堆 _ 开头的文件" | _前缀约定、re-export、lazy import | 各 `__init__.py` | Python 模块与包导入机制 |
| 14 | 继承体系：从 StateModule 到 AgentBase | "你收到一个 bug：Agent 序列化后恢复，但记忆丢失了" | PyTorch 式状态管理 | `module/_state_module.py`, `agent/_agent_base.py` | 继承与多态 |
| 15 | 元类与 Hook：方法调用的拦截 | "你加了一行日志到 reply() 但没生效——因为 Hook 先执行了" | _AgentMeta 编译期包装、AgentHookTypes/ReActAgentHookTypes 类型约束 | `agent/_agent_meta.py`, `types/`, `hooks/` | 无（卷一 ch05 已讲过元类基础） |
| 16 | 策略模式：Formatter 的多态分发 | "你接了一个 bug：Gemini 模型的工具调用格式不对" | FormatterBase → TruncatedFormatterBase → 各 Provider | `formatter/_formatter_base.py` 及子类 | 策略模式（用同一接口做不同事） |
| 17 | 工厂与 Schema：从函数到 JSON Schema | "你的工具函数有嵌套的 Pydantic 参数，Schema 生成报错了" | _parse_tool_function + pydantic.create_model | `_utils/_common.py`, `tool/_toolkit.py`, `types/_tool.py` | Pydantic 基础（只需理解 BaseModel 自动生成 JSON Schema 这一功能，5 行最小示例） |
| 18 | 中间件与洋葱模型 | "你的工具被并发调用，需要加限流" | _apply_middlewares 装饰器链、AsyncGenerator 统一接口 | `tool/_toolkit.py` | 装饰器链（函数包装函数） |
| 19 | 发布-订阅：多 Agent 通信 | "两个 Agent 在 MsgHub 里收到重复消息" | MsgHub add/delete、广播机制 | `pipeline/_msghub.py`, `pipeline/_class.py`, `pipeline/_functional.py` | 发布-订阅模式 |
| 20 | 可观测性与持久化 | "Agent 跑了 10 分钟，你需要知道它卡在哪" | OpenTelemetry 装饰器、state_dict 持久化、Session 管理 | `tracing/_trace.py`, `session/` | 无 |

每章内部结构（统一）：
1. **开场场景** — 一个具体的 bug 或任务场景
2. **知识补全**（按需）— 本章涉及的设计模式或 Python 概念
3. **源码分析** — 按设计模式拆解相关模块的代码
4. **对比与反思** — 与其他实现方式的对比（简要预告卷四详细讨论）
5. **试一试** — 修改源码验证理解（如"给 Formatter 加一个新的格式化策略"）。难度标注：入门 / 中等 / 进阶
6. **检查点 + 练习题** — 1-2 道自检练习

### 卷三：造一个新齿轮

每章一个完整扩展任务，从开发到 PR 提交。

| 章 | 标题 | 实战项目 | 核心文件 |
|----|------|---------|---------|
| 21 | 扩展准备 | 开发环境、测试策略、pre-commit | `tests/`, `.github/`, `pyproject.toml` |
| 22 | 造一个新 Tool | 数据库查询工具（同步 + 流式） | `tool/_toolkit.py` |
| 23 | 造一个新 Model Provider | 接入 FastLLM API（非流式→流式→结构化输出三步走） | `model/`, `formatter/` |
| 24 | 造一个新 Memory Backend | SQLite Memory | `memory/_working_memory/` |
| 25 | 造一个新 Agent 类型 | Plan-Execute Agent | `agent/_agent_base.py` |
| 26 | 集成 MCP Server | 对接本地 MCP Server | `mcp/_client_base.py` |
| 27 | 高级扩展：中间件与分组 | 限流中间件 + 场景分组 + Agent Skill | `tool/_toolkit.py` |
| 28 | 终章：集成实战 | 把 ch22-ch25 造的 Tool/Model/Memory/Agent 集成为完整系统，跑通端到端测试 | 综合 |

每章内部结构（统一）：
1. **任务目标** — 要造什么，为什么需要它
2. **设计方案** — 先画架构图，再写代码
3. **逐步实现** — 分步写代码，每步可运行可验证
4. **测试验证** — 写测试、跑测试
5. **试一试** — 在此基础上做扩展（如"给你的 Tool 加一个缓存中间件"）
6. **PR 检查清单** — 测试覆盖、__init__.py 导出、Docstring 规范、pre-commit 通过

### 卷四：为什么要这样设计

每章围绕一个设计决策，呈现选择、被否方案、后果。

| 章 | 标题 | 设计决策 | 核心文件 |
|----|------|---------|---------|
| 29 | 消息为什么是唯一接口 | Agent/Model/Tool 全部通过 Msg 通信 | `message/_message_base.py`, `agent/_agent_base.py`, `model/_model_base.py`, `tool/_toolkit.py` |
| 30 | 为什么不用装饰器注册工具 | 显式 register_tool_function vs @tool | `tool/_toolkit.py`, `_utils/_common.py` |
| 31 | 上帝类 vs 模块拆分 | Toolkit 单文件的权衡 | `tool/_toolkit.py` |
| 32 | 编译期 Hook vs 运行时 Hook | 元类注入 vs 装饰器链 | `agent/_agent_meta.py`, `agent/_agent_base.py` |
| 33 | 为什么 ContentBlock 是 Union | TypedDict 数据优先 vs OOP 行为优先 | `message/_message_block.py` |
| 34 | 为什么用 ContextVar | 并发安全的配置传递 | `_run_config.py` |
| 35 | 为什么 Formatter 独立于 Model | 关注点分离 vs 简单性 | `formatter/_formatter_base.py`, `model/_model_base.py` |
| 36 | 架构的全景与边界 | 依赖图复盘、边界模糊处（_utils/_common.py）、evaluate/realtime/a2a/tune（空壳）/tuner（实际模块）等模块的存在与范围 | 全部 |

每章内部结构（统一）：
1. **决策回顾** — 源码中的证据（文件 + 行号）
2. **被否方案** — 另一种设计的伪代码
3. **后果分析** — 今天的好处和麻烦
4. **横向对比** — LangChain/AutoGen/CrewAI 怎么做的
5. **你的判断** — 开放性问题

---

## 写作规范

### 源码引用

引用优先使用符号名，避免硬编码行号（行号随代码演进必然漂移）：

- **一级引用（正文）**：`src/agentscope/<module>/_<file>.py` 中的 `ClassName.method_name`
- **二级引用（可选）**：`_<file>.py:约L380`，标注"约"字，表示行号仅供参考
- **强制规则**：不在正文中硬编码精确行号；行号仅在写作时用于验证，不写入最终文档

写作时必须验证：
1. `ls` 确认文件存在
2. `grep -n "class ClassName\|def method_name" <file>` 确认符号存在且位置合理
3. 类名/方法名/参数名与源码一致
4. 每章开头标注：`源码验证日期：YYYY-MM-DD，基于 commit <short-hash>`

禁止：编造不存在的类/方法/参数、简化实现后当作真实代码呈现。

### 代码示例

- 仅展示真实源码片段，标注行号范围
- 如需简化，明确标注 `[简化版，实际源码见 xxx]`
- 练习代码（卷三）不要求是框架内真实代码，但必须能实际运行

### 设计推理嵌入

在卷一和卷二的正文中，用引用块（blockquote）嵌入小型设计对比（"方案 A vs 方案 B"）。不把所有设计讨论都堆到卷四。**卷一和卷二每章至少 1 个"设计一瞥"引用块。** 格式：

```
> **设计一瞥**：为什么用 TypedDict 而不是 dataclass？
> TypedDict 直接对应 JSON dict 结构，与 OpenAI API 天然兼容。
> 如果用 dataclass，每个 Block 都需要 `.to_dict()` 转换。
> 代价：没有共享基类，无法统一添加行为。
> 详见卷四第 33 章。
```

### 结构模板预告

每卷的第一章（ch01/ch03/ch13/ch21/ch29）开头加一个灰底框，列出本卷的章节内部结构模板，让读者知道接下来的节奏。例如卷一 ch03 开头：

> **卷一每章的结构**：路线图 → 知识补全 → 源码入口 → 逐行阅读 → 调试实践 → 试一试 → 检查点 → 下一站预告

### 无 API key 友好

所有需要 API key 的"试一试"环节都必须提供无 key 替代方案（手动追踪源码变量、使用模拟数据、修改不依赖 LLM 的代码路径）。

### 试一试（贯穿全书）

每章至少 1 个"试一试"环节。核心原则：
- 读者在本地改源码，观察变化
- 改动量小（1-5 行），效果可观察（print 输出、行为变化、测试通过/失败）
- 提供具体的文件路径和修改位置
- 修改后能跑通（不破坏现有功能）

示例：
- 卷一："在 `_agent_base.py` 的 `__call__` 方法中加一行 `print(f"收到消息: {msg.name}")`，观察调用链"
- 卷二："给 `InMemoryMemory.add()` 加一行日志，观察消息何时被存入"
- 卷三："写一个新的 Tool 函数，注册到 Toolkit，用 ReActAgent 调用"

### 术语

首次出现的术语给出中英文：如"工作记忆（Working Memory）"。后续使用统一中文。

### 图表

每章至少 1 个流程图或架构图（Mermaid 格式）。

### 章节篇幅

弹性控制，内容决定篇幅，不因篇幅填充内容：
- 卷零：300 行起步
- 卷一：400 行起步（含源码走读和试一试，内容密度高）
- 卷二：300 行起步（含设计分析和试一试）
- 卷三：400 行起步（含实战代码和 PR 清单）
- 卷四：200 行起步（论述为主，代码少）
- 过渡章节（如 ch12 旅程复盘）允许低于起步行数

### 难度标注

卷二每章标注难度（入门 / 中等 / 进阶），帮助读者管理预期和跳读。

### 官方文档融入

本书面向**离线读者**——内测用户可能没有网络访问。所有参考资料必须**内嵌到正文中**，不能仅放外部链接。

**文档来源**（按优先级）：
1. `docs.agentscope.io`（Mintlify 新文档）— Basic Concepts + Building Blocks
2. `doc.agentscope.io`（Sphinx 旧文档）— API Reference（autodoc 生成）
3. 仓库内 `docs/tutorial/` — 教程源码和示例脚本

**融入规则**：
- 每章在首次提到核心主题时，用 1-3 句话自然引入官方文档的用法说明
- 官方文档的配置示例、API 使用示例直接写进正文（如"官方文档推荐的配置方式是：……"）
- 不再使用"官方文档对照"侧边栏格式，改为融入正文叙述
- 代码示例优先用源码片段；官方文档有更好的完整示例时，直接把示例代码写进"试一试"环节
- 建筑块（Building Blocks）页面特别有价值：Hook、Middleware、Memory、Tool Capabilities

**已确认的官方文档页面与章节对应**：

| 官方文档页面 | 对应章节 |
|-------------|---------|
| Basic Concepts > Message | ch04（消息诞生）|
| Basic Concepts > Agent | ch05（Agent 收信）|
| Basic Concepts > Model | ch09（调用模型）|
| Basic Concepts > Context and Memory | ch06-ch07（记忆 + 检索）|
| Basic Concepts > Tool | ch10（执行工具）|
| Building Blocks > Agent | ch14（继承体系）|
| Building Blocks > Models | ch09（调用模型）|
| Building Blocks > Memory | ch06-ch07 + ch24（新 Memory）|
| Building Blocks > RAG | ch07（检索与知识）|
| Building Blocks > Tool Capabilities | ch10 + ch22（新 Tool）|
| Building Blocks > Hooking Functions | ch15（元类与 Hook）|
| Building Blocks > Orchestration | ch19（发布-订阅）|

---

## 文件组织

```
teaching/book/
├── README.md                          # 书籍入口
├── volume-0-basics/                   # 卷零：基础知识
│   ├── ch01-what-is-llm.md
│   └── ch02-what-is-agent.md
├── volume-1-journey/                  # 卷一
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
├── volume-2-patterns/                 # 卷二
│   ├── ch13-module-system.md
│   ├── ch14-inheritance.md
│   ├── ch15-metaclass-hooks.md
│   ├── ch16-formatter-strategy.md
│   ├── ch17-schema-factory.md
│   ├── ch18-middleware.md
│   ├── ch19-pubsub.md
│   └── ch20-observability.md
├── volume-3-building/                 # 卷三
│   ├── ch21-dev-setup.md
│   ├── ch22-new-tool.md
│   ├── ch23-new-model.md
│   ├── ch24-new-memory.md
│   ├── ch25-new-agent.md
│   ├── ch26-mcp-server.md
│   ├── ch27-advanced-extension.md
│   └── ch28-integration-capstone.md
├── volume-4-why/                      # 卷四
│   ├── ch29-msg-interface.md
│   ├── ch30-no-decorator.md
│   ├── ch31-god-class.md
│   ├── ch32-compile-time-hooks.md
│   ├── ch33-typedict-union.md
│   ├── ch34-contextvar.md
│   ├── ch35-formatter-separate.md
│   └── ch36-panorama.md
└── appendix/                          # 附录
    ├── python-primer.md               # Python 进阶知识速查（补充卷中未展开的细节）
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

## 修订记录

### 新手友好化 + 动手实践修订（2026-05-10）
- 新增卷零（ch01-ch02）：LLM 基础 + Agent 概念，无需任何前置知识
- 每章增加"知识补全"列：Python 进阶和软件工程基础知识在遇到时自然引入
- 卷零每章增加"生活类比""动手试试"环节
- async/await 提前到 ch03（准备工具箱），因为读者从第一行代码就看到 await
- 卷一 ch06 拆为两章：ch06（工作记忆）+ ch07（长期记忆/RAG/embedding）
- 每章增加"试一试"环节（贯穿全书）：改一行源码、加一个 print、观察输出变化
- 卷二、卷三补充明确的章节内部结构（开场场景/知识补全/源码分析/试一试/检查点）
- 卷四每章补充核心文件列和目标篇幅
- ch01 动手试试加"无 API key 也可跟随阅读"的替代方案
- ch09 补充 `_model_response.py` 和 `_model_usage.py`
- ch11 补充"结构化输出"知识补全
- ch15 Hook 章节补充 `hooks/` 模块
- ch36 全景章提及 evaluate/realtime/a2a/tuner 等模块
- 全书从 34 章扩展到 36 章 + 3 附录 + 1 README = 40 个文件
- 总目标不变：新手入门 → 能改源码 → 高级贡献者

### 三专家评审修订（2026-05-11）
- ch01 精简：Token 和流式响应降至"提及但不展开"，标注后移到 ch08/ch09
- ch01/ch02 试一试分两级（基础级 pip install + 完整级 clone），增加纯本地无 API key 动手环节
- ch03 知识补全去掉 pip install -e（已移至 ch01），聚焦 async/await + 事件循环预告
- ch04 补充 `_utils/_mixin.py`（DictMixin 被 Msg 继承）
- ch10 补充 `tool/_async_wrapper.py`（同步/异步包装）和 `tool/_types.py`（RegisteredToolFunction/ToolGroup）
- ch11 补充 `agent/_react_agent_base.py`（ReActAgentBase 中间层）
- ch14 开场场景统一为"Agent 序列化后恢复，但记忆丢失了"
- ch17 知识补全明确 Pydantic 范围 + 补充 `types/_tool.py`
- ch29/ch35 核心文件从目录级改为具体文件
- ch36 区分 tune/（空壳）vs tuner/（实际模块）
- 写作规范新增"结构模板预告"（每卷第一章加结构框）和"无 API key 友好"规则
- 设计推理嵌入改为"卷一和卷二每章至少 1 个设计一瞥侧边栏"
- 文件总数修正为 40 个

### 源码覆盖修复
- 卷一第 11 章加入 plan/（PlanNotebook）、token/（Token 压缩）、tts/（语音输出）
- 卷二 ch15 加入 types/ 模块（AgentHookTypes 类型约束）
- 卷四 ch31 标题改为"上帝类 vs 模块拆分"（去掉硬编码行数）

### 文档类型说明（Diátaxis 对齐）

本书五卷对应 Diátaxis 框架的四种文档类型：

| 卷 | 文档类型 | 用户意图 | 阅读方式 |
|----|---------|---------|---------|
| 卷零 + 卷一 | Tutorial（教程） | "我想学会" | 从头到尾，线性 |
| 卷三 | How-to Guide（操作指南） | "我想做 X" | 按需跳读 |
| 附录 / source-map | Reference（参考资料） | "我需要查 X" | 查阅 |
| 卷二 + 卷四 | Explanation（深度解析） | "我想理解为什么" | 按需跳读 |

建议在 README.md 中增加一段说明，帮助读者根据需求选择阅读路径。

### 文档漂移防治

源码引用基于特定 commit。维护规则：
1. 正文中不硬编码精确行号，改用 `ClassName.method_name` 引用
2. 每章开头标注源码验证日期和 commit hash
3. 重大版本更新时，运行 `grep -rn "class \|def "` 批量验证所有引用
4. 推荐在 CI 中加入源码引用验证脚本

### 中文排版规范

- 中英文之间加空格（如 "使用 OpenAI API" 而非 "使用OpenAI API"）
- 代码块标注语言类型（` ```python ` 而非裸 ` ``` `）
- 行内代码用反引号包裹
- 图表（Mermaid）必须配有文字说明（确保纯文本可读）
- 章节标题不超过 4 级（`####`）
- 每段不超过 7 行，最佳 4 行以内
- 标题避免孤立编号（同级标题不应只有一个）

### 源码准确性修复
- ch05：广播方法名修正为 `_broadcast_to_subscribers`（非 `broadcast`）
- ch11：核心文件补充 `token/`（Token 压缩）
- ch10：`ToolResponseStream` 类不存在于代码库，仅引用 `ToolResponse`
- 修订记录"源码覆盖修复"中"卷一第 9 章"应为"卷一第 11 章"（章节拆分后编号变更）

### 官方文档融入修订（2026-05-11）[已被离线修订取代]
- ~~新增"官方文档融入"写作规范：每章至少 1 个"官方文档对照"侧边栏~~
- ~~侧边栏对比文档视角（怎么用）vs 本书视角（为什么这样实现）~~
- 文档来源确认：docs.agentscope.io（Mintlify）+ doc.agentscope.io（Sphinx API）
- 建立文档页面→章节的映射表（仍有效）
- ~~已写完的 ch01-ch12 需要回填"官方文档对照"侧边栏~~ → 改为内嵌到正文
- ~~ch13-ch36 在写作时直接融入~~ → 已被离线修订取代

### 外部资源融入修订（2026-05-11 → 2026-05-11 离线修订）

内测用户没有网络，所有外部参考资料必须**直接融入正文**。不再使用"推荐阅读"侧边栏+链接的方式。

**需融入的外部资源**：

| 类别 | 来源 | 数量 | 融入方式 |
|------|------|------|---------|
| 学术论文 | AgentScope 1.0 论文 (arXiv:2508.16279) | 18 处引用 | 摘录论文原文 1-3 句，用引用格式融入正文 |
| 学术论文 | ReAct 论文 (arXiv:2210.03629) | 1 处引用 | 摘录核心思想（Reason+Act > Act-only）到知识补全 |
| 教程文章 | MarkTechPost Production Ready Workflows | 6 处引用 | 关键示例代码直接写进"试一试"环节 |
| 视频教程 | Bilibili 源码带读系列 | 3 处引用 | 转为文字说明，描述视频核心内容 |
| 技术规范 | PEP 589 (TypedDict)、PEP 567 (ContextVar)、Python metaclass 文档 | 4 处引用 | 关键规范条款摘录到"知识补全"节 |
| 设计文章 | Refactoring Guru "Bloaters" | 1 处引用 | 大类拆分标准融入 ch31 正文 |
| 技术文档 | OpenTelemetry Python Tracing | 1 处引用 | Tracer/Span API 要点融入 ch20 知识补全 |

**论文摘录格式**：

```
AgentScope 1.0 论文对这一设计的说明是：

> "The framework adopts a unified message format (Msg) across all components to ensure seamless interoperability."
>
> — AgentScope 1.0: A Comprehensive Framework for Building Multi-Agent Systems, Section 2.1
```

**官方文档内嵌格式**：

把官方文档的用法示例直接写进正文，例如：

```
官方文档推荐的配置方式是：

\`\`\`python
from agentscope.model import OpenAIChatModel
model = OpenAIChatModel(model_name="gpt-4o", api_key="your-key")
\`\`\`

这和我们在源码中看到的 `__init__` 参数一一对应。
```

**融入规则**：
- 不再使用"推荐阅读"侧边栏
- 不再使用"官方文档对照"侧边栏（上一节已删除）
- 论文内容用 `>` 引用格式，标注出处（论文名 + 节号 + arXiv ID）
- 官方文档内容自然融入正文叙述
- 教程示例代码从源码重写（避免第三方教程的版权问题），直接写进"试一试"或正文
- PEP/技术规范的关键条款摘录到"知识补全"节

**PEP/技术规范摘录格式**：

```
Python 的 PEP 589 (TypedDict) 规范规定：

> TypedDict types are defined using a class definition syntax ...
> The body of the class is processed using the regular class semantics.
>
> — PEP 589, "Specification"
```

**视频内容文字化格式**：

```
AgentScope 源码带读系列视频中，对 Memory 模块的讲解覆盖了以下要点：
1. `MemoryBase` 的 5 个抽象方法定义
2. `InMemoryMemory` 的内部 `list[tuple[Msg, list[str]]]` 结构
3. mark 机制的添加和过滤流程
```

> 注：如果视频内容不可用，直接从源码和文档中提取等价信息。

### 教学设计增强
- 每章增加"检查点"（你现在已经理解了 X）+ 1-2 道自检练习题
- 卷二每章以具体场景开头（bug 场景 / 任务场景）
- 卷二每章标注难度（入门 / 中等 / 进阶）
- 卷三增加 ch28 集成终章（端到端测试前面造的所有模块）
- 章节篇幅改为弹性控制

### 离线自包含修订（2026-05-11）
内测用户反馈无法访问网络，需将所有外部参考资料直接内嵌到正文中：
- 删除"官方文档对照"侧边栏格式，改为在正文中自然引入官方文档的用法说明
- 删除"推荐阅读"侧边栏格式，论文内容用引用格式摘录到正文
- MarkTechPost 等教程的关键示例代码直接写进"试一试"环节
- Bilibili 视频引用转为文字说明
- PEP/技术规范的关键条款摘录到"知识补全"节
- 全书不再依赖任何外部链接即可完整阅读
