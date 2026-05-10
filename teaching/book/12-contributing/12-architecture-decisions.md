# 架构决策记录

> **Level 9**: 能参与架构讨论
> **前置要求**: [调试指南](./12-debugging-guide.md)
> **后续章节**: [术语表](../appendices/appendix_a.md)

---

## 学习目标

学完本章后，你能：
- 理解 AgentScope 的核心架构决策
- 知道为什么选择当前设计方案
- 参与架构层面的讨论
- 提出合理的重构建议

---

## ADR (Architecture Decision Records)

### ADR-001: 使用 TypedDict 而非 Pydantic

**日期**: 2024-Q1
**状态**: 已采纳

**背景**: 需要为工具参数生成 JSON Schema，但不需要运行时验证。

**决策**: 使用 `TypedDict(total=False)` 而非 Pydantic Model。

**理由**:
1. LLM 已经理解 Schema，会生成正确格式的参数
2. Python 函数调用时自动类型转换
3. TypedDict 更轻量，避免额外依赖

**后果**:
- 优点：性能更好，无额外依赖
- 缺点：类型错误只能在运行时发现

**替代方案**:
```python
# 方案 1: Pydantic (被否决)
class ToolParams(BaseModel):
    arg1: str
    arg2: int

# 方案 2: TypedDict (已采纳)
class ToolParams(TypedDict, total=False):
    arg1: str
    arg2: int
```

---

### ADR-002: 使用 ContextVar 管理配置

**日期**: 2024-Q1
**状态**: 已采纳

**背景**: 需要在异步环境中安全地传递配置（如 run_id, trace_enabled）。

**决策**: 使用 `contextvars.ContextVar` 而非全局变量或单例。

**理由**:
1. 线程安全，适合 asyncio
2. 自动清理，不会泄漏
3. 支持嵌套上下文的隔离

**实现**:
```python
from contextvars import ContextVar

_config = ContextVar("config")

# 设置
_config.set({"run_id": "123", "trace_enabled": True})

# 获取（自动继承）
config = _config.get()
```

---

### ADR-003: 单文件 Toolkit 设计

**日期**: 2024-Q1
**状态**: 待重构

**背景**: `_toolkit.py` 已超过 1600 行，难以维护。

**决策**: 保持单文件，标记 TODO，等待重构时机。

**理由**:
1. 拆分风险高，可能破坏现有功能
2. 各模块共享状态紧密
3. 重构需要大量测试覆盖

**技术债**:
```python
# TODO (from _toolkit.py:4):
# We should consider to split this Toolkit class in the future.
```

---

### ADR-004: ReActAgent 循环控制

**日期**: 2024-Q2
**状态**: 已采纳

**背景**: 需要防止 Agent 进入死循环。

**决策**: 使用 `max_iters` 限制循环次数。

**实现**:
```python
for _ in range(self.max_iters):  # 默认 5 次
    msg_reasoning = await self._reasoning()
    if not msg_reasoning.has_content_blocks("tool_use"):
        break
else:
    # 达到上限，执行总结
    reply_msg = await self._summarizing()
```

**替代方案**:
- _timeout 机制（可能导致不完整回复）
- LLM 自判断（不可靠）

---

### ADR-005: 流式输出架构

**日期**: 2024-Q2
**状态**: 已采纳

**背景**: 需要支持流式输出以提升用户体验。

**决策**: 通过 `stream=True` 参数启用，内部使用 async generator。

**实现**:
```python
if self.stream:
    async for content_chunk in res:
        msg.content = content_chunk.content
        await self.print(msg, False)  # 实时打印
```

---

## 架构原则

### 1. 组合优于继承

```python
# 推荐：组合
agent = ReActAgent(
    model=model,
    toolkit=toolkit,
    memory=memory,
    formatter=formatter,
)

# 不推荐：深层继承
class MyAgent(ReActAgentBase):
    ...
```

### 2. 接口抽象

```python
# 定义接口
class ChatModelBase(ABC):
    @abstractmethod
    async def __call__(self, ...) -> ChatResponse: ...

# 实现接口
class OpenAIChatModel(ChatModelBase): ...
```

### 3. 依赖注入

```python
# 好：依赖注入
def __init__(self, model: ChatModelBase, toolkit: Toolkit): ...

# 不好：硬编码
def __init__(self):
    self.model = OpenAIChatModel()  # 硬编码
```

---

## 参与架构讨论

### 如何提出 ADR

1. 在 GitHub Issues 创建讨论
2. 描述问题和背景
3. 提出解决方案
4. 讨论利弊
5. Maintainer 决策

### 评审要点

- 是否解决实际问题？
- 是否有足够的测试覆盖？
- 是否有破坏性变更？
- 是否符合现有架构原则？

---

## 下一步

接下来学习 [术语表](../appendices/appendix_a.md)。


---

## 工程现实与架构问题

### 技术债 (源码级)

| 位置 | 问题 | 影响 | 优先级 |
|------|------|------|--------|
| ADR-003 | 单文件 Toolkit 未拆分 | 1684 行难以维护 | 高 |
| ADR-001 | TypedDict 无运行时验证 | 类型错误只在运行时发现 | 中 |
| `_react_agent.py:2` | ReActAgent 过于庞大 | 1137 行难以扩展新功能 | 高 |
| 无 ADR 废弃机制 | 旧 ADR 状态不更新 | 难以了解决策历史 | 低 |

**[HISTORICAL INFERENCE]**: ADR-003 的"待重构"状态反映了维护者的两难：重构风险高收益不确定。TypedDict 选择是在性能和安全性之间的权衡，运行时验证的缺失是已知 trade-off。

### 性能考量

```python
# 架构决策对性能的影响

# TypedDict vs Pydantic
TypedDict:     ~0.01ms/对象创建 (无验证开销)
Pydantic:      ~0.1-0.5ms/对象创建 (运行时验证)

# ContextVar vs 全局变量
ContextVar:    ~0.001ms/访问 (线程安全)
threading.local: ~0.01ms/访问

# 单文件 vs 模块化
单文件导入:    ~50ms (一次加载)
模块化导入:    ~10ms/模块 (惰性加载优势)
```

### 渐进式重构方案

```python
# 方案 1: Toolkit 渐进式拆分
# Phase 1: 保持接口不变，内部按职责组织
class Toolkit:
    def __init__(self):
        self._registry = _ToolRegistry()      # 新增内部类
        self._schema_gen = _SchemaGenerator()  # 新增内部类
        self._executor = _ToolExecutor()       # 新增内部类

    def register_tool_function(self, func, ...):
        self._registry.register(func)

    def get_json_schemas(self):
        return self._schema_gen.generate()

# Phase 2: 提取为独立模块
# toolkit/_registry.py
# toolkit/_schema.py
# toolkit/_executor.py

# 方案 2: 添加 ADR 废弃机制
class ADR:
    def __init__(self, id: str, title: str, status: str):
        self.id = id
        self.title = title
        self.status = status  # "active", "deprecated", "superseded"
        self.superseded_by: str | None = None

    def deprecate(self, superseded_by: str) -> None:
        self.status = "deprecated"
        self.superseded_by = superseded_by
        # 自动更新文档链接
```

