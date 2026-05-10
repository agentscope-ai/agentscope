# 第十四章：策略模式——Formatter 的多态分发

**难度**：中等

> 你接了一个 bug：Gemini 模型的工具调用格式不对。日志里显示 `function_call` 字段被发成了 `tool_calls`——这是 OpenAI 的格式。你翻了翻代码，发现 Model 层根本不管格式，格式转换全部在 Formatter 里完成。这就是本章要讲的策略模式。

---

## 1. 开场场景

你的 Agent 配了一个 Gemini 模型：

```python
agent = ReActAgent(
    name="Helper",
    model=GeminiChatModel(model_name="gemini-2.0-flash"),
    formatter=GeminiChatFormatter(),  # <-- 你忘了写这行
    ...
)
```

运行后 Gemini API 返回 400 错误：`"tool_calls" is not a valid key`。原因是 Agent 拿到了一段 OpenAI 格式的消息，原封不动发给了 Gemini。

看 `ReActAgent.__init__` 的签名（`_react_agent.py` 第 177-182 行）：

```python
def __init__(
    self,
    name: str,
    sys_prompt: str,
    model: ChatModelBase,
    formatter: FormatterBase,  # 必须显式传入
    ...
)
```

`model` 和 `formatter` 是两个独立参数。Model 负责 HTTP 调用，Formatter 负责消息格式——它们之间的配合完全靠调用者保证。这就是策略模式的典型应用：同一个 Agent，换一个 Formatter 就能对接不同的 LLM API。

---

## 2. 设计模式概览

Formatter 的类层次结构如下：

```
FormatterBase (抽象基类)
  |
  +-- TruncatedFormatterBase (抽象, 加入截断逻辑)
        |
        +-- OpenAIChatFormatter
        +-- OpenAIMultiAgentFormatter
        +-- AnthropicChatFormatter
        +-- AnthropicMultiAgentFormatter
        +-- GeminiChatFormatter
        +-- GeminiMultiAgentFormatter
        +-- DashScopeChatFormatter
        +-- DashScopeMultiAgentFormatter
        +-- OllamaChatFormatter
        +-- OllamaMultiAgentFormatter
        +-- DeepSeekChatFormatter
        +-- DeepSeekMultiAgentFormatter
        +-- A2AChatFormatter
```

每个 Provider 有两个 Formatter：
- `XxxChatFormatter` —— 双人对话（一个 user + 一个 assistant）
- `XxxMultiAgentFormatter` —— 多 Agent 对话（需要把历史折叠进 `<history>` 标签）

策略模式的核心在这里：`FormatterBase` 定义了 `format()` 接口，每个子类实现自己的格式转换规则。调用者（Agent）只依赖抽象接口，不知道也不关心具体用了哪种格式。

---

## 3. 源码分析

### 3.1 FormatterBase：定义接口

`_formatter_base.py` 第 11-15 行：

```python
class FormatterBase:
    """The base class for formatters."""

    @abstractmethod
    async def format(self, *args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        """Format the Msg objects to a list of dictionaries that satisfy the
        API requirements."""
```

接口很简洁：输入 `Msg` 对象列表，输出 `list[dict]`——即 LLM API 能直接接受的消息格式。

这个类还提供了两个工具方法。第一个是 `assert_list_of_msgs`（第 20-34 行），做类型校验：

```python
@staticmethod
def assert_list_of_msgs(msgs: list[Msg]) -> None:
    if not isinstance(msgs, list):
        raise TypeError("Input must be a list of Msg objects.")
    for msg in msgs:
        if not isinstance(msg, Msg):
            raise TypeError(f"Expected Msg object, got {type(msg)} instead.")
```

第二个是 `convert_tool_result_to_string`（第 37-129 行），处理工具调用结果中的多模态数据。因为很多 LLM API 不支持在工具结果里直接放图片，所以这个方法把图片/音频/视频的 URL 或 base64 数据转成文本描述，同时返回原始数据供后续提升（promote）使用。

### 3.2 TruncatedFormatterBase：加入截断骨架

`_truncated_formatter_base.py` 第 19 行：

```python
class TruncatedFormatterBase(FormatterBase, ABC):
```

它在 `FormatterBase` 之上增加了两层能力：**消息分组** 和 **token 截断**。

先看 `format()` 方法（第 48-83 行）的核心循环：

```python
async def format(self, msgs: list[Msg], **kwargs) -> list[dict[str, Any]]:
    msgs = deepcopy(msgs)          # 深拷贝，不污染原始数据
    while True:
        formatted_msgs = await self._format(msgs)
        n_tokens = await self._count(formatted_msgs)
        if (n_tokens is None or self.max_tokens is None
                or n_tokens <= self.max_tokens):
            return formatted_msgs
        msgs = await self._truncate(msgs)  # 超了就截，再来
```

这是一个 while 循环：格式化 -> 计 token -> 超限就截断 -> 重新格式化。截断策略在 `_truncate()` 方法中（第 151-215 行）：从最早的普通消息开始删除，但保证 tool_use 和 tool_result 成对移除。

