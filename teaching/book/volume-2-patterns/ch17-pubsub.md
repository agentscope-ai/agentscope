# 第十七章：发布-订阅——多 Agent 通信的中心枢纽

**难度**：中等

> 你搭了一个三人讨论组：研究员、审稿人和编辑。你在 MsgHub 里让他们轮流发言，但编辑收到了研究员消息的两次副本——一次来自 auto-broadcast，一次来自你手动的 `agent.observe()`。问题出在哪？这一章拆解 MsgHub 的发布-订阅机制：消息如何在 Agent 之间流动，订阅关系如何建立与拆除。

---

## 1. 开场场景

你的代码看起来很合理：

```python
from agentscope.pipeline import MsgHub
from agentscope.agent import ReActAgent

researcher = ReActAgent(name="researcher", ...)
reviewer = ReActAgent(name="reviewer", ...)
editor = ReActAgent(name="editor", ...)

async with MsgHub(participants=[researcher, reviewer, editor]):
    r1 = await researcher("论文主题：发布-订阅模式")
    # 你怕 editor 没收到，手动 observe 了一次
    await editor.observe(r1)

    e1 = await editor("请修改摘要")
```

运行结果：`editor` 的 memory 里出现了两条一模一样的 `r1`。原因很简单——MsgHub 的 `enable_auto_broadcast` 默认为 `True`。当 `researcher` 产生回复后，`_broadcast_to_subscribers` 已经自动把消息推给了所有订阅者（包括 `editor`）。你手动调用的 `observe` 是第二次推送。

修复：删掉手动 `observe`，信任 Hub。

```python
async with MsgHub(participants=[researcher, reviewer, editor]):
    await researcher("论文主题：发布-订阅模式")  # auto-broadcast 给 reviewer 和 editor
    await editor("请修改摘要")                   # auto-broadcast 给 researcher 和 reviewer
```

这个 bug 引出了一个核心问题：MsgHub 的广播机制到底是怎么实现的？

---

## 2. 设计模式概览

MsgHub 实现了经典的**发布-订阅（Publish-Subscribe）** 模式。中心化 Hub 管理订阅关系，消息发布者不需要知道谁在接收。

```
                    MsgHub (context manager)
                    ┌─────────────────────────┐
                    │ participants: [A, B, C]  │
                    │ enable_auto_broadcast    │
                    └──────┬──────────┬────────┘
                           │          │
              __aenter__   │          │  __aexit__
              reset_subscribers       │  remove_subscribers
                           │          │
         ┌─────────────────v----------v───────────┐
         │                                          │
         │   Agent A          Agent B       Agent C │
         │  _subscribers:     _subscribers:  ...    │
         │    hub_name:         hub_name:           │
         │      [B, C]           [A, C]             │
         │                                          │
         └──────────────────────────────────────────┘

Agent A 发言 (reply)
    │
    v
_broadcast_to_subscribers(reply_msg)
    │  遍历 _subscribers[hub_name]
    v
B.observe(msg)  ──>  C.observe(msg)
```

关键设计选择：
- **Hub 只管订阅关系**，不存储消息（消息在 Agent 的 memory 里）
- **广播发生在 `__call__` 的 `finally` 块**，即 Agent 完成回复后自动执行
- **订阅者列表排除自身**，`reset_subscribers` 里用 `if _ != self` 过滤

源码入口：

```
src/agentscope/pipeline/
├── _msghub.py          MsgHub 上下文管理器
├── _class.py           SequentialPipeline, FanoutPipeline
├── _functional.py      函数式管道
└── __init__.py         导出 MsgHub, SequentialPipeline, FanoutPipeline 等
```

---

## 3. 源码分析

### 3.1 订阅关系的建立：`_reset_subscriber`

当进入 `async with MsgHub(...)` 时，`__aenter__` 被调用（`_msghub.py` 第 73 行）：

```python
async def __aenter__(self) -> "MsgHub":
    self._reset_subscriber()

    if self.announcement is not None:
        await self.broadcast(msg=self.announcement)

    return self
```

