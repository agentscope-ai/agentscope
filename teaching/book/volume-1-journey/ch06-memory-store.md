# 第 6 章：第 3 站：工作记忆

消息从外部世界涌入 Agent，第一步不是"思考"，而是"记住"。AgentScope 把这条消息存入一个叫**工作记忆（Working Memory）** 的容器，后续所有推理都从这里取数据。本章我们打开这个容器，看看它的内部结构。

---

## 6.1 路线图

```mermaid
flowchart LR
    A[外部消息 Msg] --> B[Memory.add]
    B --> C["content 列表<br/>list&lt;tuple&gt;"]
    C --> D[Memory.get_memory]
    D --> E[过滤 mark]
    E --> F[Agent 推理]
    F --> G[新消息]
    G --> B
```

一条消息的生命周期：

1. **进入**：`add()` 把 `Msg` 连同可选的 mark 存入 `content` 列表。
2. **存储**：`content` 是一个 `list[tuple[Msg, list[str]]]`，每个元素是一对（消息, 标记列表）。
3. **检索**：`get_memory()` 根据 mark 过滤，返回纯净的 `list[Msg]`。
4. **消费**：Agent 把拿到的消息列表交给 Model 做 LLM 推理。
5. **循环**：Model 的回复再次被 `add()` 存入记忆。

我们的阅读路径：先看抽象基类 `MemoryBase` 定义了哪些能力，再看 `InMemoryMemory` 如何实现。

---

## 6.2 知识补全

### 抽象基类（Abstract Base Class）

AgentScope 用 Python 的 `abc.ABC` 体系定义接口。`MemoryBase` 继承自 `StateModule`（一个支持序列化/反序列化的基类），然后声明一组 `@abstractmethod`：子类**必须**实现这些方法，否则无法实例化。

这种模式的好处：上层代码只依赖 `MemoryBase` 的接口签名，不关心底层用的是内存列表、Redis 还是数据库。替换实现时，业务代码一行不改。

### tuple 与 list

- `tuple`：不可变序列，创建后不能增删元素。适合表示"一条记录"。
- `list`：可变序列，可以增删改。`content` 本身是 list，允许动态添加消息。

### deepcopy（深拷贝）

`deepcopy` 递归复制对象及其所有嵌套引用。存入记忆时做 deepcopy，意味着后续对原始消息的修改不会影响已存储的副本——记忆是**隔离的快照**。

---

## 6.3 源码入口

```
src/agentscope/memory/
  _working_memory/
    _base.py              # MemoryBase —— 抽象基类
    _in_memory_memory.py  # InMemoryMemory —— 内存实现
```

两个文件，总共约 470 行代码，是工作记忆的全部。

---

## 6.4 逐行阅读

### 6.4.1 MemoryBase：定义能力契约

文件：`_base.py`

```python
# 第 4 行
from abc import abstractmethod
from ...message import Msg
from ...module import StateModule
```

`MemoryBase` 继承 `StateModule`（第 11 行），获得 `register_state()`、`state_dict()` 等序列化能力。子类的属性只要通过 `register_state()` 注册，就能被自动保存和恢复。

```python
# 第 14-20 行
def __init__(self) -> None:
    super().__init__()
    self._compressed_summary: str = ""
    self.register_state("_compressed_summary")
```

`_compressed_summary` 是一个压缩摘要字符串。当记忆中积累了大量消息，可以用一段摘要文本替代它们，节省 token。默认为空字符串，不启用。

```python
# 第 32-47 行 —— add
@abstractmethod
async def add(
    self,
    memories: Msg | list[Msg] | None,
    marks: str | list[str] | None = None,
    **kwargs: Any,
) -> None:
```

`add` 是最核心的方法。它接受一条或多条消息，以及可选的 mark。注意 `marks` 参数的类型：`str | list[str] | None`，三种形态都合法。这种宽进严出的设计在 AgentScope 中很常见——调用方不必总是构造列表。

