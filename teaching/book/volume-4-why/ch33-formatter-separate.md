# 第三十三章 为什么 Formatter 独立于 Model

在 AgentScope 中，消息格式化（Formatter）和模型调用（Model）是两个独立对象。Agent 在构造时同时接收两者：

```python
# src/agentscope/agent/_react_agent.py，第 177-182 行
def __init__(
    self,
    name: str,
    sys_prompt: str,
    model: ChatModelBase,
    formatter: FormatterBase,
    ...
)
```

然后在推理阶段，先格式化，再调用：

```python
# 第 554-568 行
prompt = await self.formatter.format(
    msgs=[
        Msg("system", self.sys_prompt, "system"),
        *await self.memory.get_memory(...),
    ],
)
res = await self.model(
    prompt,
    tools=self.toolkit.get_json_schemas(),
    tool_choice=tool_choice,
)
```

两行代码。两个对象。一次协作。Formatter 把内部 `Msg` 列表翻译成某个 API 能理解的 `list[dict]`，Model 拿着这个字典列表发 HTTP 请求。职责清晰，互不干涉。

为什么不让 Model 自己处理格式化？为什么要用户同时管理两个对象？

## 一、决策回顾

### 1.1 Formatter 的完整接口

`FormatterBase`（`src/agentscope/formatter/_formatter_base.py`，第 11 行）只有一个抽象方法：

```python
class FormatterBase:
    @abstractmethod
    async def format(self, *args, **kwargs) -> list[dict[str, Any]]:
        """Format the Msg objects to a list of dictionaries that satisfy the
        API requirements."""
```

输入是 `Msg` 对象列表，输出是裸字典列表。没有 HTTP 调用，没有 API Key，没有网络。纯内存变换。

框架提供了 7 个具体实现，每个对应一个模型提供商的 API 格式：

| Formatter | 对应 API | 文件 |
|---|---|---|
| `OpenAIChatFormatter` | OpenAI / vLLM / Azure | `_openai_formatter.py` |
| `AnthropicChatFormatter` | Claude | `_anthropic_formatter.py` |
| `DashScopeChatFormatter` | 通义千问 | `_dashscope_formatter.py` |
| `GeminiChatFormatter` | Gemini | `_gemini_formatter.py` |
| `OllamaChatFormatter` | Ollama 本地 | `_ollama_formatter.py` |
| `DeepSeekChatFormatter` | DeepSeek | `_deepseek_formatter.py` |
| `A2AChatFormatter` | Agent-to-Agent | `_a2a_formatter.py` |

每个 Formatter 做的事情完全不同。以 OpenAI 和 Anthropic 为例：

OpenAI（`_openai_formatter.py`，第 219-371 行）把 `ToolUseBlock` 翻译成 `tool_calls` 字段，把图片翻译成 `image_url` 字段，本地文件自动 base64 编码。

Anthropic（`_anthropic_formatter.py`，第 123-217 行）把 `ToolUseBlock` 翻译成 `type: "tool_use"` 的 content block，把图片统一转成 base64 source（Anthropic 不支持 URL 图片），tool_result 必须放在 `role: "user"` 的消息里。

同一个 `Msg` 列表，两种完全不同的输出格式。这是 Formatter 独立存在的核心原因——格式差异的复杂度本身就值得一个独立抽象层。

### 1.2 Model 只接收字典

`ChatModelBase`（`src/agentscope/model/_model_base.py`，第 13 行）的接口：

```python
class ChatModelBase:
    @abstractmethod
    async def __call__(
        self, *args, **kwargs
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        pass
```

具体实现如 `OpenAIChatModel`（`_openai_model.py`，第 176 行）接收的参数：

```python
async def __call__(
    self,
    messages: list[dict],     # 已经格式化好的字典列表
    tools: list[dict] | None,
    tool_choice: ...,
    structured_model: ...,
)
```

注意第一个参数：`messages: list[dict]`。Model 不认识 `Msg` 对象。它只接收 Formatter 已经翻译好的字典。Model 的唯一职责是把字典列表发到 API 端点，解析响应，返回 `ChatResponse`。

### 1.3 Agent 是协作的编排者

在 `_reasoning` 方法（第 540-655 行）中，Agent 编排了完整的两步流程：

```python
# 第 554 行：Formatter 把 Msg 翻译成 dict
prompt = await self.formatter.format(msgs=[...])

# 第 568 行：Model 拿 dict 发请求
res = await self.model(prompt, tools=..., tool_choice=...)
```

Agent 持有 `self.model` 和 `self.formatter` 两个实例（第 275-276 行），在需要时分别调用。Model 不知道 Formatter 的存在，Formatter 也不知道 Model 的存在。

### 1.4 更极端的例子：压缩用不同的 Model 和 Formatter

在内存压缩场景中（`CompressionConfig`，第 164-168 行），Agent 可以用一对完全不同的 Model+Formatter 做压缩：

