# 智能体模块与 Hooks 深度剖析

## 目录

1. [模块概述](#1-模块概述)
2. [核心类继承体系](#2-核心类继承体系)
3. [AgentBase 源码解读](#3-agentbase-源码解读)
4. [ReActAgent 实现类分析](#4-reactagent-实现类分析)
5. [UserAgent 用户代理分析](#5-useragent-用户代理分析)
6. [Hook 机制分析](#6-hook-机制分析)
7. [设计模式总结](#7-设计模式总结)
8. [代码示例](#8-代码示例)
9. [练习题](#9-练习题)

---

## 学习目标

完成本模块学习后，您将能够：

| 目标层级 | 学习目标 | Bloom 动词 |
|----------|----------|-----------|
| 记忆 | 列举 AgentScope 智能体模块的核心类及其继承关系 | 列举、识别 |
| 理解 | 解释 AgentBase 的 `reply()`、`observe()`、`__call__()` 方法的设计意图与协作关系 | 解释、比较 |
| 应用 | 使用 Hook 机制为自定义智能体注入前置/后置处理逻辑 | 实现、配置 |
| 分析 | 分析 ReActAgent 的推理-行动循环流程，诊断循环中断的常见原因 | 分析、诊断 |
| 评价 | 评价不同智能体设计模式（模板方法、策略、观察者）在特定场景下的适用性 | 评价、推荐 |
| 创造 | 设计并实现一个具备自定义 Hook 和状态管理的多智能体协作系统 | 设计、构建 |

## 先修检查

在开始学习本模块之前，请确认您已掌握以下知识：

- [ ] Python 异步编程基础 (`async`/`await`)
- [ ] 面向对象编程核心概念（类、继承、抽象方法、元类）
- [ ] 装饰器的基本用法
- [ ] 设计模式基础（模板方法、观察者模式）

**预计学习时间**: 45 分钟

---

## 1. 模块概述

智能体模块是 AgentScope 框架的核心模块，负责实现智能体的基本行为和交互逻辑。该模块位于 `/Users/nadav/IdeaProjects/agentscope/src/agentscope/agent/` 目录下，主要包含以下组件：

### 1.1 目录结构

```
src/agentscope/agent/
├── __init__.py                    # 模块导出
├── _agent_base.py                 # AgentBase 基类（核心）
├── _agent_meta.py                 # 智能体元类
├── _react_agent_base.py           # ReActAgentBase 基类
├── _react_agent.py                # ReActAgent 实现
├── _user_agent.py                 # UserAgent 实现
├── _user_input.py                 # 用户输入基类
├── _a2a_agent.py                  # A2A 协议代理
├── _realtime_agent.py             # 实时代理
└── _utils.py                      # 工具函数
```

### 1.2 Hooks 模块结构

```
src/agentscope/hooks/
├── __init__.py                    # 模块导出
└── _studio_hooks.py               # Studio 钩子实现
```

### 1.3 核心功能

- **异步消息处理**：基于 asyncio 的异步消息接收和回复机制
- **生命周期管理**：支持中断、恢复、超时等生命周期控制
- **订阅发布机制**：支持多代理间的消息广播
- **Hook 扩展机制**：支持在关键节点注入自定义逻辑
- **流式输出**：支持文本、音频等内容的流式打印

---

## 2. 核心类继承体系

### 2.1 类图

```
┌─────────────────────────────────────────────────────────────┐
│                        StateModule                          │
│  (来自 module.py，提供状态序列化和恢复能力)                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       AgentBase                              │
│  文件: _agent_base.py:30                                    │
│                                                             │
│  - id: str                    # 唯一标识符                    │
│  - supported_hook_types       # 支持的钩子类型                 │
│  - _class_pre_reply_hooks    # 类级预回复钩子                 │
│  - _class_post_reply_hooks   # 类级后回复钩子                 │
│  - _instance_*_hooks         # 实例级钩子                     │
│  - _subscribers              # 订阅者列表                     │
│  - _reply_task               # 当前回复任务                    │
│                                                             │
│  + reply()                   # 核心回复方法（抽象）            │
│  + observe()                 # 消息观察方法（抽象）            │
│  + print()                   # 消息打印方法                   │
│  + register_class_hook()     # 注册类级钩子                   │
│  + register_instance_hook()  # 注册实例级钩子                 │
│  + __call__()                # 调用入口                      │
│  + interrupt()                # 中断回复                      │
│  + handle_interrupt()        # 处理中断（抽象）               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     ReActAgentBase                            │
│  文件: _react_agent_base.py:12                               │
│  元类: _ReActAgentMeta                                       │
│                                                             │
│  扩展钩子类型:                                                │
│  - pre_reasoning / post_reasoning                           │
│  - pre_acting / post_acting                                 │
│                                                             │
│  + _reasoning()              # 推理方法（抽象）               │
│  + _acting()                 # 行动方法（抽象）               │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                       ReActAgent                             │
│  文件: _react_agent.py:98                                    │
│                                                             │
│  - name                       # 代理名称                      │
│  - model                      # 聊天模型                     │
│  - formatter                  # 消息格式化器                  │
│  - toolkit                    # 工具包                       │
│  - memory                     # 工作记忆                      │
│  - long_term_memory           # 长期记忆                     │
│  - knowledge                  # 知识库列表                    │
│  - plan_notebook              # 计划笔记本                    │
│  - tts_model                  # TTS 模型                     │
│  - compression_config         # 压缩配置                     │
│                                                             │
│  + reply()                   # ReAct 循环实现                │
│  + _reasoning()              # 推理阶段                      │
│  + _acting()                 # 行动阶段                      │
│  + generate_response()       # 生成结构化响应                 │
│  + _compress_memory_if_needed() # 记忆压缩                   │
│  + _retrieve_from_long_term_memory() # 长期记忆检索          │
│  + _retrieve_from_knowledge() # 知识库检索                  │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 继承关系说明

| 类名 | 父类 | 文件位置 | 说明 |
|------|------|----------|------|
| `AgentBase` | `StateModule` | `_agent_base.py:30` | 所有代理的基类 |
| `ReActAgentBase` | `AgentBase` | `_react_agent_base.py:12` | ReAct 算法基础 |
| `ReActAgent` | `ReActAgentBase` | `_react_agent.py:98` | ReAct 算法完整实现 |
| `UserAgent` | `AgentBase` | `_user_agent.py:12` | 用户交互代理 |
| `A2AAgent` | `AgentBase` | `_a2a_agent.py` | A2A 协议代理 |
| `RealtimeAgent` | `AgentBase` | `_realtime_agent.py` | 实时交互代理 |

---

## 3. AgentBase 源码解读

### 3.1 类定义与初始化

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/agent/_agent_base.py`

```python
# _agent_base.py:30-44
class AgentBase(StateModule, metaclass=_AgentMeta):
    """Base class for asynchronous agents."""

    id: str
    """The agent's unique identifier, generated using shortuuid."""

    supported_hook_types: list[str] = [
        "pre_reply",
        "post_reply",
        "pre_print",
        "post_print",
        "pre_observe",
        "post_observe",
    ]
    # 第 36-43 行: 定义支持的钩子类型
```

**关键初始化逻辑** (`_agent_base.py:140-183`):

```python
# _agent_base.py:140-183
def __init__(self) -> None:
    """Initialize the agent."""
    super().__init__()

    self.id = shortuuid.uuid()  # 生成唯一ID

    # 回复任务管理 - 用于中断机制
    self._reply_task: Task | None = None
    self._reply_id: str | None = None

    # 实例级钩子初始化 - 按钩子类型分类存储
    self._instance_pre_print_hooks = OrderedDict()
    self._instance_post_print_hooks = OrderedDict()
    self._instance_pre_reply_hooks = OrderedDict()
    self._instance_post_reply_hooks = OrderedDict()
    self._instance_pre_observe_hooks = OrderedDict()
    self._instance_post_observe_hooks = OrderedDict()

    # 流式打印前缀缓存 - 避免重复打印
    # 格式: {msg_id: {"text": "已打印文本", "audio": (player, base64_data)}}
    self._stream_prefix = {}

    # 订阅者列表 - key: MsgHub名称, value: 订阅者智能体列表
    self._subscribers: dict[str, list[AgentBase]] = {}

    # 控制台输出控制 - 通过环境变量配置
    self._disable_console_output: bool = (
        os.getenv("AGENTSCOPE_DISABLE_CONSOLE_OUTPUT", "false").lower() == "true"
    )

    # 消息队列 - 用于流式输出时的异步处理
    self._disable_msg_queue: bool = True
    self.msg_queue = None
```

### 3.2 reply() 核心方法源码分析

**reply() 方法** (`_agent_base.py:197-203`) - 抽象方法定义:

```python
# _agent_base.py:197-203
async def reply(self, *args: Any, **kwargs: Any) -> Msg:
    """The main logic of the agent, which generates a reply based on the
    current state and input arguments."""
    raise NotImplementedError(
        "The reply function is not implemented in "
        f"{self.__class__.__name__} class.",
    )
```

**流程说明**: reply() 是智能体的核心抽象方法,所有子类必须实现。它接收任意参数,返回 `Msg` 对象。框架使用元类 `_AgentMeta` 在类创建时自动用钩子包装此方法。

### 3.3 observe() 方法源码分析

**observe() 方法** (`_agent_base.py:185-195`) - 接收消息:

```python
# _agent_base.py:185-195
async def observe(self, msg: Msg | list[Msg] | None) -> None:
    """Receive the given message(s) without generating a reply.

    Args:
        msg (`Msg | list[Msg] | None`):
            The message(s) to be observed.
    """
    raise NotImplementedError(
        f"The observe function is not implemented in"
        f" {self.__class__.__name__} class.",
    )
```

**流程说明**: observe() 是消息接收方法,用于订阅发布机制。当智能体作为订阅者时,会通过 observe() 接收广播消息,但不产生回复。

### 3.4 __call__() 调用入口源码

**文件**: `_agent_base.py:448-467`

```python
# _agent_base.py:448-467
async def __call__(self, *args: Any, **kwargs: Any) -> Msg:
    """Call the reply function with the given arguments."""
    self._reply_id = shortuuid.uuid()  # 生成唯一回复ID,用于追踪

    reply_msg: Msg | None = None
    try:
        # 记录当前回复任务,支持中断机制
        self._reply_task = asyncio.current_task()
        reply_msg = await self.reply(*args, **kwargs)  # 调用核心回复逻辑

    # asyncio.CancelledError 在 interrupt() 被调用时抛出
    except asyncio.CancelledError:
        reply_msg = await self.handle_interrupt(*args, **kwargs)

    finally:
        # 广播消息给所有订阅者
        if reply_msg:
            await self._broadcast_to_subscribers(reply_msg)
        self._reply_task = None  # 清除当前任务引用

    return reply_msg
```

**流程说明**:
```
__call__() 执行流程:
1. 生成唯一 _reply_id
2. 获取当前 asyncio.Task 引用存入 _reply_task
3. 调用 reply() 执行核心逻辑
   - 正常完成 → 返回回复消息
   - 被取消 → 抛出 CancelledError → 调用 handle_interrupt()
4. finally: 广播消息给订阅者,清空 _reply_task
```

### 3.5 消息打印机制 print() 源码详解

**print() 方法** (`_agent_base.py:205-274`):

```python
# _agent_base.py:205-274
async def print(
    self,
    msg: Msg,
    last: bool = True,
    speech: AudioBlock | list[AudioBlock] | None = None,
) -> None:
    """The function to display the message.

    Args:
        msg: The message object to be printed.
        last: Whether this is the last one in streaming messages.
        speech: The audio content block(s) to be played along.
    """
    # 1. 消息队列模式 - 用于异步流式输出
    if not self._disable_msg_queue:
        await self.msg_queue.put((deepcopy(msg), last, speech))
        await asyncio.sleep(0)  # 让出控制权给消费者协程

    # 2. 跳过控制台输出(可通过环境变量禁用)
    if self._disable_console_output:
        return

    # 3. 收集待打印的文本和思考块
    thinking_and_text_to_print = []

    for block in msg.get_content_blocks():
        if block["type"] == "text":
            self._print_text_block(
                msg.id,
                name_prefix=msg.name,
                text_content=block["text"],
                thinking_and_text_to_print=thinking_and_text_to_print,
            )
        elif block["type"] == "thinking":
            self._print_text_block(
                msg.id,
                name_prefix=f"{msg.name}(thinking)",
                text_content=block["thinking"],
                thinking_and_text_to_print=thinking_and_text_to_print,
            )
        elif last:
            # 4. 处理工具调用块、结果块等
            self._print_last_block(block, msg)

    # 5. 处理音频块 - TTS模型生成的音频
    if isinstance(speech, list):
        for audio_block in speech:
            self._process_audio_block(msg.id, audio_block)
    elif isinstance(speech, dict):
        self._process_audio_block(msg.id, speech)

    # 6. 流结束时清理资源
    if last and msg.id in self._stream_prefix:
        if "audio" in self._stream_prefix[msg.id]:
            player, _ = self._stream_prefix[msg.id]["audio"]
            player.close()  # 关闭音频播放器
        stream_prefix = self._stream_prefix.pop(msg.id)
        # 如果文本未以换行结束,则打印换行
        if "text" in stream_prefix and not stream_prefix["text"].endswith("\n"):
            print()
```

**_print_text_block()** (`_agent_base.py:369-407`) - 增量打印实现:

```python
# _agent_base.py:369-407
def _print_text_block(
    self,
    msg_id: str,
    name_prefix: str,
    text_content: str,
    thinking_and_text_to_print: list[str],
) -> None:
    """Print the text block with streaming support - only prints new content."""
    thinking_and_text_to_print.append(f"{name_prefix}: {text_content}")
    to_print = "\n".join(thinking_and_text_to_print)

    # 初始化消息的前缀缓存
    if msg_id not in self._stream_prefix:
        self._stream_prefix[msg_id] = {}

    text_prefix = self._stream_prefix[msg_id].get("text", "")

    # 增量打印: 只打印新增的字符
    if len(to_print) > len(text_prefix):
        print(to_print[len(text_prefix):], end="")
        self._stream_prefix[msg_id]["text"] = to_print
```

**_process_audio_block()** (`_agent_base.py:276-367`) - 音频处理:

```python
# _agent_base.py:276-367
def _process_audio_block(self, msg_id: str, audio_block: AudioBlock) -> None:
    """Process audio block from URL or base64 data."""
    if audio_block["source"]["type"] == "url":
        # 从URL下载并播放音频
        import wave, sounddevice as sd
        with urllib.request.urlopen(url) as response:
            audio_data = response.read()
        with wave.open(io.BytesIO(audio_data), "rb") as wf:
            audio_np = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            sd.play(audio_np, wf.getframerate())
            sd.wait()
    elif audio_block["source"]["type"] == "base64":
        # 流式音频播放 - 使用 sounddevice OutputStream
        if msg_id not in self._stream_prefix:
            self._stream_prefix[msg_id] = {}
        # ... 流式播放逻辑,缓存 player 和已播放数据
```

### 3.6 订阅发布机制源码

**_broadcast_to_subscribers()** (`_agent_base.py:469-485`):

```python
# _agent_base.py:469-485
async def _broadcast_to_subscribers(
    self,
    msg: Msg | list[Msg] | None,
) -> None:
    """Broadcast the message to all subscribers.

    Thinking blocks are stripped before broadcasting, since they represent
    the agent's internal reasoning and should not be visible to others.
    """
    if msg is None:
        return

    # 广播前去除思考块 - 思考是智能体内部推理,不应暴露
    broadcast_msg = self._strip_thinking_blocks(msg)

    # 遍历所有MsgHub的订阅者列表
    for subscribers in self._subscribers.values():
        for subscriber in subscribers:
            await subscriber.observe(broadcast_msg)  # 调用订阅者的observe()
```

**_strip_thinking_blocks()** (`_agent_base.py:487-514`) - 思考块剥离:

```python
# _agent_base.py:487-514
@staticmethod
def _strip_thinking_blocks(msg: Msg | list[Msg]) -> Msg | list[Msg]:
    """Remove thinking blocks from message(s) before sharing with other agents."""
    if isinstance(msg, list):
        return [AgentBase._strip_thinking_blocks_single(m) for m in msg]
    return AgentBase._strip_thinking_blocks_single(msg)

@staticmethod
def _strip_thinking_blocks_single(msg: Msg) -> Msg:
    """Remove thinking blocks from a single message."""
    if not isinstance(msg.content, list):
        return msg

    # 过滤掉 type=="thinking" 的块
    filtered = [b for b in msg.content if b.get("type") != "thinking"]
    if len(filtered) == len(msg.content):
        return msg  # 没有思考块,直接返回原消息

    # 创建新消息对象,保留原ID和时间戳
    new_msg = Msg(
        name=msg.name,
        content=filtered,
        role=msg.role,
        metadata=msg.metadata,
        timestamp=msg.timestamp,
        invocation_id=msg.invocation_id,
    )
    new_msg.id = msg.id
    return new_msg
```

### 3.7 中断处理机制源码

**interrupt() 方法** (`_agent_base.py:528-531`):

```python
# _agent_base.py:528-531
async def interrupt(self, msg: Msg | list[Msg] | None = None) -> None:
    """Interrupt the current reply process."""
    # 检查任务是否存在且未完成,然后取消
    if self._reply_task and not self._reply_task.done():
        self._reply_task.cancel(msg)  # 传入的消息会被携带到 CancelledError
```

**handle_interrupt() 抽象方法** (`_agent_base.py:516-526`):

```python
# _agent_base.py:516-526
async def handle_interrupt(
    self,
    *args: Any,
    **kwargs: Any,
) -> Msg:
    """The post-processing logic when the reply is interrupted by the
    user or something else."""
    raise NotImplementedError(
        f"The handle_interrupt function is not implemented in "
        f"{self.__class__.__name__}",
    )
```

**中断处理流程**:
```
用户调用 interrupt(msg)
    │
    ▼
_reply_task.cancel(msg) 发送 CancelledError
    │
    ▼
__call__() 捕获 CancelledError
    │
    ▼
调用 handle_interrupt() 子类实现
    │
    ▼
返回中断响应消息
```

**ReActAgent 的 handle_interrupt 实现** (`_react_agent.py:799-827`):

```python
# _react_agent.py:799-827
async def handle_interrupt(
    self,
    msg: Msg | list[Msg] | None = None,
    structured_model: Type[BaseModel] | None = None,
) -> Msg:
    """The post-processing logic when the reply is interrupted."""
    response_msg = Msg(
        self.name,
        "I noticed that you have interrupted me. What can I "
        "do for you?",
        "assistant",
        metadata={
            "_is_interrupted": True,  # 标记中断状态
        },
    )
    await self.print(response_msg, True)
    await self.memory.add(response_msg)
    return response_msg
```

### 3.8 Hook 注册机制源码

**register_class_hook() 类方法** (`_agent_base.py:590-616`):

```python
# _agent_base.py:590-616
@classmethod
def register_class_hook(
    cls,
    hook_type: AgentHookTypes,
    hook_name: str,
    hook: Callable,
) -> None:
    """The universal function to register a hook to the agent class,
    which will take effect for all instances of the class."""
    assert hook_type in cls.supported_hook_types  # 验证钩子类型
    # 从类属性获取钩子字典并注册
    hooks = getattr(cls, f"_class_{hook_type}_hooks")
    hooks[hook_name] = hook
```

**register_instance_hook() 实例方法** (`_agent_base.py:533-559`):

```python
# _agent_base.py:533-559
def register_instance_hook(
    self,
    hook_type: AgentHookTypes,
    hook_name: str,
    hook: Callable,
) -> None:
    """Register a hook to the agent instance, which only takes effect
    for the current instance."""
    hooks = getattr(self, f"_instance_{hook_type}_hooks")
    hooks[hook_name] = hook
```

**remove_instance_hook()** (`_agent_base.py:561-588`) - 移除钩子:

```python
# _agent_base.py:561-588
def remove_instance_hook(
    self,
    hook_type: AgentHookTypes,
    hook_name: str,
) -> None:
    """Remove an instance-level hook from the agent instance."""
    hooks = getattr(self, f"_instance_{hook_type}_hooks")
    if hook_name in hooks:
        del hooks[hook_name]
    else:
        raise ValueError(f"Hook '{hook_name}' not found...")
```

**clear_class_hooks() / clear_instance_hooks()** (`_agent_base.py:647-699`):

```python
# _agent_base.py:647-669
@classmethod
def clear_class_hooks(cls, hook_type: AgentHookTypes | None = None) -> None:
    """Clear all class-level hooks."""
    if hook_type is None:
        for typ in cls.supported_hook_types:
            hooks = getattr(cls, f"_class_{typ}_hooks")
            hooks.clear()
    else:
        hooks = getattr(cls, f"_class_{hook_type}_hooks")
        hooks.clear()

# _agent_base.py:671-699
def clear_instance_hooks(
    self,
    hook_type: AgentHookTypes | None = None,
) -> None:
    """Clear all instance-level hooks."""
    if hook_type is None:
        for typ in self.supported_hook_types:
            hooks = getattr(self, f"_instance_{typ}_hooks")
            hooks.clear()
    else:
        hooks = getattr(self, f"_instance_{hook_type}_hooks")
        hooks.clear()
```

### 3.9 状态管理机制

AgentBase 继承自 `StateModule`,提供状态序列化和恢复能力。关键状态变量通过 `register_state()` 注册:

```python
# _react_agent.py:363-364 - ReActAgent 中的状态注册
self.register_state("name")
self.register_state("_sys_prompt")
```

**reset_subscribers()** (`_agent_base.py:701-715`):

```python
# _agent_base.py:701-715
def reset_subscribers(
    self,
    msghub_name: str,
    subscribers: list["AgentBase"],
) -> None:
    """Reset the subscribers of the agent for a specific MsgHub."""
    # 排除自身,防止智能体给自己发消息
    self._subscribers[msghub_name] = [_ for _ in subscribers if _ != self]
```

### 3.10 控制台输出管理

**set_console_output_enabled()** (`_agent_base.py:738-748`):

```python
# _agent_base.py:738-748
def set_console_output_enabled(self, enabled: bool) -> None:
    """Enable or disable the console output of the agent."""
    self._disable_console_output = not enabled
```

**set_msg_queue_enabled()** (`_agent_base.py:750-774`):

```python
# _agent_base.py:750-774
def set_msg_queue_enabled(self, enabled: bool, queue: Queue | None = None) -> None:
    """Enable or disable the message queue for streaming outputs."""
    if enabled:
        self.msg_queue = queue if queue else asyncio.Queue(maxsize=100)
    else:
        self.msg_queue = None
    self._disable_msg_queue = not enabled
```

---

## 4. ReActAgent 实现类分析

### 4.1 类定义

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/agent/_react_agent.py:98`

```python
class ReActAgent(ReActAgentBase):
    """A ReAct agent implementation in AgentScope, which supports

    - Realtime steering
    - API-based (parallel) tool calling
    - Hooks around reasoning, acting, reply, observe and print functions
    - Structured output generation
    """
```

### 4.2 初始化参数

**__init__() 方法** (第 177-262 行):

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 代理名称 |
| `sys_prompt` | `str` | 系统提示词 |
| `model` | `ChatModelBase` | 聊天模型 |
| `formatter` | `FormatterBase` | 消息格式化器 |
| `toolkit` | `Toolkit` | 工具包 |
| `memory` | `MemoryBase` | 工作记忆（默认为 `InMemoryMemory`，用于存储对话历史） |
| `long_term_memory` | `LongTermMemoryBase` | 长期记忆 |
| `parallel_tool_calls` | `bool` | 是否并行调用工具 |
| `knowledge` | `KnowledgeBase` | 知识库 |
| `plan_notebook` | `PlanNotebook` | 计划笔记本 |
| `max_iters` | `int` | 最大迭代次数 |
| `tts_model` | `TTSModelBase` | TTS 模型 |
| `compression_config` | `CompressionConfig` | 记忆压缩配置 |

### 4.3 核心 reply() 方法流程

**文件**: `_react_agent.py:376-537`

```
reply() 主流程:

1. 接收消息并添加到记忆
   │
   ▼
2. 从长期记忆检索（如已配置）
   │
   ▼
3. 从知识库检索（如已配置）
   │
   ▼
4. 进入 ReAct 推理循环 (max_iters 次)
   │
   ├── 4.1 记忆压缩检查
   │       │
   │       ▼
   ├── 4.2 推理阶段 (_reasoning)
   │       │
   │       ▼
   ├── 4.3 行动阶段 (_acting)
   │       │
   │       ▼
   └── 4.4 检查退出条件
           ├── 无需结构化输出 + 只有文本 → break
           ├── 结构化输出完成 → break
           └── 达到最大迭代 → _summarizing()
   │
   ▼
5. 记录到长期记忆（如为 static_control 模式）
   │
   ▼
6. 返回回复消息
```

**reply() 方法完整源码** (`_react_agent.py:376-537`):

```python
# _react_agent.py:376-537
@trace_reply  # 追踪装饰器
async def reply(
    self,
    msg: Msg | list[Msg] | None = None,
    structured_model: Type[BaseModel] | None = None,
) -> Msg:
    """Generate a reply based on the current state and input arguments."""

    # 1. 记录输入消息到记忆
    await self.memory.add(msg)

    # 2. 从长期记忆检索
    await self._retrieve_from_long_term_memory(msg)

    # 3. 从知识库检索
    await self._retrieve_from_knowledge(msg)

    # 4. 结构化输出管理
    self._required_structured_model = structured_model
    tool_choice: Literal["auto", "none", "required"] | None = None

    if structured_model:
        # 注册 generate_response 工具
        if self.finish_function_name not in self.toolkit.tools:
            self.toolkit.register_tool_function(
                getattr(self, self.finish_function_name),
            )
        self.toolkit.set_extended_model(self.finish_function_name, structured_model)
        tool_choice = "required"  # 强制调用工具
    else:
        self.toolkit.remove_tool_function(self.finish_function_name)

    # 5. ReAct 推理循环
    structured_output = None
    reply_msg = None

    for _ in range(self.max_iters):
        # 5.1 记忆压缩检查
        await self._compress_memory_if_needed()

        # 5.2 推理阶段
        msg_reasoning = await self._reasoning(tool_choice)

        # 5.3 行动阶段 - 支持并行/串行工具调用
        futures = [
            self._acting(tool_call)
            for tool_call in msg_reasoning.get_content_blocks("tool_use")
        ]
        if self.parallel_tool_calls:
            structured_outputs = await asyncio.gather(*futures)
        else:
            structured_outputs = [await _ for _ in futures]

        # 5.4 检查退出条件
        if self._required_structured_model:
            structured_outputs = [_ for _ in structured_outputs if _]
            if structured_outputs:
                structured_output = structured_outputs[-1]
                if msg_reasoning.has_content_blocks("text"):
                    reply_msg = Msg(self.name,
                        msg_reasoning.get_content_blocks("text"),
                        "assistant",
                        metadata=structured_output)
                    break
                # 需要生成文本响应
                tool_choice = "none"
                self._required_structured_model = None
            elif not msg_reasoning.has_content_blocks("tool_use"):
                tool_choice = "required"
        elif not msg_reasoning.has_content_blocks("tool_use"):
            msg_reasoning.metadata = structured_output
            reply_msg = msg_reasoning
            break

    # 6. 超时处理
    if reply_msg is None:
        reply_msg = await self._summarizing()
        reply_msg.metadata = structured_output
        await self.memory.add(reply_msg)

    # 7. 记录到长期记忆
    if self._static_control:
        await self.long_term_memory.record([
            *await self.memory.get_memory(exclude_mark=_MemoryMark.COMPRESSED),
        ])

    return reply_msg
```

### 4.4 推理阶段 _reasoning() 源码详解

**_reasoning() 方法** (`_react_agent.py:540-655`):

```python
# _react_agent.py:540-655
async def _reasoning(
    self,
    tool_choice: Literal["auto", "none", "required"] | None = None,
) -> Msg:
    """Perform the reasoning process."""

    # 1. 获取计划提示(PlanNotebook)
    if self.plan_notebook:
        hint_msg = await self.plan_notebook.get_current_hint()
        if self.print_hint_msg and hint_msg:
            await self.print(hint_msg)
        await self.memory.add(hint_msg, marks=_MemoryMark.HINT)

    # 2. 格式化消息为模型输入
    prompt = await self.formatter.format(
        msgs=[
            Msg("system", self.sys_prompt, "system"),
            *await self.memory.get_memory(
                exclude_mark=_MemoryMark.HINT
                if self.compression_config and self.compression_config.enable
                else None,
            ),
        ],
    )
    # 格式化后清除提示消息标记
    await self.memory.delete_by_mark(mark=_MemoryMark.HINT)

    # 3. 调用模型获取响应
    res = await self.model(
        prompt,
        tools=self.toolkit.get_json_schemas(),
        tool_choice=tool_choice,
    )

    # 4. 处理模型响应 - 支持流式/非流式
    interrupted_by_user = False
    msg = None
    tts_context = self.tts_model or _AsyncNullContext()
    speech: AudioBlock | list[AudioBlock] | None = None

    try:
        async with tts_context:
            msg = Msg(name=self.name, content=[], role="assistant")

            if self.model.stream:
                # 4.1 流式响应处理
                async for content_chunk in res:
                    msg.invocation_id = content_chunk.id
                    msg.content = content_chunk.content

                    # 获取音频块(多模态模型)
                    speech = msg.get_content_blocks("audio") or None

                    # TTS模型流式输入支持
                    if self.tts_model and self.tts_model.supports_streaming_input:
                        tts_res = await self.tts_model.push(msg)
                        speech = tts_res.content

                    await self.print(msg, False, speech=speech)  # 流式打印

            else:
                # 4.2 非流式响应
                msg.invocation_id = res.id
                msg.content = list(res.content)

            # 4.3 TTS模型完整合成
            if self.tts_model:
                tts_res = await self.tts_model.synthesize(msg)
                if self.tts_model.stream:
                    async for tts_chunk in tts_res:
                        speech = tts_chunk.content
                        await self.print(msg, False, speech=speech)
                else:
                    speech = tts_res.content

            await self.print(msg, True, speech=speech)  # 最终打印
            await asyncio.sleep(0.001)  # 让出控制权

    except asyncio.CancelledError as e:
        interrupted_by_user = True
        raise e from None

    finally:
        # 5. 记录到记忆
        await self.memory.add(msg)

        # 6. 用户中断后处理 - 生成假的工具结果
        if interrupted_by_user and msg:
            tool_use_blocks = msg.get_content_blocks("tool_use")
            for tool_call in tool_use_blocks:
                msg_res = Msg(
                    "system",
                    [ToolResultBlock(
                        type="tool_result",
                        id=tool_call["id"],
                        name=tool_call["name"],
                        output="The tool call has been interrupted by the user.",
                    )],
                    "system",
                )
                await self.memory.add(msg_res)
                await self.print(msg_res, True)

    return msg
```

### 4.5 行动阶段 _acting() 源码详解

**_acting() 方法** (`_react_agent.py:657-714`):

```python
# _react_agent.py:657-714
async def _acting(self, tool_call: ToolUseBlock) -> dict | None:
    """Perform the acting process, and return the structured output if
    it's generated and verified in the finish function call.

    Args:
        tool_call: The tool use block to be executed.

    Returns:
        The structured output if generated and verified, otherwise None.
    """

    # 1. 创建工具结果消息模板
    tool_res_msg = Msg(
        "system",
        [
            ToolResultBlock(
                type="tool_result",
                id=tool_call["id"],
                name=tool_call["name"],
                output=[],  # 初始为空,流式更新
            ),
        ],
        "system",
    )

    try:
        # 2. 执行工具调用 - 返回异步生成器
        tool_res = await self.toolkit.call_tool_function(tool_call)

        # 3. 处理流式工具响应
        async for chunk in tool_res:
            # 3.1 更新工具结果内容
            tool_res_msg.content[0]["output"] = chunk.content

            # 3.2 流式打印工具结果
            await self.print(tool_res_msg, chunk.is_last)

            # 3.3 检查用户中断
            if chunk.is_interrupted:
                raise asyncio.CancelledError()

            # 3.4 检查结构化输出完成 - generate_response 工具调用成功
            if (
                tool_call["name"] == self.finish_function_name
                and chunk.metadata
                and chunk.metadata.get("success", False)
            ):
                # 返回验证通过的结构化输出
                return chunk.metadata.get("structured_output")

        return None  # 正常完成但无结构化输出

    finally:
        # 4. 无论成功/失败/中断,都要记录工具结果到记忆
        await self.memory.add(tool_res_msg)
```

### 4.6 记忆压缩配置

**CompressionConfig** (第 107-172 行):

```python
class CompressionConfig(BaseModel):
    """The compression related configuration in AgentScope"""

    enable: bool                              # 是否启用压缩
    agent_token_counter: TokenCounterBase     # Token 计数器
    trigger_threshold: int                    # 触发阈值
    keep_recent: int = 3                     # 保留最近消息数
    compression_prompt: str                   # 压缩提示词
    summary_template: str                      # 摘要模板
    summary_schema: Type[BaseModel] = SummarySchema  # 摘要模式
    compression_model: ChatModelBase | None    # 压缩模型
    compression_formatter: FormatterBase | None  # 压缩格式化器
```

---

## 5. UserAgent 用户代理分析

### 5.1 类定义

**文件**: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/agent/_user_agent.py:12`

```python
class UserAgent(AgentBase):
    """The class for user interaction, allowing developers to handle the user
    input from different sources, such as web UI, cli, and other interfaces.
    """

    _input_method: UserInputBase = TerminalUserInput()
```

### 5.2 reply() 实现

**文件**: `_user_agent.py:30-76`

```python
async def reply(
    self,
    msg: Msg | list[Msg] | None = None,
    structured_model: Type[BaseModel] | None = None,
) -> Msg:
    """Receive input message(s) and generate a reply message from the user."""

    # 1. 从输入方法获取用户输入
    input_data = await self._input_method(
        agent_id=self.id,
        agent_name=self.name,
        structured_model=structured_model,
    )

    # 2. 处理输入块
    blocks_input = input_data.blocks_input
    if (
        blocks_input
        and len(blocks_input) == 1
        and blocks_input[0].get("type") == "text"
    ):
        blocks_input = blocks_input[0].get("text")

    # 3. 创建消息
    msg = Msg(
        self.name,
        content=blocks_input,
        role="user",
        metadata=input_data.structured_input,
    )

    await self.print(msg)
    return msg
```

### 5.3 输入方法覆盖

```python
# 实例级覆盖
agent.override_instance_input_method(custom_input_method)

# 类级覆盖
UserAgent.override_class_input_method(custom_input_method)
```

---

## 6. Hook 机制分析

### 6.1 Hook 类型总览

**AgentBase 支持的 Hook 类型** (`_agent_base.py:36-43`):

| Hook 类型 | 触发时机 | 参数 | 返回值 |
|-----------|----------|------|--------|
| `pre_reply` | 调用 reply() 之前 | `self`, `kwargs` | 修改后的 kwargs |
| `post_reply` | 调用 reply() 之后 | `self`, `kwargs`, `output` | 修改后的 output |
| `pre_print` | 调用 print() 之前 | `self`, `kwargs` | 修改后的 kwargs |
| `post_print` | 调用 print() 之后 | `self`, `kwargs`, `output` | 无 |
| `pre_observe` | 调用 observe() 之前 | `self`, `kwargs` | 修改后的 kwargs |
| `post_observe` | 调用 observe() 之后 | `self`, `kwargs`, `None` | 无 |

**ReActAgentBase 额外 Hook 类型** (`_react_agent_base.py:21-32`):

| Hook 类型 | 触发时机 |
|-----------|----------|
| `pre_reasoning` | 推理阶段之前 |
| `post_reasoning` | 推理阶段之后 |
| `pre_acting` | 行动阶段之前 |
| `post_acting` | 行动阶段之后 |

### 6.2 元类 Hook 包装机制

Hook 机制的核心实现位于 `_agent_meta.py`,通过元类在类创建时自动包装方法。

> **交叉引用**: Hook 机制的中间件实现在 Tool 模块的 `Toolkit` 类中，详见 `module_tool_mcp_deep.md` 的「中间件机制」章节。

**元类架构**:

```python
# _agent_meta.py:159-192
class _AgentMeta(type):
    """The agent metaclass that wraps the agent's reply, observe and print
    functions with pre- and post-hooks."""

    def __new__(mcs, name: Any, bases: Any, attrs: Dict) -> Any:
        """Wrap the agent's functions with hooks."""
        for func_name in ["reply", "print", "observe"]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])
        return super().__new__(mcs, name, bases, attrs)


class _ReActAgentMeta(_AgentMeta):
    """The ReAct metaclass that adds pre- and post-hooks for _reasoning
    and _acting functions."""

    def __new__(mcs, name: Any, bases: Any, attrs: Dict) -> Any:
        """Wrap the ReAct agent's _reasoning and _acting functions."""
        for func_name in ["_reasoning", "_acting"]:
            if func_name in attrs:
                attrs[func_name] = _wrap_with_hooks(attrs[func_name])
        return super().__new__(mcs, name, bases, attrs)
```

**_wrap_with_hooks() 装饰器** (`_agent_meta.py:55-156`):

```python
# _agent_meta.py:55-156
def _wrap_with_hooks(original_func: Callable) -> Callable:
    """A decorator to wrap the original async function with pre- and post-hooks.

    This decorator normalizes positional/keyword arguments and executes hooks
    in sequence: pre_hooks -> original_func -> post_hooks.
    """
    func_name = original_func.__name__.replace("_", "")
    hook_guard_attr = f"_hook_running_{func_name}"  # 防止重复执行

    @wraps(original_func)
    async def async_wrapper(self: AgentBase, *args: Any, **kwargs: Any) -> Any:
        """The wrapped function with hook execution."""

        # 防止在MRO中多重继承时重复执行钩子
        if getattr(self, hook_guard_attr, False):
            return await original_func(self, *args, **kwargs)

        # 1. 参数归一化 - 将位置参数和关键字参数统一为字典
        normalized_kwargs = _normalize_to_kwargs(original_func, self, *args, **kwargs)

        current_normalized_kwargs = normalized_kwargs

        # 2. 执行 pre_hooks (实例级 + 类级)
        pre_hooks = (
            list(getattr(self, f"_instance_pre_{func_name}_hooks").values()) +
            list(getattr(self.__class__, f"_class_pre_{func_name}_hooks").values())
        )
        for pre_hook in pre_hooks:
            modified_keywords = await _execute_async_or_sync_func(
                pre_hook, self, deepcopy(current_normalized_kwargs)
            )
            if modified_keywords is not None:
                current_normalized_kwargs = modified_keywords

        # 3. 执行原始函数
        args = current_normalized_kwargs.get("args", [])
        kwargs = current_normalized_kwargs.get("kwargs", {})
        others = {k: v for k, v in current_normalized_kwargs.items()
                  if k not in ["args", "kwargs"]}

        setattr(self, hook_guard_attr, True)
        try:
            current_output = await original_func(self, *args, **others, **kwargs)
        finally:
            setattr(self, hook_guard_attr, False)

        # 4. 执行 post_hooks (实例级 + 类级)
        post_hooks = (
            list(getattr(self, f"_instance_post_{func_name}_hooks").values()) +
            list(getattr(self.__class__, f"_class_post_{func_name}_hooks").values())
        )
        for post_hook in post_hooks:
            modified_output = await _execute_async_or_sync_func(
                post_hook, self, deepcopy(current_normalized_kwargs), deepcopy(current_output)
            )
            if modified_output is not None:
                current_output = modified_output

        return current_output

    return async_wrapper
```

**_normalize_to_kwargs() 参数归一化** (`_agent_meta.py:21-52`):

```python
# _agent_meta.py:21-52
def _normalize_to_kwargs(func: Callable, self: Any, *args: Any, **kwargs: Any) -> dict:
    """Normalize positional and keyword arguments into a kwargs dict."""
    sig = inspect.signature(func)
    try:
        bound = sig.bind(self, *args, **kwargs)
        bound.apply_defaults()
        res = dict(bound.arguments)
        res.pop("self")  # 移除self,只返回参数
        return res
    except TypeError as e:
        raise TypeError(
            f"Failed to bind parameters for '{func.__name__}': {e}\n"
            f"Expected: {list(sig.parameters.keys())}\n"
            f"Provided: {len(args)} positional, kwargs: {list(kwargs.keys())}"
        ) from e
```

### 6.3 Hook 执行流程图

```
Agent.__call__()
    │
    ▼
reply() 被元类包装后:
    │
    ├── pre_reply hooks
    │   ├── 实例级 hooks
    │   └── 类级 hooks
    │
    ▼
原始 reply() 执行
    │
    ├── (如为ReActAgent)
    │   │
    │   ├── pre_reasoning hooks
    │   ├── _reasoning() 执行
    │   ├── post_reasoning hooks
    │   │
    │   ├── pre_acting hooks
    │   ├── _acting() 执行
    │   └── post_acting hooks
    │
    ▼
post_reply hooks
    │
    ├── 实例级 hooks
    └── 类级 hooks
```

### 6.4 Hook 注册示例

**Studio Hook 注册** (`hooks/__init__.py:17-29`):

```python
# hooks/__init__.py:17-29
def _equip_as_studio_hooks(studio_url: str) -> None:
    """Connect to the agentscope studio."""
    AgentBase.register_class_hook(
        "pre_print",
        "as_studio_forward_message_pre_print_hook",
        partial(
            as_studio_forward_message_pre_print_hook,
            studio_url=studio_url,
            run_id=_config.run_id,
        ),
    )
```

### 6.5 as_studio_forward_message_pre_print_hook 实现

**文件**: `hooks/_studio_hooks.py:12-58`

```python
# hooks/_studio_hooks.py:12-58
def as_studio_forward_message_pre_print_hook(
    self: AgentBase,
    kwargs: dict[str, Any],
    studio_url: str,
    run_id: str,
) -> None:
    """The pre-speak hook to forward messages to the studio.

    This hook is called before print() to forward the message to AgentScope Studio
    for visualization and monitoring.
    """
    msg = kwargs["msg"]
    message_data = msg.to_dict()

    # 获取回复ID用于追踪
    if hasattr(self, "_reply_id"):
        reply_id = getattr(self, "_reply_id")
    else:
        reply_id = shortuuid.uuid()

    # 重试机制 - 最多3次
    n_retry = 0
    while True:
        try:
            res = requests.post(
                f"{studio_url}/trpc/pushMessage",
                json={
                    "runId": run_id,
                    "replyId": reply_id,
                    "replyName": getattr(self, "name", msg.name),
                    "replyRole": "user" if isinstance(self, UserAgent) else "assistant",
                    "msg": message_data,
                },
            )
            res.raise_for_status()
            break
        except Exception as e:
            if n_retry < 3:
                n_retry += 1
                continue
            # 优雅降级: 记录警告但不让智能体崩溃
            logger.warning(
                "Failed to forward message to Studio after %d retries: %s. "
                "Agent will continue without Studio forwarding.",
                n_retry,
                e,
            )
            return
```

### 6.6 自定义 Hook 示例

```python
from functools import partial

# 自定义日志Hook - 记录回复内容
def my_logging_hook(self, kwargs, **other):
    print(f"[LOG] Agent {self.name} is replying...")
    return None  # 不修改参数

# 注册类级Hook
AgentBase.register_class_hook("pre_reply", "my_logger", my_logging_hook)

# 实例级Hook - 仅对特定实例生效
def my_instance_hook(self, kwargs):
    print(f"[LOG] Instance {self.name} is processing...")
    return kwargs

agent.register_instance_hook("pre_reply", "instance_hook", my_instance_hook)

# 带参数的Hook使用partial
def parameterized_hook(self, kwargs, prefix=""):
    print(f"[{prefix}] Agent {self.name} reply started")
    return kwargs

agent.register_instance_hook(
    "pre_reply",
    "param_hook",
    partial(parameterized_hook, prefix="DEBUG")
)
```

---

## 7. 设计模式总结

### 7.1 模板方法模式

**应用场景**: 智能体的 reply() 方法定义了算法框架，具体推理和行动由子类实现。

```
AgentBase.reply()  [模板方法]
    │
    ├── pre_reply hooks
    │
    ├── _reasoning()  [抽象方法 - 子类实现]
    │
    ├── _acting()  [抽象方法 - 子类实现]
    │
    ├── post_reply hooks
    │
    └── 返回结果
```

### 7.2 策略模式

**应用场景**: Hook 机制允许在运行时替换/扩展行为。

```python
# 类级策略
AgentBase.register_class_hook("pre_reply", "custom_hook", my_hook_func)

# 实例级策略
agent.register_instance_hook("pre_reply", "custom_hook", my_hook_func)
```

### 7.3 观察者模式

**应用场景**: 订阅发布机制 (MsgHub)。

```python
# 智能体广播消息给订阅者
await self._broadcast_to_subscribers(reply_msg)

# 订阅者接收消息
await subscriber.observe(broadcast_msg)
```

### 7.4 装饰器模式

**应用场景**: Hook 装饰器用于扩展智能体行为。

```python
# ReActAgentBase 使用元类自动包装 _reasoning 和 _acting 方法
```

### 7.5 洋葱模型

**应用场景**: Toolkit 的中间件机制。

```
请求 → middleware1 → middleware2 → middleware3 → 核心处理 → middleware3 → middleware2 → middleware1 → 响应
```

### 7.6 与 Java Spring 框架对比

对于熟悉 Java Spring 的开发者，以下是 AgentScope 设计模式与 Spring 框架对应关系的说明：

#### Hook 机制 vs Spring Intercept（拦截器模式）

| AgentScope | Java Spring | 说明 |
|------------|-------------|------|
| `pre_reply` / `post_reply` | `HandlerInterceptor.preHandle()` / `postHandle()` | 请求处理前后拦截 |
| `register_class_hook()` | `WebMvcConfigurer.addInterceptors()` | 全局拦截器注册 |
| `register_instance_hook()` | `@Autowired` 特定 Bean | 实例级定制 |
| Hook guard (`_hook_running_*`) | synchronized 或 ReentrantLock | 防止重复执行 |

```java
// Java Spring 等效示例
@Component
public class AgentInterceptor implements HandlerInterceptor {
    @Override
    public boolean preHandle(HttpServletRequest request,
                             HttpServletResponse response,
                             Object handler) {
        // 类似 pre_reply hook
        return true;
    }

    @Override
    public void postHandle(HttpServletRequest request,
                           HttpServletResponse response,
                           Object handler,
                           ModelAndView modelAndView) {
        // 类似 post_reply hook
    }
}

@Configuration
public class WebConfig implements WebMvcConfigurer {
    @Override
    public void addInterceptors(InterceptorRegistry registry) {
        registry.addInterceptor(new AgentInterceptor())
                .addPathPatterns("/agent/**");
    }
}
```

#### 观察者模式 vs Spring ApplicationEvent

| AgentScope | Java Spring | 说明 |
|------------|-------------|------|
| `_broadcast_to_subscribers()` | `applicationEventPublisher.publishEvent()` | 发布事件 |
| `observe()` | `@EventListener` | 接收事件 |
| `_subscribers` | `ApplicationListener` 列表 | 订阅者集合 |

```java
// Java Spring 等效示例
@Component
public class AgentEventPublisher {
    @Autowired
    private ApplicationEventPublisher publisher;

    public void broadcast(Message msg) {
        publisher.publishEvent(new AgentMessageEvent(this, msg));
    }
}

@Component
public class SubscriberAgent {
    @EventListener
    public void observe(AgentMessageEvent event) {
        // 处理接收到的消息
    }
}
```

#### 模板方法 vs Spring WebMvcConfigurer

| AgentScope | Java Spring | 说明 |
|------------|-------------|------|
| `AgentBase.reply()` | `Controller` | 算法骨架定义 |
| `_reasoning()` / `_acting()` | 子类实现 | 抽象步骤 |
| 元类自动包装 | `@Override` 方法 | 扩展点 |

#### 策略模式 vs Spring @Qualifier

| AgentScope | Java Spring | 说明 |
|------------|-------------|------|
| 类级/实例级 Hook 注册 | `@Primary` + `@Qualifier` | 策略选择 |
| `clear_class_hooks()` | 重新配置 Bean | 策略切换 |

```java
// Java Spring 等效示例
@Configuration
public class AgentStrategyConfig {
    @Bean
    @Primary
    public AgentStrategy defaultStrategy() {
        return new DefaultAgentStrategy();
    }

    @Bean
    public AgentStrategy customStrategy() {
        return new CustomAgentStrategy();
    }
}

@Service
public class AgentService {
    @Autowired
    @Qualifier("customStrategy")
    private AgentStrategy strategy;
}
```

#### 元类包装 vs Spring AOP

AgentScope 的 `_wrap_with_hooks()` 元类机制类似于 Spring AOP 的 `@Aspect` 切面：

| AgentScope | Java Spring AOP | 说明 |
|------------|-----------------|------|
| `_AgentMeta` 元类 | `@Aspect` | 切面定义 |
| `_wrap_with_hooks()` | `@Around` | 环绕通知 |
| Hook guard | `ProceedingJoinPoint.proceed()` | 执行控制 |

```java
// Java Spring AOP 等效示例
@Aspect
@Component
public class AgentHookAspect {
    @Around("execution(* Agent+.reply(..))")
    public Object aroundReply(ProceedingJoinPoint pjp) throws Throwable {
        // pre_reply 类似逻辑
        Object result = pjp.proceed();
        // post_reply 类似逻辑
        return result;
    }
}
```

---

## 8. 代码示例

### 8.1 创建基础 Agent

```python
from agentscope import AgentBase, Msg

class MyAgent(AgentBase):
    def __init__(self, name: str):
        super().__init__()
        self.name = name

    async def reply(self, msg: Msg | None = None) -> Msg:
        response = Msg(
            name=self.name,
            content=f"Hello! I received: {msg.content if msg else 'nothing'}",
            role="assistant"
        )
        await self.print(response)
        return response

    async def observe(self, msg: Msg | list[Msg] | None) -> None:
        print(f"[{self.name}] Observed: {msg}")

    async def handle_interrupt(self, *args, **kwargs) -> Msg:
        return Msg(name=self.name, content="Interrupted!", role="assistant")
```

### 8.2 创建 ReActAgent

```python
from agentscope import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIFormatter
from agentscope.tool import Toolkit, execute_python_code

# 初始化模型和格式化器
model = OpenAIChatModel(model_name="gpt-4")
formatter = OpenAIFormatter()

# 创建工具包
toolkit = Toolkit()
toolkit.register_tool_function(execute_python_code)

# 创建 ReActAgent
agent = ReActAgent(
    name="assistant",
    sys_prompt="You are a helpful AI assistant.",
    model=model,
    formatter=formatter,
    toolkit=toolkit,
    max_iters=10,
)

# 运行
result = await agent(Msg(name="user", content="What is 2 + 2?", role="user"))
print(result.content)
```

### 8.3 使用 Hooks

```python
from functools import partial

# 自定义 pre_reply hook
def my_pre_reply_hook(self, kwargs):
    print(f"Agent {self.name} is about to reply...")
    return kwargs  # 返回修改后的 kwargs

# 注册 hook
AgentBase.register_class_hook("pre_reply", "my_hook", my_pre_reply_hook)

# 实例级 hook
def my_instance_hook(self, kwargs):
    print(f"Instance hook for {self.name}")
    return kwargs

agent.register_instance_hook("pre_reply", "instance_hook", my_instance_hook)
```

### 8.4 订阅发布模式

```python
# 创建订阅者
subscriber = MyAgent(name="subscriber")

# 订阅者注册到发布者
publisher.reset_subscribers("channel_1", [subscriber])

# 发布者广播消息
await publisher._broadcast_to_subscribers(message)
```

### 8.5 用户代理

```python
from agentscope import UserAgent

# 创建用户代理
user_agent = UserAgent(name="user")

# 运行，会等待用户输入
result = await user_agent()
print(f"User said: {result.content}")
```

---

## 本章关联

### 与其他模块的关系

| 关联模块 | 关联内容 | 参考位置 |
|----------|----------|----------|
| [Model 模块深度剖析](module_model_deep.md) | Agent 如何通过 `model` 参数调用 LLM 进行推理 | 第 3.2 节 `__call__()` 方法 |
| [Tool/MCP 模块深度剖析](module_tool_mcp_deep.md) | ReActAgent 如何通过 `toolkit` 调用工具，Hook 机制与 Toolkit 中间件的协作关系 | 第 4.4 节 `_acting()` 方法、第 6 章 Hook 机制 |
| [Memory/RAG 模块深度剖析](module_memory_rag_deep.md) | Agent 如何使用 `memory` 进行上下文管理，记忆压缩与 Token 计数的关联 | 第 3.3 节 `observe()` 方法、第 5.2 节记忆压缩 |
| [Pipeline/基础设施模块深度剖析](module_pipeline_infra_deep.md) | MsgHub 与 AgentBase `_broadcast_to_subscribers` 的协作关系，Pipeline 如何编排 Agent | 第 3.5 节订阅发布机制、第 2.1 节 MsgHub |
| [最佳实践参考](reference_best_practices.md) | Agent 设计模式（ReAct、Plan-and-Execute）、Prompt Engineering、安全性实践 | 设计模式章节 |

### 前置知识

- **异步编程**: 如不熟悉 `async`/`await`，建议先阅读 [Python async 教程](https://docs.python.org/3/library/asyncio.html)
- **装饰器**: 如不熟悉 Python 装饰器，建议先了解其工作原理
- **面向对象**: 需要理解类继承、抽象方法、`super()` 调用

### 后续学习建议

1. 完成本模块练习题后，建议继续学习 [Tool/MCP 模块](module_tool_mcp_deep.md)，深入理解工具调用机制
2. 如需构建多智能体系统，建议学习 [Pipeline 模块](module_pipeline_infra_deep.md) 的 MsgHub 和 Pipeline 编排
3. 如需优化智能体性能，建议参考 [最佳实践](reference_best_practices.md) 中的 Prompt Engineering 和 RAG 优化章节

---

## 9. 练习题

### 9.1 基础题

1. **简述 AgentBase 的主要职责是什么？**

2. **在 `_agent_base.py:140-183` 中，AgentBase 的 `__init__` 方法初始化了哪些关键属性？**

3. **请说明 `reply()` 和 `observe()` 方法的区别。**

### 9.2 进阶题

4. **分析 ReActAgent 的推理-行动循环流程，参考 `_react_agent.py:376-537`。**

5. **设计一个自定义 Hook，实现在每次智能体回复前记录日志。**

6. **参考 `_agent_base.py:528-531`，分析 `interrupt()` 方法如何实现中断机制。**

### 9.3 挑战题

7. **尝试实现一个简单的多代理协作系统，包含一个指挥官代理和一个执行者代理。**

8. **分析 AgentScope 的 Hook 机制与装饰器模式的异同。**

9. **设计一个记忆压缩策略，当对话历史超过一定长度时自动压缩。**

---

## 参考答案

### 9.1 基础题

**1. 简述 AgentBase 的主要职责是什么？**

AgentBase 是 AgentScope 所有智能体的抽象基类，其核心职责包括：

- **消息处理**: 通过 `reply()` 方法处理消息并生成响应（抽象方法，子类必须实现）
- **消息观察**: 通过 `observe()` 方法接收消息但不生成响应（抽象方法，子类必须实现）
- **统一入口**: `__call__()` 方法作为外部调用统一入口，协调 `observe()` → `reply()` → `print()` 的完整流程
- **订阅发布**: 维护 `_subscribers` 字典，支持多智能体间的消息自动广播
- **生命周期管理**: 支持 `interrupt()` 中断和 `handle_interrupt()` 中断恢复
- **Hook 扩展**: 支持在关键节点（pre_reply / post_reply 等）注入自定义逻辑
- **状态管理**: 继承自 `StateModule`，支持 `state_dict()` / `load_state_dict()` 序列化

**2. AgentBase 的 `__init__` 方法初始化了哪些关键属性？**

```python
# 核心属性
self.name: str                    # 智能体名称
self.sys_prompt: str | None       # 系统提示词
self.model: ChatModelWrapper      # 模型包装器
self.memory: MemoryBase | None    # 记忆模块
self.tools: Toolkit | None        # 工具包
self.max_iters: int | None        # 最大迭代次数

# Hook 相关
self.supported_hook_types: list   # 支持的钩子类型列表
self._instance_pre_reply_hooks    # 实例级 pre_reply 钩子
self._instance_post_reply_hooks   # 实例级 post_reply 钩子

# 状态相关
self._subscribers: dict           # 订阅者字典 {hub_name: [agent, ...]}
self._reply_task: asyncio.Task    # 当前回复任务（用于中断）
```

**3. 请说明 `reply()` 和 `observe()` 方法的区别。**

| 维度 | `reply()` | `observe()` |
|------|-----------|-------------|
| **职责** | 处理消息并生成响应 | 接收消息但不生成响应 |
| **返回值** | `Msg` 对象（响应消息） | `None` |
| **调用时机** | 外部通过 `__call__()` 触发 | 外部通过 `__call__()` 触发，或被其他 Agent 广播消息时 |
| **典型实现** | ReActAgent: 推理→行动→观察循环 | 通常将消息添加到 memory |
| **Hook 触发** | 触发 pre_reply / post_reply | 不触发 reply 相关 Hook |

关键区别：`reply()` 是"主动响应"，`observe()` 是"被动接收"。在 `__call__()` 中，先调用 `observe()` 将消息存入记忆，再调用 `reply()` 生成回复。

---

### 9.2 进阶题

**4. 分析 ReActAgent 的推理-行动循环流程。**

ReActAgent 的 `reply()` 方法实现了经典的 ReAct（Reasoning + Acting）范式：

```
用户输入 → _reasoning() → _acting() → 循环判断
                ↓              ↓
           LLM 推理生成    执行工具调用
           思考内容          或生成最终回复
                ↓              ↓
           提取 tool_use   创建 ToolResultBlock
           块               更新消息内容
                ↓              ↓
           是否需要行动? ──→ 是: 继续循环
                ↓
           否: 返回最终响应
```

**核心流程**（`_react_agent.py:376-537`）：

1. **初始化**: 准备系统提示词和对话历史
2. **_reasoning()**: 调用 LLM 生成思考内容，可能包含 `tool_use` 块
3. **解析工具调用**: 从响应中提取 `ToolUseBlock`，获取工具名称和参数
4. **_acting()**: 调用 `toolkit.call_tool_function()` 执行工具
5. **处理工具结果**: 将 `ToolResponse` 包装为 `ToolResultBlock`
6. **循环判断**: 如果工具执行后需要继续推理（如多步任务），则回到步骤 2
7. **终止条件**: 当响应不包含 `tool_use` 块，或达到 `max_iters` 时终止

**5. 设计一个自定义 Hook，实现在每次智能体回复前记录日志。**

```python
from agentscope import AgentBase, Msg
import logging

logger = logging.getLogger(__name__)

# 定义 Hook 函数
def log_pre_reply_hook(
    self: AgentBase,
    msg: Msg,
    **kwargs: Any,
) -> Msg:
    """在智能体回复前记录日志。"""
    logger.info(
        f"[Hook] Agent '{self.name}' 即将回复消息，"
        f"输入内容: {msg.content[:100]}..."
    )
    # 可选: 记录调用时间戳
    msg.metadata = msg.metadata or {}
    msg.metadata["hook_timestamp"] = datetime.now().isoformat()
    return msg

# 注册到类级别（所有实例生效）
AgentBase.register_pre_reply_hook(log_pre_reply_hook)

# 或注册到单个实例
agent = ReActAgent(name="助手", model=model)
agent.register_pre_reply_hook(log_pre_reply_hook)
```

**设计要点**：
- Hook 函数签名必须匹配 `(self, msg, **kwargs) -> Msg`
- 通过 `register_pre_reply_hook` 注册到类级别或实例级别
- 可以在 Hook 中修改 `msg` 对象（如添加元数据），修改后的 `msg` 会传递给后续流程

**6. 分析 `interrupt()` 方法如何实现中断机制。**

`interrupt()` 方法（`_agent_base.py:528-531`）的实现：

```python
async def interrupt(self) -> None:
    if self._reply_task is not None:
        self._reply_task.cancel()
```

**中断机制原理**：

1. **任务跟踪**: `__call__()` 在调用 `reply()` 前，将异步任务存入 `self._reply_task`
2. **取消信号**: `interrupt()` 调用 `self._reply_task.cancel()`，向事件循环发送取消信号
3. **异常捕获**: `reply()` 内部通过 `try/except asyncio.CancelledError` 捕获取消异常
4. **清理操作**: `finally` 块中执行清理，包括：
   - 将已处理的消息添加到记忆
   - 如果用户中断时存在未完成的 `tool_use` 块，生成假的 `tool_result` 块避免死锁
5. **优雅退出**: 中断后智能体状态保持一致，可接受新的消息

**关键设计**: 使用 `asyncio.Task.cancel()` 而非强制终止，确保资源清理和状态一致性。

---

### 9.3 挑战题

**7. 实现一个简单的多代理协作系统。**

```python
import asyncio
from agentscope import ReActAgent, Msg
from agentscope.model import OpenAIChatModel
from agentscope.pipeline import MsgHub

async def main():
    model = OpenAIChatModel(model_name="gpt-4o")

    # 指挥官代理: 负责分解任务并下达指令
    commander = ReActAgent(
        name="指挥官",
        model=model,
        sys_prompt="你是任务指挥官。将用户请求分解为具体步骤，"
                   "并指示执行者代理完成。",
    )

    # 执行者代理: 负责执行具体任务
    executor = ReActAgent(
        name="执行者",
        model=model,
        sys_prompt="你是任务执行者。根据指挥官的指示完成具体任务，"
                   "并报告执行结果。",
    )

    # 使用 MsgHub 实现消息自动广播
    async with MsgHub(participants=[commander, executor]) as hub:
        # 指挥官分解任务
        plan = await commander(
            Msg("user", "研究 Python 异步编程的最佳实践", "user")
        )
        # plan 会自动广播给 executor
        # executor 收到后会调用 observe() 将计划存入记忆

        # 执行者根据计划执行任务
        result = await executor(
            Msg("user", "请按照上述计划执行研究", "user")
        )
        print(f"研究结果: {result.content}")

asyncio.run(main())
```

**8. 分析 AgentScope 的 Hook 机制与装饰器模式的异同。**

**相似之处**：
- **行为增强**: 两者都用于在不修改原函数的情况下增强功能
- **链式执行**: Hook 链和装饰器链都可以层层包装，形成洋葱模型
- **开闭原则**: 都遵循"对扩展开放，对修改封闭"的设计原则

**不同之处**：

| 维度 | Hook 机制 | 装饰器模式 |
|------|-----------|-----------|
| **注册方式** | 运行时动态注册/卸载 | 定义时静态包装 |
| **作用范围** | 类级别或实例级别 | 通常作用于单个函数 |
| **参数传递** | 接收 `(self, msg, **kwargs)` | 接收被装饰函数的任意参数 |
| **执行顺序** | 按注册顺序执行 | 按嵌套顺序执行（由内到外） |
| **状态访问** | 可访问 `self`（Agent 实例） | 通常不直接访问实例状态 |
| **返回值** | 必须返回修改后的 `msg` | 可返回任意类型 |

**核心区别**: Hook 是 AgentScope 框架级别的扩展机制，与 Agent 生命周期深度绑定；装饰器是 Python 语言级别的语法糖，更通用但缺乏框架语义。

**9. 设计一个记忆压缩策略。**

```python
from agentscope.memory import InMemoryMemory
from agentscope.model import OpenAIChatModel

class CompressionConfig:
    """记忆压缩配置。"""
    trigger_threshold: int = 20      # 超过 20 条消息触发压缩
    target_size: int = 5             # 压缩后保留 5 条摘要
    compression_model: str = "gpt-4o-mini"  # 用于生成摘要的模型

async def compress_memory(
    memory: InMemoryMemory,
    model: OpenAIChatModel,
    config: CompressionConfig,
) -> None:
    """当记忆超过阈值时，自动压缩历史消息为摘要。"""
    messages = await memory.get_memory()

    if len(messages) <= config.trigger_threshold:
        return  # 未达阈值，跳过

    # 1. 提取需要压缩的消息（除最近 5 条外）
    to_compress = messages[:-5]
    keep_recent = messages[-5:]

    # 2. 使用 LLM 生成摘要
    summary_prompt = (
        "请总结以下对话的关键信息，保留所有重要事实和决策:\n"
        + "\n".join([f"{msg.name}: {msg.content}" for msg in to_compress])
    )
    summary_msg = Msg("user", summary_prompt, "user")
    summary_response = await model([summary_msg.to_dict()])
    summary = summary_response.content

    # 3. 清空记忆并重新构建
    memory.clear()

    # 4. 添加压缩摘要（标记为 COMPRESSED）
    summary_msg = Msg("system", f"[历史摘要] {summary}", "system")
    await memory.add(summary_msg, marks=["compressed"])

    # 5. 恢复最近的消息
    for msg in keep_recent:
        await memory.add(msg)

# 在 Agent 的 post_reply Hook 中触发压缩
async def auto_compress_hook(self, msg, **kwargs):
    if self.memory and hasattr(self, "compression_config"):
        await compress_memory(
            self.memory,
            self.model,
            self.compression_config,
        )
    return msg

# 注册
AgentBase.register_post_reply_hook(auto_compress_hook)
```

**设计要点**：
- **触发条件**: 基于消息数量阈值，避免频繁压缩
- **保留策略**: 保留最近 N 条原始消息，只压缩更早的历史
- **摘要质量**: 使用 LLM 生成摘要，确保关键信息不丢失
- **标记管理**: 使用 `"compressed"` 标记区分摘要消息和原始消息
- ** Hook 集成**: 在 `post_reply` Hook 中自动触发，无需手动调用

---

## 参考资料

- 源码文件路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/agent/`
- Hooks 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/hooks/`
- AgentScope 官方文档: https://agentscope.readthedocs.io

---

*文档版本: 1.0*
*最后更新: 2026-04-27*