```python
# 第 50-64 行 —— delete
@abstractmethod
async def delete(
    self,
    msg_ids: list[str],
    **kwargs: Any,
) -> int:
```

按消息 ID 删除。每条 `Msg` 有唯一的 `id` 属性。返回值是实际删除的数量。

```python
# 第 66-89 行 —— delete_by_mark
async def delete_by_mark(
    self,
    mark: str | list[str],
    *args: Any,
    **kwargs: Any,
) -> int:
    raise NotImplementedError(...)
```

注意这个方法**不是** `@abstractmethod`——它在基类中提供了默认实现（抛出 `NotImplementedError`）。这意味着子类可以不实现它，只有需要按 mark 删除的子类才去覆盖。这是一种"可选能力"的设计模式。

```python
# 第 105-132 行 —— get_memory
@abstractmethod
async def get_memory(
    self,
    mark: str | None = None,
    exclude_mark: str | None = None,
    prepend_summary: bool = True,
    **kwargs: Any,
) -> list[Msg]:
```

三个参数揭示了 mark 机制的完整用法：

- `mark`：只返回带有这个 mark 的消息。
- `exclude_mark`：排除带有这个 mark 的消息。
- `prepend_summary`：如果 `_compressed_summary` 非空，在最前面插入一条包含摘要的 `Msg`。

两个参数可以组合：`mark="planning"` 且 `exclude_mark="draft"` 意味着"只要标记为 planning 但不是 draft 的消息"。

此外，基类还定义了 `size()`、`clear()` 和 `update_messages_mark()`（第 134-168 行），构成完整的 CRUD 接口。

### 6.4.2 InMemoryMemory：内存中的实现

文件：`_in_memory_memory.py`

```python
# 第 13-20 行
def __init__(self) -> None:
    super().__init__()
    self.content: list[tuple[Msg, list[str]]] = []
    self.register_state("content")
```

这就是整个存储结构：一个列表，每个元素是一个二元组 `(Msg, marks)`。`marks` 是字符串列表，一条消息可以有零个、一个或多个 mark。

#### get_memory 的过滤逻辑

```python
# 第 67-71 行
filtered_content = [
    (msg, marks)
    for msg, marks in self.content
    if mark is None or mark in marks
]
```

列表推导式（list comprehension）做过滤。如果 `mark` 为 `None`，不过滤，返回全部；否则只保留 marks 中包含指定 mark 的消息。

```python
# 第 74-79 行
if exclude_mark is not None:
    filtered_content = [
        (msg, marks)
        for msg, marks in filtered_content
        if exclude_mark not in marks
    ]
```

第二步排除。两步串行，逻辑清晰。

```python
# 第 81-91 行
if prepend_summary and self._compressed_summary:
    return [
        Msg("user", self._compressed_summary, "user"),
        *[msg for msg, _ in filtered_content],
    ]
return [msg for msg, _ in filtered_content]
```

如果压缩摘要存在且 `prepend_summary=True`，构造一条新的 `Msg` 前置到结果列表。星号表达式 `*[...]` 把内部列表展开，拼成一个完整的 `list[Msg]`。

#### add 的去重机制

```python
# 第 112-135 行
if memories is None:
    return
if isinstance(memories, Msg):
    memories = [memories]
```

先将输入统一为列表。然后处理 marks：

```python
if marks is None:
    marks = []
elif isinstance(marks, str):
    marks = [marks]
```

同样是宽进：`None` 变空列表，单个字符串变单元素列表。

```python
# 第 130-132 行
if not allow_duplicates:
    existing_ids = {msg.id for msg, _ in self.content}
    memories = [msg for msg in memories if msg.id not in existing_ids]
```

默认不允许重复。通过集合（set）快速查重——集合的 `in` 操作是 O(1)，比列表的 O(n) 快得多。

```python
# 第 134-135 行
for msg in memories:
    self.content.append((deepcopy(msg), deepcopy(marks)))
```