再看 `_format()` 方法（第 85-113 行）中的消息分组逻辑：

```python
async for typ, group in self._group_messages(msgs[start_index:]):
    match typ:
        case "tool_sequence":
            formatted_msgs.extend(await self._format_tool_sequence(group))
        case "agent_message":
            formatted_msgs.extend(await self._format_agent_message(group, ...))
```

`_group_messages()` 是一个异步生成器（第 231-297 行），把消息流分成两种组：
- **tool_sequence**：包含 `tool_use` 或 `tool_result` block 的消息
- **agent_message**：普通对话消息

这个分组在多 Agent 场景下至关重要：多条普通消息会被合并进 `<history>` 标签，而工具调用序列必须保持原样（因为 LLM API 对工具调用的格式有严格约束）。

`TruncatedFormatterBase` 定义了三个抽象钩子，由 Provider 子类实现：
- `_format_tool_sequence()` —— 格式化工具调用序列
- `_format_agent_message()` —— 格式化普通对话消息
- `_format_system_message()` —— 格式化系统提示（有默认实现）

### 3.3 OpenAIChatFormatter：OpenAI 的格式规则

`_openai_formatter.py` 第 168-371 行。

OpenAI API 的消息格式是业界事实标准，理解它有助于对比其他 Provider 的差异。核心逻辑在 `_format()` 方法中（第 219-371 行），遍历每条消息的 content blocks：

**文本 block**（第 245-246 行）—— 直接保留：
```python
if typ == "text":
    content_blocks.append({**block})
```

**工具调用 block**（第 248-261 行）—— 转成 OpenAI 的 `tool_calls` 结构：
```python
elif typ == "tool_use":
    tool_calls.append({
        "id": block.get("id"),
        "type": "function",
        "function": {
            "name": block.get("name"),
            "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
        },
    })
```

注意：`input` 是 dict，必须 `json.dumps` 成字符串。这是 OpenAI 的要求。

**工具结果 block**（第 263-326 行）—— 生成 `role: "tool"` 的独立消息：
```python
elif typ == "tool_result":
    textual_output, multimodal_data = self.convert_tool_result_to_string(block["output"])
    messages.append({
        "role": "tool",
        "tool_call_id": block.get("id"),
        "content": textual_output,
        "name": block.get("name"),
    })
```

最终，每条消息组装成（第 355-366 行）：
```python
msg_openai = {
    "role": msg.role,
    "name": msg.name,
    "content": content_blocks or None,
}
if tool_calls:
    msg_openai["tool_calls"] = tool_calls
```

### 3.4 GeminiChatFormatter：为什么格式不同

`_gemini_formatter.py` 第 108-310 行。

现在回到开场场景的 bug。Gemini API 和 OpenAI API 的核心差异：

| 概念 | OpenAI | Gemini |
|------|--------|--------|
| 消息结构 | `{"role": "...", "content": [...]}` | `{"role": "...", "parts": [...]}` |
| 工具调用 | `tool_calls` 数组，含 `function.name` | `function_call` 对象，含 `name` |
| 工具结果 | `role: "tool"`, `tool_call_id` | `role: "user"`, `function_response` |
| assistant 角色 | `role: "assistant"` | `role: "model"` |
| 图片格式 | `{"type": "image_url", "image_url": {...}}` | `{"inline_data": {"data": ..., "mime_type": ...}}` |

看 Gemini 的工具调用格式（第 200-210 行）：

```python
elif typ == "tool_use":
    parts.append({
        "function_call": {
            "id": None,
            "name": block["name"],
            "args": block["input"],        # 注意：直接传 dict
        },
        "thought_signature": block.get("id", None),
    })
```

与 OpenAI 的差异：
1. 键名是 `function_call`（单数），不是 `tool_calls`（复数）
2. 参数字段是 `args`（dict），不是 `arguments`（JSON 字符串）
3. `id` 被放在 `thought_signature` 里，而不是 `id` 字段

工具结果更不一样（第 213-233 行）：

```python
elif typ == "tool_result":
    messages.append({
        "role": "user",                    # OpenAI 用 "tool"
        "parts": [{
            "function_response": {         # OpenAI 用顶层字段
                "id": block["id"],
                "name": block["name"],
                "response": {"output": textual_output},
            },
        }],
    })
```

角色映射也不同（第 296 行）：

```python
role = "model" if msg.role == "assistant" else "user"
```

Gemini 没有 `system` 角色，也没有 `tool` 角色——所有非 model 的消息都是 `user`。

---

## 4. 设计一瞥

### 为什么不把格式转换放在 Model 里？

一个自然的想法是让 `GeminiChatModel` 自己处理格式，不需要单独的 Formatter。AgentScope 选择把它们分开，原因有三：

**1. 职责单一。** Model 的职责是发 HTTP 请求、处理重试、解析响应。Formatter 的职责是把 `Msg` 转成 dict。混在一起会让 Model 类膨胀——每个 Provider 的 Model 都要同时处理网络和格式。

