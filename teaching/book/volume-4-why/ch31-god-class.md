# 第 31 章：上帝类 vs 模块拆分

> **难度**：中等
>
> `src/agentscope/tool/_toolkit.py` 有 1684 行。注册、调用、中间件、分组、异步任务、Schema 管理——全在一个文件里。这是上帝类，还是合理的聚合？

## 决策回顾

打开 `_toolkit.py` 看看它包含了多少职责：

| 职责 | 方法 | 大致行数 |
|------|------|---------|
| 注册工具 | `register_tool_function` | 274-450 |
| 调用工具 | `call_tool_function` | 852-920 |
| 中间件 | `_apply_middlewares`, `register_middleware` | 57-115, 1441-1540 |
| 工具分组 | `create_tool_group`, `set_active_group` | 200-270 |
| Schema 管理 | `get_json_schemas`, `set_extended_model` | 450-540 |
| 异步任务 | `call_tool_function_async`, `view_task` | 750-850, 1541+ |
| MCP 对接 | `register_mcp_server` | 550-700 |
| Agent Skill | skill 相关方法 | 700-750 |
| 序列化 | `state_dict`, `load_state_dict` | 1200-1400 |

9 种职责，1684 行。

---

## 被否方案：按职责拆分

**方案**：每个职责一个类，Toolkit 作为外观（Facade）：

```python
# 拆分方案
class ToolRegistry:           # 注册工具
class ToolExecutor:           # 调用工具
class MiddlewareChain:        # 中间件管理
class ToolGroupManager:       # 分组管理
class SchemaBuilder:          # Schema 生成

class Toolkit:                # Facade
    def __init__(self):
        self.registry = ToolRegistry()
        self.executor = ToolExecutor(self.registry)
        self.middlewares = MiddlewareChain()
        self.groups = ToolGroupManager()
```

**好处**：
- 每个类职责单一
- 可以独立测试每个组件
- 文件更短，更容易理解

**问题**：
1. **调用链变长**：`toolkit.tools` → `toolkit.registry.tools`
2. **状态共享复杂**：`ToolExecutor` 需要访问 `ToolRegistry` 和 `MiddlewareChain`
3. **序列化变复杂**：`state_dict` 需要协调多个子类的序列化
4. **过度工程**：对于大多数开发者，只用到注册和调用两个功能

---

## AgentScope 的选择：大文件聚合

`Toolkit` 是一个聚合根——所有工具相关的操作都通过它。它的"大"不是偶然的，而是有意为之：

1. **内聚性高**：所有方法都围绕"工具"这个概念
2. **调用简单**：`toolkit.register_tool_function()`, `toolkit.call_tool_function()` —— 不需要知道内部结构
3. **状态一致**：注册、调用、分组、中间件共享同一个 `tools` 字典

### 判断标准

"上帝类"是一个贬义词，但不是所有大类都是上帝类。关键区别：

| 上帝类（坏） | 大聚合类（可接受） |
|-------------|------------------|
| 职责不相关 | 职责围绕同一概念 |
| 修改一个功能影响其他功能 | 修改被 `_apply_middlewares` 等封装隔离 |
| 难以独立测试 | 每个方法可以独立测试 |
| 类之间紧耦合 | 类对外接口简洁 |

`Toolkit` 属于右侧——大，但内聚。

### 实际的拆分边界

虽然没有拆分类，但 `Toolkit` 的内部已经有清晰的边界：

```python
# 注册相关
toolkit.register_tool_function(...)
toolkit.register_mcp_server(...)

# 调用相关
toolkit.call_tool_function(...)
toolkit.call_tool_function_async(...)

# 配置相关
toolkit.register_middleware(...)
toolkit.create_tool_group(...)
toolkit.set_extended_model(...)

# 查询相关
toolkit.get_json_schemas(...)
toolkit.view_task(...)
```

这些方法组之间互不干扰。开发者通常只用其中 2-3 个方法组。

---

## 后果分析

### 好处

1. **使用简单**：一个类搞定所有工具操作
2. **状态一致**：不需要跨类协调
3. **序列化简单**：一个 `state_dict` 搞定

### 麻烦

1. **代码审查困难**：PR 改了哪个职责需要仔细看
2. **认知负担**：新开发者看到 1684 行会畏惧
3. **合并冲突**：多人修改同一文件容易冲突
4. **导入开销**：即使只用注册功能，也会加载中间件、MCP 等代码

---

## 横向对比

| 框架 | 工具模块组织 | 文件大小 |
|------|------------|---------|
| **AgentScope** | 单文件 `Toolkit` 类 | 1684 行 |
| **LangChain** | `Tool` 基类 + 多个工具子类 | 每个工具一个文件 |
| **AutoGen** | 函数列表 + `FunctionTool` 包装 | 分散 |
| **CrewAI** | `Tool` 基类 + 装饰器 | 中等 |

> **官方文档对照**：本文对应 [Building Blocks > Tool Capabilities](https://docs.agentscope.io/building-blocks/tool-capabilities)。官方文档按功能分组展示 Toolkit 的方法，本章讨论了为什么这些功能放在同一个类中。
>
> **推荐阅读**：Martin Fowler 的 ["Bloaters" 代码味道](https://refactoring.guru/refactoring/smells/bloaters) 讨论了"大类"何时需要拆分。

---

## 你的判断

1. 如果你是 AgentScope 的维护者，会把 `Toolkit` 拆分吗？如果会，按什么边界？
2. 1684 行是"太大了"还是"刚好够用"？阈值在哪里？

---

## 下一章预告

`Toolkit` 的"大"是空间维度的问题。接下来我们看时间维度的设计选择——Hook 为什么在类定义时注入（编译期），而不是在调用时添加（运行时）？