`_reset_subscriber`（第 89 行）遍历所有参与者，给每个 Agent 设置订阅者：

```python
def _reset_subscriber(self) -> None:
    if self.enable_auto_broadcast:
        for agent in self.participants:
            agent.reset_subscribers(self.name, self.participants)
```

跳到 `AgentBase.reset_subscribers`（`_agent_base.py` 第 701 行）：

```python
def reset_subscribers(
    self,
    msghub_name: str,
    subscribers: list["AgentBase"],
) -> None:
    self._subscribers[msghub_name] = [_ for _ in subscribers if _ != self]
```

三个关键点：
1. **以 MsgHub 的 `name` 为 key**，所以一个 Agent 可以同时属于多个 Hub，互不干扰
2. **排除自身** `_ != self`，避免 Agent 收到自己的回复
3. **整个列表替换**，不是增量追加——每次 `add` 或 `delete` 都会重建所有订阅关系

### 3.2 自动广播：`_broadcast_to_subscribers`

当 Agent 被 `await agent(msg)` 调用时，`AgentBase.__call__` 执行（`_agent_base.py` 第 450-467 行）：

```python
reply_msg: Msg | None = None
try:
    self._reply_task = asyncio.current_task()
    reply_msg = await self.reply(*args, **kwargs)
except asyncio.CancelledError:
    reply_msg = await self.handle_interrupt(*args, **kwargs)
finally:
    # Broadcast the reply message to all subscribers
    if reply_msg:
        await self._broadcast_to_subscribers(reply_msg)
    self._reply_task = None
```

`finally` 块确保无论 `reply` 正常返回还是被取消，广播逻辑都会执行。`_broadcast_to_subscribers`（第 469 行）：

```python
async def _broadcast_to_subscribers(
    self,
    msg: Msg | list[Msg] | None,
) -> None:
    if msg is None:
        return

    broadcast_msg = self._strip_thinking_blocks(msg)

    for subscribers in self._subscribers.values():
        for subscriber in subscribers:
            await subscriber.observe(broadcast_msg)
```

注意两层循环：外层遍历所有 MsgHub（`_subscribers.values()`），内层遍历每个 Hub 下的订阅者。这意味着如果一个 Agent 同时在两个 MsgHub 里，两个 Hub 的成员都会收到消息。

还要注意 `_strip_thinking_blocks`——Agent 的内部推理（thinking blocks）在广播前被剥离，其他 Agent 看不到推理过程。

### 3.3 动态订阅管理：`add` 和 `delete`

MsgHub 支持运行时增减参与者（`_msghub.py` 第 95-128 行）：

```python
def add(self, new_participant: list[AgentBase] | AgentBase) -> None:
    if isinstance(new_participant, AgentBase):
        new_participant = [new_participant]

    for agent in new_participant:
        if agent not in self.participants:
            self.participants.append(agent)

    self._reset_subscriber()
```

```python
def delete(self, participant: list[AgentBase] | AgentBase) -> None:
    if isinstance(participant, AgentBase):
        participant = [participant]

    for agent in participant:
        if agent in self.participants:
            self.participants.pop(self.participants.index(agent))
        else:
            logger.warning(
                "Cannot find the agent with ID %s, skip its deletion.",
                agent.id,
            )

    self._reset_subscriber()
```

两个方法最后都调用 `_reset_subscriber()`，重建所有参与者的订阅列表。这意味着：
- 新加入的 Agent 会立即收到后续消息
- 被移除的 Agent 不再收到任何消息
- **但历史消息不会重发**——如果 Agent 错过了 Hub 里的早期对话，需要自行补发

### 3.4 清理：`__aexit__`

退出 `async with` 块时（第 83 行）：

```python
async def __aexit__(self, *args: Any, **kwargs: Any) -> None:
    if self.enable_auto_broadcast:
        for agent in self.participants:
            agent.remove_subscribers(self.name)
```

`remove_subscribers`（`_agent_base.py` 第 717 行）直接从 `_subscribers` 字典中删除对应 Hub 的 key。退出 Hub 后，Agent 之间不再自动广播。

### 3.5 手动广播与公告

