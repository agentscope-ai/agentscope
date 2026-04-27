# Agent 模块与 Hooks 深度剖析

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

## 1. 模块概述

Agent 模块是 AgentScope 框架的核心模块，负责实现智能体的基本行为和交互逻辑。该模块位于 `/Users/nadav/IdeaProjects/agentscope/src/agentscope/agent/` 目录下，主要包含以下组件：

### 1.1 目录结构

```
src/agentscope/agent/
├── __init__.py                    # 模块导出
├── _agent_base.py                 # AgentBase 基类（核心）
├── _agent_meta.py                 # Agent 元类
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
│  + _retrieve_from_long_term_memory() // 长期记忆检索          │
│  + _retrieve_from_knowledge() // 知识库检索                  │
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
# 第 30-44 行
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
```

**关键初始化逻辑** (第 140-183 行):

```python
def __init__(self) -> None:
    """Initialize the agent."""
    super().__init__()

    self.id = shortuuid.uuid()  # 生成唯一ID

    # 回复任务管理
    self._reply_task: Task | None = None
    self._reply_id: str | None = None

    # 实例级钩子初始化
    self._instance_pre_print_hooks = OrderedDict()
    self._instance_post_print_hooks = OrderedDict()
    self._instance_pre_reply_hooks = OrderedDict()
    self._instance_post_reply_hooks = OrderedDict()
    self._instance_pre_observe_hooks = OrderedDict()
    self._instance_post_observe_hooks = OrderedDict()

    # 流式打印前缀缓存
    self._stream_prefix = {}

    # 订阅者列表
    self._subscribers: dict[str, list[AgentBase]] = {}

    # 控制台输出控制
    self._disable_console_output: bool = (
        os.getenv("AGENTSCOPE_DISABLE_CONSOLE_OUTPUT", "false").lower() == "true"
    )

    # 消息队列（用于流式输出）
    self._disable_msg_queue: bool = True
    self.msg_queue = None
```

### 3.2 核心方法：reply() 和 observe()

**reply() 方法** (第 197-203 行) - 抽象方法:

```python
async def reply(self, *args: Any, **kwargs: Any) -> Msg:
    """The main logic of the agent, which generates a reply based on the
    current state and input arguments."""
    raise NotImplementedError(
        "The reply function is not implemented in "
        f"{self.__class__.__name__} class.",
    )
```

**observe() 方法** (第 185-195 行) - 接收消息:

```python
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

### 3.3 调用入口：__call__()

**文件**: `_agent_base.py:448-467`

```python
async def __call__(self, *args: Any, **kwargs: Any) -> Msg:
    """Call the reply function with the given arguments."""
    self._reply_id = shortuuid.uuid()  # 生成回复ID

    reply_msg: Msg | None = None
    try:
        self._reply_task = asyncio.current_task()
        reply_msg = await self.reply(*args, **kwargs)  # 调用核心回复逻辑

    # 处理用户中断
    except asyncio.CancelledError:
        reply_msg = await self.handle_interrupt(*args, **kwargs)

    finally:
        # 广播消息给所有订阅者
        if reply_msg:
            await self._broadcast_to_subscribers(reply_msg)
        self._reply_task = None

    return reply_msg
```

### 3.4 消息打印机制

**print() 方法** (第 205-274 行):

```python
async def print(
    self,
    msg: Msg,
    last: bool = True,
    speech: AudioBlock | list[AudioBlock] | None = None,
) -> None:
    """The function to display the message."""
    if not self._disable_msg_queue:
        await self.msg_queue.put((deepcopy(msg), last, speech))
        await asyncio.sleep(0)  # 让出控制权给消费者协程

    if self._disable_console_output:
        return

    # 处理文本块和思考块
    thinking_and_text_to_print = []
    for block in msg.get_content_blocks():
        if block["type"] == "text":
            self._print_text_block(...)
        elif block["type"] == "thinking":
            self._print_text_block(...)
        elif last:
            self._print_last_block(block, msg)

    # 处理音频块
    if isinstance(speech, list):
        for audio_block in speech:
            self._process_audio_block(msg.id, audio_block)
    elif isinstance(speech, dict):
        self._process_audio_block(msg.id, speech)

    # 流结束时清理资源
    if last and msg.id in self._stream_prefix:
        if "audio" in self._stream_prefix[msg.id]:
            player, _ = self._stream_prefix[msg.id]["audio"]
            player.close()
        stream_prefix = self._stream_prefix.pop(msg.id)