每条消息独立做 deepcopy 后追加。注意 marks 也做了 deepcopy——这是为了防止多条消息共享同一个 marks 列表引用，修改一处影响全局。

#### delete_by_mark 的逐 mark 过滤

```python
# 第 191-197 行
initial_size = len(self.content)
for m in mark:
    self.content = [
        (msg, marks) for msg, marks in self.content if m not in marks
    ]
return initial_size - len(self.content)
```

如果传入多个 mark，逐一过滤。每轮都重建列表，排除含有当前 mark 的消息。

---

## 6.5 设计一瞥

> **为什么用 `list[tuple[Msg, list[str]]]` 而不是 `dict`？**
>
> 三个原因：
>
> 1. **消息有序**。对话是时序性的，"你好"在"再见"之前，这个顺序不可丢失。`dict` 在 Python 3.7+ 虽然保持插入顺序，但它的语义是"键值映射"，暗示通过 key 查找是主要操作——而记忆的主要操作是遍历，不是按键查找。
>
> 2. **同一消息可以有多个 mark**。如果用 `dict[str, Msg]`（mark -> 消息），一条消息有多个 mark 时要么重复存储，要么丢失 mark。用 `tuple[Msg, list[str]]` 天然支持一对多。
>
> 3. **不需要唯一性约束**。`dict` 的 key 必须唯一，但消息可能内容相同而 mark 不同（比如同一段文本在不同上下文中被赋予不同的可见性标记）。列表没有这个限制。
>
> 更进一步，为什么不用数据库？因为 `InMemoryMemory` 的设计目标是**轻量和快速**——单进程、短对话、不需要持久化。后面我们会看到 Redis 和 SQLAlchemy 实现，它们处理的是跨进程、需要持久化的场景。

---

## 6.6 调试实践

在调试记忆相关问题时，以下几个技巧很有用：

**1. 检查 content 的原始状态**

```python
# 直接访问 content 属性，查看消息和 mark 的对应关系
for i, (msg, marks) in enumerate(memory.content):
    print(f"[{i}] msg.id={msg.id}, marks={marks}, content={msg.content[:50]}")
```

**2. 追踪 get_memory 的过滤结果**

```python
# 不带 mark —— 看全部
all_msgs = await memory.get_memory(mark=None, prepend_summary=False)
print(f"Total: {len(all_msgs)}")

# 带 mark —— 看过滤后
filtered = await memory.get_memory(mark="planning", prepend_summary=False)
print(f"Filtered: {len(filtered)}")
```

**3. 检查压缩摘要**

```python
print(f"Summary: '{memory._compressed_summary}'")
```

如果摘要为空字符串，说明压缩未启用或尚未触发。

---

## 6.7 试一试

下面这段代码可以在任何 Python 环境中运行，不需要 API key。它会创建一个 `InMemoryMemory`，添加几条消息并观察 mark 过滤效果。