**2. 可测试性。** 你可以单独测试 Formatter：传入构造好的 `Msg` 列表，检查输出的 dict 是否符合 API 规范，完全不需要发网络请求。

**3. 组合灵活。** `MultiAgentFormatter` 内部委托给 `ChatFormatter` 处理工具序列。比如 `GeminiMultiAgentFormatter._format_tool_sequence`（第 399-416 行）直接创建一个 `GeminiChatFormatter` 实例：

```python
async def _format_tool_sequence(self, msgs):
    return await GeminiChatFormatter(
        promote_tool_result_images=self.promote_tool_result_images,
    ).format(msgs)
```

如果格式逻辑嵌在 Model 里，这种组合就不可能了——你总不能在一个 Model 里创建另一个 Model 来处理部分消息。

### 截断与格式化的解耦

`TruncatedFormatterBase` 把截断策略和格式转换分开。`format()` 是模板方法，`_format()` 是策略方法。子类只关心"怎么格式化"，不用操心"超了怎么办"。这是模板方法模式（Template Method）和策略模式（Strategy）的联用。

---

## 5. 横向对比

其他多模型框架如何处理格式差异？

**LangChain** 使用 `BaseMessage` 子类体系（`HumanMessage`, `AIMessage`, `SystemMessage`, `ToolMessage`），每个 Chat Model 实现内部包含私有的格式转换逻辑。好处是开箱即用，坏处是格式逻辑散落在各个 Model 文件中，无法独立测试。

**LiteLLM** 采用统一转换层：所有输入用 OpenAI 格式，内部根据目标 Provider 转换。好处是接口统一，坏处是转了两次（用户的格式 -> OpenAI 格式 -> 目标格式），且新增 Provider 需要理解整个转换管线。

**AgentScope** 的方式是让 Formatter 成为独立的第一等公民。它的代价是调用者必须自己匹配 Model 和 Formatter（如 `GeminiChatModel` + `GeminiChatFormatter`），但换来的是每个 Formatter 可以独立演进和测试。一个实际的好处是：当你发现 Gemini API 更新了某个字段的格式，你只需要改 `_gemini_formatter.py` 一个文件，不用碰 Model 层。

---

## 6. 调试实践

当你遇到格式相关的 bug（比如 API 返回 400 错误），按以下步骤排查：

**Step 1：确认 Formatter 类型。** 在调用处打印：

```python
print(type(agent.formatter))
# 应该是 XxxChatFormatter 或 XxxMultiAgentFormatter
# 如果不是你用的 Model 对应的 Formatter，这就是 bug
```

**Step 2：检查单条消息的格式化输出。** Formatter 是独立的，你可以直接调用：

```python
from agentscope.message import Msg
from agentscope.formatter import GeminiChatFormatter

formatter = GeminiChatFormatter()
test_msg = Msg(name="user", content="hello", role="user")
result = await formatter.format([test_msg])
print(result)
# [{'role': 'user', 'parts': [{'text': 'hello'}]}]
```

如果输出不符合目标 API 的文档描述，问题在 Formatter。

**Step 3：关注 block 类型映射。** 最常见的 bug 来源是 block 类型处理遗漏。检查你的 `Msg.content` 中有哪些 block 类型，然后去对应 Formatter 的 `_format()` 方法里找对应的 `elif` 分支。

**Step 4：多 Agent 场景检查分组。** 如果你用了 `XxxMultiAgentFormatter`，检查 `_group_messages()` 的分组是否符合预期。可以在 `_format()` 的 `async for` 循环处打断点，看 `typ` 和 `group` 的内容。

**Step 5：图片/音频格式。** 不同 Provider 对多媒体的格式要求差异很大。OpenAI 用 `image_url`，Gemini 用 `inline_data`，Ollama 用纯 base64 字符串。如果涉及多媒体，仔细对比 `_format_xxx_image_block` 系列辅助函数。

---

## 7. 检查点

1. `FormatterBase.format()` 的输入和输出类型分别是什么？
2. `TruncatedFormatterBase.format()` 中的 while 循环什么时候终止？有几种终止条件？
3. 在 `OpenAIChatFormatter._format()` 中，`tool_use` block 被转成了什么结构？`input` 字段经历了什么变换？
4. 在 `GeminiChatFormatter._format()` 中，`assistant` 角色被映射成了什么？
5. `_group_messages()` 为什么要把消息分成 `tool_sequence` 和 `agent_message` 两组？如果不分组，多 Agent 场景下会出现什么问题？
6. 如果你要新增一个 Provider（比如 Mistral），需要创建哪些类？至少需要实现哪些方法？

---

## 8. 下一章预告

Formatter 把 `Msg` 变成了 dict，但 Model 拿到 dict 之后还需要考虑重试、超时和流式解析——下一章看 AgentScope 的 Model 层如何用适配器模式统一不同的 HTTP API。