```

### 3.5 订阅发布机制

**_broadcast_to_subscribers() 方法** (第 469-485 行):

```python
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

    broadcast_msg = self._strip_thinking_blocks(msg)

    for subscribers in self._subscribers.values():
        for subscriber in subscribers:
            await subscriber.observe(broadcast_msg)
```

### 3.6 中断处理机制

**interrupt() 方法** (第 528-531 行):

```python
async def interrupt(self, msg: Msg | list[Msg] | None = None) -> None:
    """Interrupt the current reply process."""
    if self._reply_task and not self._reply_task.done():
        self._reply_task.cancel(msg)
```

**handle_interrupt() 抽象方法** (第 516-526 行):

```python
async def handle_interrupt(
    self,
    *args: Any,
    **kwargs: Any,
) -> Msg:
    """The post-processing logic when the reply is interrupted."""
    raise NotImplementedError(
        f"The handle_interrupt function is not implemented in "
        f"{self.__class__.__name__}",
    )
```

### 3.7 Hook 注册机制

**register_class_hook() 类方法** (第 590-616 行):

```python
@classmethod
def register_class_hook(
    cls,
    hook_type: AgentHookTypes,
    hook_name: str,
    hook: Callable,
) -> None:
    """The universal function to register a hook to the agent class,
    which will take effect for all instances of the class."""
    assert hook_type in cls.supported_hook_types

    hooks = getattr(cls, f"_class_{hook_type}_hooks")
    hooks[hook_name] = hook
```

**register_instance_hook() 实例方法** (第 533-559 行):

```python
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
| `memory` | `MemoryBase` | 工作记忆 |
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

### 4.4 推理阶段 _reasoning()

**文件**: `_react_agent.py:540-655`

```python
async def _reasoning(
    self,
    tool_choice: Literal["auto", "none", "required"] | None = None,
) -> Msg:
    """Perform the reasoning process."""

    # 1. 获取计划提示
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

    # 3. 调用模型获取响应
    res = await self.model(
        prompt,
        tools=self.toolkit.get_json_schemas(),
        tool_choice=tool_choice,
    )

    # 4. 处理流式或非流式响应
    msg = Msg(name=self.name, content=[], role="assistant")
    if self.model.stream:
        async for content_chunk in res:
            msg.invocation_id = content_chunk.id
            msg.content = content_chunk.content
            speech = msg.get_content_blocks("audio") or None
            await self.print(msg, False, speech=speech)
    else:
        msg.invocation_id = res.id
        msg.content = list(res.content)

    # 5. 记录到记忆并返回
    await self.memory.add(msg)
    return msg
```

### 4.5 行动阶段 _acting()

**文件**: `_react_agent.py:657-714`

```python
async def _acting(self, tool_call: ToolUseBlock) -> dict | None:
    """Perform the acting process."""

    # 1. 创建工具结果消息
    tool_res_msg = Msg(
        "system",
        [
            ToolResultBlock(
                type="tool_result",
                id=tool_call["id"],
                name=tool_call["name"],
                output=[],
            ),
        ],
        "system",
    )

    try:
        # 2. 执行工具调用
        tool_res = await self.toolkit.call_tool_function(tool_call)

        # 3. 处理流式工具响应
        async for chunk in tool_res:
            tool_res_msg.content[0]["output"] = chunk.content
            await self.print(tool_res_msg, chunk.is_last)

            # 检查是否被中断
            if chunk.is_interrupted:
                raise asyncio.CancelledError()

            # 检查是否完成结构化输出
            if (
                tool_call["name"] == self.finish_function_name
                and chunk.metadata
                and chunk.metadata.get("success", False)
            ):
                return chunk.metadata.get("structured_output")

        return None

    finally:
        # 4. 记录工具结果到记忆
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

### 6.1 Hook 类型

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

### 6.2 Hook 注册示例

**Studio Hook 注册** (`hooks/_studio_hooks.py:17-29`):

```python
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