```python
import asyncio
from agentscope.message import Msg
from agentscope.memory import InMemoryMemory


async def main():
    # 创建记忆存储
    memory = InMemoryMemory()

    # 创建几条消息
    m1 = Msg(name="user", content="你好，今天天气怎么样？", role="user")
    m2 = Msg(name="assistant", content="今天北京晴，25度。", role="assistant")
    m3 = Msg(name="user", content="帮我规划一个户外活动。", role="user")
    m4 = Msg(name="assistant", content="建议去公园野餐。", role="assistant")

    # 添加消息，带不同的 mark
    await memory.add(m1)                           # 无 mark
    await memory.add(m2, marks="weather")          # mark: weather
    await memory.add(m3, marks="planning")         # mark: planning
    await memory.add(m4, marks=["planning", "suggestion"])  # 两个 mark

    # 查看总数
    print(f"总消息数: {await memory.size()}")  # 4

    # 获取全部消息（不过滤 mark）
    all_msgs = await memory.get_memory(prepend_summary=False)
    print(f"\n全部消息 ({len(all_msgs)} 条):")
    for msg in all_msgs:
        print(f"  [{msg.name}] {msg.content}")

    # 只获取带 "planning" mark 的消息
    planning = await memory.get_memory(mark="planning", prepend_summary=False)
    print(f"\nplanning 消息 ({len(planning)} 条):")
    for msg in planning:
        print(f"  [{msg.name}] {msg.content}")

    # 排除 "suggestion" mark
    no_suggestion = await memory.get_memory(
        exclude_mark="suggestion", prepend_summary=False
    )
    print(f"\n排除 suggestion 后 ({len(no_suggestion)} 条):")
    for msg in no_suggestion:
        print(f"  [{msg.name}] {msg.content}")

    # 测试压缩摘要
    await memory.update_compressed_summary("用户询问了天气并请求活动规划。")
    with_summary = await memory.get_memory(prepend_summary=True)
    print(f"\n带摘要的消息列表 (第1条是摘要):")
    print(f"  [{with_summary[0].name}] {with_summary[0].content}")

    # 删除 planning 相关消息
    deleted = await memory.delete_by_mark("planning")
    print(f"\n删除了 {deleted} 条 planning 消息")
    print(f"剩余: {await memory.size()} 条")


asyncio.run(main())
```

**预期输出：**

```
总消息数: 4

全部消息 (4 条):
  [user] 你好，今天天气怎么样？
  [assistant] 今天北京晴，25度。
  [user] 帮我规划一个户外活动。
  [assistant] 建议去公园野餐。

planning 消息 (2 条):
  [user] 帮我规划一个户外活动。
  [assistant] 建议去公园野餐。

排除 suggestion 后 (3 条):
  [user] 你好，今天天气怎么样？
  [assistant] 今天北京晴，25度。
  [user] 帮我规划一个户外活动。

带摘要的消息列表 (第1条是摘要):
  [user] 用户询问了天气并请求活动规划。

删除了 2 条 planning 消息
剩余: 2 条
```

**思考题**：如果一条消息同时有 `mark="planning"` 和 `exclude_mark="planning"`，`get_memory` 会怎么处理？阅读源码第 67-79 行，验证你的猜想。

---

## 6.8 检查点

阅读到这里，你应该能够回答以下问题：

1. **工作记忆的存储结构是什么？** —— `list[tuple[Msg, list[str]]]`，每个元素是一条消息加上它的 mark 列表。
2. **mark 机制解决什么问题？** —— 给消息打标签，让不同上下文可以选择性地看到不同子集。同一消息可以有多个 mark，`get_memory` 支持正向过滤（`mark`）和反向排除（`exclude_mark`）。
3. **`_compressed_summary` 什么时候生效？** —— 当它非空且 `prepend_summary=True` 时，`get_memory` 会在结果列表最前面插入一条包含摘要的 `Msg`。它是外部压缩策略的结果，`InMemoryMemory` 本身不执行压缩。
4. **为什么 `add` 要做 `deepcopy`？** —— 确保记忆中的消息是独立副本，外部修改不会污染已存储的数据。
5. **`delete_by_mark` 为什么不是抽象方法？** —— 它是可选能力。基类用 `raise NotImplementedError` 提供默认行为，只有支持按 mark 删除的子类才覆盖它。

如果你对以上问题都能给出清晰的回答，说明你已经理解了工作记忆的核心设计。

---

## 6.9 下一站预告

`InMemoryMemory` 把所有消息存在一个列表里。只要进程活着，记忆就在；进程结束，记忆消失。这对单次对话够用，但如果 Agent 需要跨会话记住用户偏好，或者多个 Agent 共享记忆呢？

工作记忆之外，还有**长期记忆**和**知识库**。下一站我们将看到 `RedisMemory` 如何将消息持久化到外部存储，以及 RAG（Retrieval-Augmented Generation）如何从海量文档中检索相关内容。