MsgHub 提供两层广播机制：

1. **`announcement`**：进入 Hub 时一次性广播给所有参与者（`__aenter__` 第 78-79 行）
2. **`broadcast()`**：在 Hub 内部随时手动广播（第 130 行）

```python
async def broadcast(self, msg: list[Msg] | Msg) -> None:
    for agent in self.participants:
        await agent.observe(msg)
```

`broadcast` 发给**所有人**，包括发送者自己——这是与 auto-broadcast 的区别。如果需要"主持人"向全体成员发布通知，用 `broadcast`；如果是 Agent 之间的自动通知，auto-broadcast 已经排除了发送者。

### 3.6 Pipeline：消息流的编排

Pipeline 解决的是"谁先说、谁后说"的问题。`SequentialPipeline`（`_class.py` 第 10 行）是最简单的：

```python
class SequentialPipeline:
    def __init__(self, agents: list[AgentBase]) -> None:
        self.agents = agents

    async def __call__(self, msg=None):
        return await sequential_pipeline(agents=self.agents, msg=msg)
```

底层函数 `sequential_pipeline`（`_functional.py` 第 10 行）：

```python
async def sequential_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
) -> Msg | list[Msg] | None:
    for agent in agents:
        msg = await agent(msg)
    return msg
```

每个 Agent 的输出成为下一个 Agent 的输入。如果结合 MsgHub 使用，Pipeline 负责**显式调用顺序**，MsgHub 负责**隐式广播**——两者互补。

`FanoutPipeline`（`_functional.py` 第 47 行）将同一条消息分发给多个 Agent，支持并发执行：

```python
async def fanout_pipeline(
    agents: list[AgentBase],
    msg: Msg | list[Msg] | None = None,
    enable_gather: bool = True,
) -> list[Msg]:
    if enable_gather:
        tasks = [
            asyncio.create_task(agent(deepcopy(msg), **kwargs))
            for agent in agents
        ]
        return await asyncio.gather(*tasks)
    else:
        return [await agent(deepcopy(msg), **kwargs) for agent in agents]
```

注意 `deepcopy(msg)`——每个 Agent 收到独立的副本，避免共享引用导致的状态污染。

---

## 4. 设计一瞥

### 为什么用中心化的 Hub 而不是点对点通信？

假设三个 Agent 需要互相通信：

**点对点方案**：
```python
r1 = await researcher("主题")
await reviewer.observe(r1)
await editor.observe(r1)
# 每新增一个 Agent，需要修改所有已有 Agent 的通知逻辑
```

**Hub 方案**：
```python
async with MsgHub(participants=[researcher, reviewer, editor]):
    await researcher("主题")
    # 自动广播，无需手动 observe
```

Hub 的优势：
1. **O(N) vs O(N^2)**：新增 Agent 只需加入 Hub，不需要修改其他 Agent 的代码
2. **生命周期管理**：`__aenter__` 建立订阅，`__aexit__` 清理，不会泄漏
3. **多 Hub 隔离**：`_subscribers` 以 Hub name 为 key，一个 Agent 可以同时参与多个讨论组

### 为什么 `_reset_subscriber` 是全量重建而非增量更新？

看代码（`_msghub.py` 第 89-93 行），每次 `add` 或 `delete` 都重建所有 Agent 的订阅列表。这看似低效，但有好处：
- **实现简单**：不需要追踪"哪个 Agent 的订阅列表需要更新"
- **一致性强**：所有 Agent 的订阅关系始终与 `self.participants` 保持一致
- **N 通常很小**：多 Agent 场景中，参与者数量一般在个位数到几十

### 思考块剥离的设计

`_strip_thinking_blocks`（`_agent_base.py` 第 487 行）在广播前移除 Agent 的内部推理。这意味着：Agent 的"内心独白"不会泄露给其他 Agent。这是隐私保护也是效率优化——其他 Agent 不需要处理大段的推理文本。

---

## 5. 横向对比

### AutoGen

AutoGen 使用 **`ConversableAgent` 的 `send` / `receive`** 模型。Agent 之间直接传递消息，没有中心化 Hub：