```python
class CompressionConfig(BaseModel):
    compression_model: ChatModelBase | None = None
    compression_formatter: FormatterBase | None = None
```

主对话用 GPT-4 + OpenAIChatFormatter，压缩可以用 Claude + AnthropicChatFormatter。两个 Formatter 共享同一套 `Msg` 对象，各自翻译成不同格式，分别喂给不同 Model。这种灵活组合只有在 Formatter 和 Model 独立时才可能。

## 二、被否方案

### 方案 A：Model 内嵌格式化逻辑

最直觉的做法——让 Model 自己处理格式化：

```python
class CombinedModel:
    """被否方案：Model 内部处理格式化"""

    def __init__(self, api_key, model_name, provider):
        self.client = AsyncOpenAI(api_key=api_key)
        self.model_name = model_name
        self.provider = provider  # "openai" | "anthropic" | ...

    async def __call__(self, msgs: list[Msg], **kwargs):
        # Model 自己判断用哪种格式
        if self.provider == "openai":
            prompt = self._format_openai(msgs)
            return await self.client.chat.completions.create(
                model=self.model_name, messages=prompt, **kwargs
            )
        elif self.provider == "anthropic":
            prompt = self._format_anthropic(msgs)
            return await self.client.messages.create(
                model=self.model_name, messages=prompt, **kwargs
            )
```

这个方案的问题：

1. **无法单独测试格式化**。验证 OpenAI 格式是否正确需要发真实请求。每次调试验式转换都要消耗 API 额度。
2. **无法独立替换**。切换 Formatter 意味着要换整个 Model 对象。想用 OpenAI 格式但走 vLLM 端点？得改 Model 内部代码。
3. **职责膨胀**。Model 同时负责格式转换、HTTP 调用、响应解析、流式处理。任何一项变动都牵连其他三项。

### 方案 B：Formatter 包含 Model 引用

让 Formatter 持有 Model，对外暴露一个统一接口：

```python
class SmartFormatter:
    """被否方案：Formatter 包装 Model"""

    def __init__(self, model: ChatModelBase, provider: str):
        self.model = model
        self._format_fn = {
            "openai": self._format_openai,
            "anthropic": self._format_anthropic,
        }[provider]

    async def chat(self, msgs: list[Msg], **kwargs):
        prompt = await self._format_fn(msgs)
        return await self.model(prompt, **kwargs)
```

这比方案 A 好一些，但引入了新问题：

1. **语义错位**。Formatter 的名字暗示它只做格式化，但现在它也发请求。调用者无法从类型签名判断 `SmartFormatter.chat()` 是否有副作用。
2. **无法一对多**。压缩场景需要一个 Formatter 服务于两个不同的 Model（主 Model 和压缩 Model）。如果 Formatter 绑定 Model，就无法复用。
3. **测试仍然耦合**。想测格式化正确性就得 mock 掉 HTTP 层，而不是直接测纯函数。

### 方案 C：工厂自动配对

一个中间方案——用工厂函数把 Model 和 Formatter 自动配对，用户不需要分别构造：

```python
def create_openai_chat(model_name, api_key):
    model = OpenAIChatModel(model_name, api_key)
    formatter = OpenAIChatFormatter()
    return model, formatter
```

这个方案其实和当前设计兼容。AgentScope 完全可以在上层提供这样的工厂函数，底层仍然是分离的。这不是对分离决策的否定，而是对分离决策的封装。

## 三、后果分析

### 3.1 收益

**格式化可独立测试**。验证 `OpenAIChatFormatter._format()` 是否正确，只需要构造 `Msg` 列表，调用 `format()`，检查输出字典。不需要 API Key，不需要网络，不需要 mock。一个纯函数的单元测试：

```python
async def test_openai_formatter_tool_use():
    msgs = [Msg("assistant", [ToolUseBlock(
        type="tool_use", id="call_123",
        name="search", input={"query": "weather"},
    )], "assistant")]
    formatter = OpenAIChatFormatter()
    result = await formatter.format(msgs)
    assert result[0]["tool_calls"][0]["function"]["name"] == "search"
```

这是最直接的好处。格式化逻辑复杂——OpenAI 的 `tool_calls`、Anthropic 的 content block `tool_use`、图片的 base64/URL 转换、音频格式检测——这些都不应该和 HTTP 调用耦合。

**Formatter 可独立替换**。在压缩场景（第 1076-1078 行）中，同一个 `Msg` 列表可以用不同的 Formatter 格式化：

```python
compression_formatter = (
    self.compression_config.compression_formatter or self.formatter
)
```

如果压缩用的是不同提供商的模型，只需要换 Formatter，不需要动 Agent 的其他配置。