### 6.3 as_studio_forward_message_pre_print_hook 实现

**文件**: `hooks/_studio_hooks.py:12-58`

```python
def as_studio_forward_message_pre_print_hook(
    self: AgentBase,
    kwargs: dict[str, Any],
    studio_url: str,
    run_id: str,
) -> None:
    """The pre-speak hook to forward messages to the studio."""

    msg = kwargs["msg"]
    message_data = msg.to_dict()

    if hasattr(self, "_reply_id"):
        reply_id = getattr(self, "_reply_id")
    else:
        reply_id = shortuuid.uuid()

    n_retry = 0
    while True:
        try:
            res = requests.post(
                f"{studio_url}/trpc/pushMessage",
                json={
                    "runId": run_id,
                    "replyId": reply_id,
                    "replyName": getattr(self, "name", msg.name),
                    "replyRole": "user"
                    if isinstance(self, UserAgent)
                    else "assistant",
                    "msg": message_data,
                },
            )
            res.raise_for_status()
            break
        except Exception as e:
            if n_retry < 3:
                n_retry += 1
                continue
            logger.warning(
                "Failed to forward message to Studio after %d retries: %s. "
                "Agent will continue without Studio forwarding.",
                n_retry,
                e,
            )
            return
```

---

## 7. 设计模式总结

### 7.1 模板方法模式

**应用场景**: Agent 的 reply() 方法定义了算法框架，具体推理和行动由子类实现。

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
# Agent 广播消息给订阅者
await self._broadcast_to_subscribers(reply_msg)

# 订阅者接收消息
await subscriber.observe(broadcast_msg)
```

### 7.4 装饰器模式

**应用场景**: Hook 装饰器用于扩展 Agent 行为。

```python
# ReActAgentBase 使用元类自动包装 _reasoning 和 _acting 方法
```

### 7.5 洋葱模型

**应用场景**: Toolkit 的中间件机制。

```
请求 → middleware1 → middleware2 → middleware3 → 核心处理 → middleware3 → middleware2 → middleware1 → 响应
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

## 9. 练习题

### 9.1 基础题

1. **简述 AgentBase 的主要职责是什么？**

2. **在 `_agent_base.py:140-183` 中，AgentBase 的 `__init__` 方法初始化了哪些关键属性？**

3. **请说明 `reply()` 和 `observe()` 方法的区别。**

### 9.2 进阶题

4. **分析 ReActAgent 的推理-行动循环流程，参考 `_react_agent.py:376-537`。**

5. **设计一个自定义 Hook，实现在每次 Agent 回复前记录日志。**

6. **参考 `_agent_base.py:528-531`，分析 `interrupt()` 方法如何实现中断机制。**

### 9.3 挑战题

7. **尝试实现一个简单的多代理协作系统，包含一个指挥官代理和一个执行者代理。**

8. **分析 AgentScope 的 Hook 机制与装饰器模式的异同。**

9. **设计一个记忆压缩策略，当对话历史超过一定长度时自动压缩。**

---

## 参考资料

- 源码文件路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/agent/`
- Hooks 源码路径: `/Users/nadav/IdeaProjects/agentscope/src/agentscope/hooks/`
- AgentScope 官方文档: https://agentscope.readthedocs.io

---

*文档版本: 1.0*
*最后更新: 2026-04-27*