```python
# AutoGen 风格
agent1.send(message, agent2)  # 点对点
# 群聊通过 GroupChat manager 中转
```

AutoGen 的 `GroupChat` 类似 MsgHub，但由一个专门的 "manager" Agent 负责路由，而非订阅机制。

### CrewAI

CrewAI 使用 **Process** 枚举（`sequential`、`hierarchical`）定义通信拓扑。消息传递隐式发生在 Task 链中，不暴露给用户。与 MsgHub 的区别：CrewAI 侧重流程编排，不提供运行时的动态订阅管理。

### LangGraph

LangGraph 基于**有向图**定义 Agent 间的消息流。节点是 Agent，边是消息传递路径。比 MsgHub 更灵活（可以有条件路由），但也更复杂。MsgHub 是"全连接广播"的简化特例。

### 对比总结

| 特性 | MsgHub | AutoGen GroupChat | CrewAI | LangGraph |
|------|--------|-------------------|--------|-----------|
| 拓扑 | 全连接广播 | Manager 路由 | Process 链 | 有向图 |
| 动态成员 | add/delete | 支持 | 不支持 | 支持 |
| 广播粒度 | 全体/排除自身 | Manager 决定 | 隐式 | 边定义 |
| 实现复杂度 | 低 | 中 | 低 | 高 |

---

## 6. 调试实践

### 场景：消息丢失

三个 Agent 在 MsgHub 中讨论，但 `reviewer` 似乎没收到 `researcher` 的消息。

**排查步骤**：

1. 确认 `enable_auto_broadcast` 没被设为 `False`：
```python
hub = MsgHub(participants=[...], enable_auto_broadcast=True)
```

2. 检查 `reviewer.observe` 是否被正确实现。ReActAgent 的 `observe` 会将消息写入 memory——确认 memory 没有被清空。

3. 检查 Hub 的生命周期：
```python
# 错误：Hub 已退出，auto-broadcast 已清理
async with MsgHub(participants=[researcher, reviewer]):
    pass  # Hub 在这里关闭

await researcher("消息")  # 此时没有订阅者
```

4. 添加日志追踪广播：
```python
import logging
logging.getLogger("agentscope").setLevel(logging.DEBUG)
```

### 场景：消息重复

这正是开场场景的问题。排查方法：

1. 检查是否手动调用了 `observe` 与 auto-broadcast 重复。
2. 检查 Agent 是否同时属于两个 Hub——`_broadcast_to_subscribers` 遍历 `_subscribers.values()`，多个 Hub 会导致多次接收。
3. 检查 `announcement` 和后续 `broadcast` 是否发送了相同内容。

### 场景：有序讨论

如果需要严格的消息顺序（研究员说→审稿人说→编辑说），结合 `SequentialPipeline` 和 `MsgHub`：

```python
from agentscope.pipeline import MsgHub, SequentialPipeline

async with MsgHub(participants=[researcher, reviewer, editor]):
    pipeline = SequentialPipeline([researcher, reviewer, editor])
    await pipeline("讨论主题：发布-订阅模式")
```

Pipeline 控制"谁先发言"，MsgHub 确保"每个人都能听到其他人的发言"。

---

## 7. 检查点

1. **基础**：MsgHub 的 `__aenter__` 做了哪两件事？
2. **机制**：`_broadcast_to_subscribers` 在什么时候被调用？为什么放在 `finally` 块？
3. **隔离**：一个 Agent 同时加入两个 MsgHub，消息会怎样流动？`_subscribers` 字典的 key 是什么？
4. **陷阱**：`broadcast()` 方法和 auto-broadcast 有什么区别？`broadcast()` 发给谁？
5. **清理**：退出 MsgHub 后，Agent 之间还会自动通信吗？`__aexit__` 做了什么？
6. **设计**：为什么 `FanoutPipeline` 对消息做 `deepcopy`，而 MsgHub 的 auto-broadcast 不做？

---

## 8. 下一章预告

消息在 Agent 之间流动时，谁来决定"下一步该谁说"——状态机与流程编排。
