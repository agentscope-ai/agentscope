# AgentScope 源码之旅

**从一次函数调用到架构贡献者**

---

## 关于本书

这是一本源码分析书。你将跟随一行代码 `await agent(msg)` 走完它在 AgentScope 框架中的完整旅程——从消息诞生、Agent 收信、记忆存取、格式转换、模型调用、工具执行，到 ReAct 循环的最终返回。

读完这本书，你会经历四个阶段：

| 卷 | 你将获得的能力 | 怎么读 |
|----|--------------|--------|
| **卷一 · 一次调用** | 能追踪请求流程、定位 bug、修小问题 | 从头到尾顺序阅读 |
| **卷二 · 每个齿轮** | 能理解设计模式、读懂任意模块的代码 | 按需跳读，每章独立 |
| **卷三 · 造新齿轮** | 能独立添加新功能模块（Tool / Model / Memory / Agent） | 跟着动手做 |
| **卷四 · 为什么** | 能参与架构讨论、理解设计权衡 | 任意顺序，独立论述 |

## 前置知识

- Python 基础（函数、类、列表、字典）
- 不需要熟悉 Agent 框架
- Python 进阶知识（async、TypedDict、元类等）在正文中以侧边栏补充

附录 [python-primer.md](./appendix/python-primer.md) 提供进阶知识的速查。

## 源码版本

本书基于 AgentScope `main` 分支写作。所有源码引用格式为：

```
src/agentscope/<module>/_<file>.py:<行号>
```

引用的行号在写作时已通过 `grep -n` 验证。如遇源码更新导致行号偏移，请以实际代码为准。

## 目录

### 卷一：一次 agent() 调用的旅程

| 章 | 标题 | 主题 |
|----|------|------|
| [ch01](./volume-1-journey/ch01-toolbox.md) | 出发前：准备你的工具箱 | init / install / 第一个 agent |
| [ch02](./volume-1-journey/ch02-message-born.md) | 第 1 站：消息诞生 | Msg 与 ContentBlock |
| [ch03](./volume-1-journey/ch03-agent-receives.md) | 第 2 站：Agent 收信 | AgentBase.__call__ / Hook / 广播 |
| [ch04](./volume-1-journey/ch04-memory-store.md) | 第 3 站：记忆存入 | 工作记忆的 add/get/delete |
| [ch05](./volume-1-journey/ch05-retrieval-knowledge.md) | 第 4 站：检索与知识 | 长期记忆 / RAG / Embedding |
| [ch06](./volume-1-journey/ch06-formatter.md) | 第 5 站：格式转换 | Msg → API 格式 / Token 截断 |
| [ch07](./volume-1-journey/ch07-model.md) | 第 6 站：调用模型 | HTTP 请求 / 流式响应 / ChatResponse |
| [ch08](./volume-1-journey/ch08-toolkit.md) | 第 7 站：执行工具 | ToolUseBlock → ToolResultBlock |
| [ch09](./volume-1-journey/ch09-loop-return.md) | 第 8 站：循环与返回 | ReAct 循环 / Plan / Token 压缩 / TTS |
| [ch10](./volume-1-journey/ch10-journey-review.md) | 旅程复盘 | 全景图 / 卷一→卷二过渡 |

### 卷二：拆开每个齿轮

| 章 | 标题 | 设计模式 |
|----|------|---------|
| [ch11](./volume-2-patterns/ch11-module-system.md) | 模块系统：文件的命名与导入 | _前缀 / re-export / lazy import |
| [ch12](./volume-2-patterns/ch12-inheritance.md) | 继承体系：从 StateModule 到 AgentBase | PyTorch 式状态管理 |
| [ch13](./volume-2-patterns/ch13-metaclass-hooks.md) | 元类与 Hook：方法调用的拦截 | _AgentMeta 编译期包装 |
| [ch14](./volume-2-patterns/ch14-formatter-strategy.md) | 策略模式：Formatter 的多态分发 | FormatterBase → 各 Provider |
| [ch15](./volume-2-patterns/ch15-schema-factory.md) | 工厂与 Schema：从函数到 JSON Schema | _parse_tool_function + pydantic |
| [ch16](./volume-2-patterns/ch16-middleware.md) | 中间件与洋葱模型 | _apply_middlewares 装饰器链 |
| [ch17](./volume-2-patterns/ch17-pubsub.md) | 发布-订阅：多 Agent 通信 | MsgHub / Pipeline |
| [ch18](./volume-2-patterns/ch18-observability.md) | 可观测性与持久化 | Tracing / Session |

### 卷三：造一个新齿轮

| 章 | 标题 | 实战项目 |
|----|------|---------|
| [ch19](./volume-3-building/ch19-dev-setup.md) | 扩展准备 | 开发环境 / 测试策略 |
| [ch20](./volume-3-building/ch20-new-tool.md) | 造一个新 Tool | 数据库查询工具 |
| [ch21](./volume-3-building/ch21-new-model.md) | 造一个新 Model Provider | 接入 FastLLM API |
| [ch22](./volume-3-building/ch22-new-memory.md) | 造一个新 Memory Backend | SQLite Memory |
| [ch23](./volume-3-building/ch23-new-agent.md) | 造一个新 Agent 类型 | Plan-Execute Agent |
| [ch24](./volume-3-building/ch24-mcp-server.md) | 集成 MCP Server | 对接本地 MCP Server |
| [ch25](./volume-3-building/ch25-advanced-extension.md) | 高级扩展：中间件与分组 | 限流中间件 / 场景分组 |
| [ch26](./volume-3-building/ch26-integration-capstone.md) | 终章：集成实战 | 端到端集成测试 |

### 卷四：为什么要这样设计

| 章 | 标题 | 设计决策 |
|----|------|---------|
| [ch27](./volume-4-why/ch27-msg-interface.md) | 消息为什么是唯一接口 | Msg 统一通信 |
| [ch28](./volume-4-why/ch28-no-decorator.md) | 为什么不用装饰器注册工具 | 显式 vs 隐式注册 |
| [ch29](./volume-4-why/ch29-god-class.md) | 上帝类 vs 模块拆分 | Toolkit 单文件权衡 |
| [ch30](./volume-4-why/ch30-compile-time-hooks.md) | 编译期 Hook vs 运行时 Hook | 元类 vs 装饰器链 |
| [ch31](./volume-4-why/ch31-typedict-union.md) | 为什么 ContentBlock 是 Union | TypedDict vs OOP |
| [ch32](./volume-4-why/ch32-contextvar.md) | 为什么用 ContextVar | 并发安全配置 |
| [ch33](./volume-4-why/ch33-formatter-separate.md) | 为什么 Formatter 独立于 Model | 关注点分离 vs 简单性 |
| [ch34](./volume-4-why/ch34-panorama.md) | 架构的全景与边界 | 依赖图 / 演进方向 |

### 附录

| 文件 | 内容 |
|------|------|
| [python-primer.md](./appendix/python-primer.md) | Python 进阶知识速查 |
| [glossary.md](./appendix/glossary.md) | 术语中英文对照表 |
| [source-map.md](./appendix/source-map.md) | 源码文件速查表 |