**Model 实现更简洁**。`ChatModelBase` 的接口只有 `__call__`（第 38-44 行），接收 `list[dict]`，返回 `ChatResponse`。Model 不需要知道 `Msg` 的存在，不需要理解 `TextBlock`、`ImageBlock` 等类型。这让 Model 专注于 HTTP 调用、流式解析、错误重试等网络层关注点。

**多对多组合**。7 个 Formatter 和 7 个 Model 在理论上可以产生 49 种组合（虽然实践中只有 7-8 种有意义）。新的模型提供商只需要写一对 Formatter+Model，不需要修改 Agent 或其他组件。

### 3.2 代价

**更多对象需要管理**。构造一个 Agent 需要分别创建 Model 和 Formatter，确保它们配对正确。如果用 `OpenAIChatFormatter` 配了 `AnthropicChatModel`，运行时会得到格式不匹配的错误，但编译时不会报错。

**配对错误不容易发现**。类型系统无法在编译时保证 Formatter 和 Model 的匹配关系。`FormatterBase` 和 `ChatModelBase` 之间没有关联约束。这是一个运行时正确性问题。

**调用者需要理解两层**。新用户看到 `model` 和 `formatter` 两个参数时，需要理解为什么要分开——这正是本章试图解释的。如果不分开，用户只需要传一个 `model` 参数。

**内部使用了两次 Formatter 调用**。在 `_reasoning`（第 554 行）和 `_summarizing`（第 738 行）中，Agent 分别调用了 `self.formatter.format()`。如果格式化逻辑有 bug，它会在两处同时出现。这不是分离本身的问题，但它是分离后代码结构的副作用。

### 3.3 边界条件

`TruncatedFormatterBase`（`_truncated_formatter_base.py`，第 19 行）引入了一个有趣的边界情况。它同时持有 `token_counter` 和 `max_tokens`（第 40、45 行），在格式化后检查 token 数，超出限制则截断后重新格式化：

```python
# 第 48-83 行
async def format(self, msgs, **kwargs):
    msgs = deepcopy(msgs)
    while True:
        formatted_msgs = await self._format(msgs)
        n_tokens = await self._count(formatted_msgs)
        if n_tokens is None or self.max_tokens is None or n_tokens <= self.max_tokens:
            return formatted_msgs
        msgs = await self._truncate(msgs)
```

这意味着 Formatter 内部可能有一个循环：格式化 -> 计数 -> 截断 -> 重新格式化。这让 Formatter 不再是"纯"的格式转换——它包含了截断策略的决策。但截断仍然不需要网络调用，所以核心的"不依赖 Model"约束没有被打破。

## 四、横向对比

### LangChain

LangChain 的 `BaseChatModel` 同时处理格式化和调用。`ChatOpenAI.bind_tools()` 方法内部既做了工具格式转换，又准备了 API 请求。没有独立的 Formatter 概念。格式化逻辑散落在 Model 的各个方法中。

好处是用户只需要创建一个对象。坏处是无法在不发请求的情况下测试格式化输出，也无法在不同 Model 间共享格式化逻辑。

### Anthropic SDK

Anthropic 的官方 Python SDK 直接暴露 API 格式。用户构造的是 API 原生的 `message` 字典。没有内部消息类型，也没有格式化层——因为只有一个提供商，不需要抽象。

如果你只支持一个 API，Formatter 确实是多余的。AgentScope 支持 7 个提供商，这就是差异。

### Semantic Kernel

微软的 Semantic Kernel 用 `PromptTemplate` 处理提示模板，但格式化和调用同样在 `ChatCompletionClient` 内部完成。模板层只处理变量替换，不处理消息结构转换。

### 设计模式视角

AgentScope 的做法对应"策略模式"（Strategy Pattern）——Formatter 是格式化策略，Model 是调用策略，Agent 是上下文。两个维度独立变化，通过组合而非继承实现灵活搭配。

## 五、你的判断

思考以下问题：

1. **类型安全的配对**。能否用泛型或类型变量在编译时约束 Formatter-Model 配对？例如 `Agent[OpenAI]` 自动推导出 `OpenAIChatFormatter + OpenAIChatModel`？这会增加多少复杂度？

2. **默认配对**。框架是否应该提供工厂函数（方案 C）作为推荐用法，把分离作为高级选项暴露？这会降低入门门槛，但可能让用户忽略底层设计的优势。

3. **边界是否正确**。`TruncatedFormatterBase` 把截断逻辑放进了 Formatter，这模糊了"纯格式转换"的边界。截断是否应该由独立组件负责？

4. **替代方案**。如果用一个协议（Protocol）代替抽象基类，让任何实现了 `format()` 方法的对象都可以作为 Formatter，会不会更灵活？是否会牺牲类型安全性？

5. **趋势判断**。随着 OpenAI 兼容 API 成为事实标准（vLLM、LiteLLM、Ollama 都支持），未来是否所有提供商都会收敛到同一种格式？如果是，Formatter 独立的价值是否会降低？
