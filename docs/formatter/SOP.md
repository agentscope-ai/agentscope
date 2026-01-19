# SOP：src/agentscope/formatter 模块

## 一、功能定义（Scope/非目标）
### 1. 设计思路和逻辑
- 将内部的 `Msg`/内容块/工具输出转换为各模型 SDK 所需的请求结构，保证 Agent → Formatter → Model 链路的可插拔与透明化。
- 支持文本、多模态（图像/音频/视频）、工具调用、多 Agent 会话等差异化需求；必要时提供长度截断、token 计数、Base64 编码等最小加工。
- 不生成业务模板、不开启自动重试，Formatter 仅负责结构转换和最小验证。

### 2. 架构设计
```mermaid
graph TD
    subgraph Base
        FB[FormatterBase]
        TB[TruncatedFormatterBase]
    end
    subgraph Providers
        OF[OpenAIChatFormatter]
        MF[OpenAIMultiAgentFormatter]
        DF[DashScopeChatFormatter]
        GF[GeminiChatFormatter]
        LF[OllamaChatFormatter]
        AF[AnthropicFormatter (if applicable)]
        DE[DeepSeekFormatter]
    end
    Msg & ToolBlocks --> FB
    FB --> TB
    TB --> OF
    TB --> DF
    TB --> GF
    FB --> LF
    FB --> MF
    FB --> DE
    FB --> Toolkit
    FB --> Model
```

### 3. 核心组件逻辑
- `FormatterBase.format`：抽象方法，子类负责把 `Msg` 序列转换为特定 provider 的 message payload；`assert_list_of_msgs` 验证输入类型。
- 多模态处理：根据内容块类型决定是否直接传 URL、转换为 Base64、或落盘（`convert_tool_result_to_string`、`_save_base64_data` 等）；OpenAI/Gemini 等对图像、音频有特殊要求。
- 工具与函数调用：将 `ToolUseBlock` 转为 provider 支持的 `function_call`/`tool_calls` 格式；`ToolResultBlock` 转成 `tool` 角色的文本。
- 截断与计数：`TruncatedFormatterBase` 结合 `TokenCounterBase` 提供最大 token 数控制（DashScope/OpenAI 等）。
- 多 Agent 对话：`OpenAIMultiAgentFormatter` 在消息头增加角色描述/系统信息，保持与多角色模型契合。

### 4. 关键设计模式
- **模板方法**：`FormatterBase` 定义抽象接口，子类实现 provider 细节。
- **适配器**：将统一消息块适配不同 API payload。
- **策略模式**：通过组合不同的 TokenCounter、截断逻辑，实现针对性限额策略。
- **工具结果规范化**：`convert_tool_result_to_string` 将多模态工具输出转为文本，作为低能力模型的兼容策略。

### 5. 其他组件的交互
- **Agent**：在调用模型前通过 Formatter 统一消息结构，必要时插入系统提示或工具结果。
- **Model**：依赖 Formatter 生成合法 payload；Formatter 不修改模型层协议，仅确保字段匹配。
- **Toolkit**：工具 schema 由 Formatter 透传；`ToolResultBlock` 被格式化回模型对话中。
- **Token Counter**：部分 Formatter 需要注入 `TokenCounterBase` 以计算和截断 token。
- **文件系统/网络**：多模态时可能需要保存临时文件或请求远程资源（例如拉取图像/音频 URL）。

## 二、文件/类/函数/成员变量映射到 src 路径
- `src/agentscope/formatter/_formatter_base.py`
  - `FormatterBase`：抽象基类；`assert_list_of_msgs`、`convert_tool_result_to_string`。
  - 依赖 `_save_base64_data`、`Msg` 块类型。
- `src/agentscope/formatter/_truncated_formatter_base.py`
  - `TruncatedFormatterBase`：在 `format` 前后执行 token 计数与截断策略，属性 `token_counter`、`max_tokens` 等。
- `src/agentscope/formatter/_openai_formatter.py`
  - `OpenAIChatFormatter`：支持文本/图像/音频、工具调用、多角色；私有方法 `_format`、`_to_openai_image_url`、`_to_openai_audio_data`; 支持 vision、tools API。
  - `OpenAIMultiAgentFormatter`：在 OpenAI 格式基础上附加多 Agent 排版。
- `src/agentscope/formatter/_dashscope_formatter.py`
  - `DashScopeChatFormatter`：自适应 Qwen 系列，处理多模态、工具、Token 截断。
- `src/agentscope/formatter/_gemini_formatter.py`
  - `GeminiChatFormatter`：处理 Google Gemini 的多模态输入格式（`parts`、`inline_data` 等）。
- `src/agentscope/formatter/_ollama_formatter.py`
  - `OllamaChatFormatter`：适配本地 Ollama 的对话结构。
- `src/agentscope/formatter/_anthropic_formatter.py`（若存在）
  - Anthropic 消息结构适配。
- `src/agentscope/formatter/_deepseek_formatter.py`
  - DeepSeek 模型专用格式。
- `src/agentscope/formatter/__init__.py`
  - 导出所有 Formatter 类。
- `src/agentscope/formatter/CLAUDE.md`
  - 记录调用链、token 截断逻辑；更新时同步维护。

## 三、关键数据结构与对外接口（含类型/返回约束）
- `FormatterBase.format(msgs: list[Msg], tools: list[dict[str, Any]] | None = None, **kwargs) -> list[dict[str, Any]] | dict`
  - `msgs`：按时间顺序的消息；`tools`：格式化后的 JSON schema；其它参数用于 provider 特定信息（如多 Agent 背景）。
  - 返回值：匹配模型 API 的 `messages`/payload（例如 OpenAI 的列表、Gemini 的字典等）。
  - 异常：`TypeError`（输入类型不符）、`ValueError`（不支持的块类型或多模态源）。
- `TruncatedFormatterBase` 额外参数
  - `max_tokens: int | None`、`token_counter: TokenCounterBase | None`、`drop_meta: bool` 等，用于截断策略。
  - 方法 `_truncate_messages`（内部使用）。
- Provider 特定接口简要：
  - `OpenAIChatFormatter(token_counter: TokenCounterBase | None = None, max_tokens: int | None = None, support_tools_api: bool = True, support_multiagent: bool = True, support_vision: bool = True)`。
  - `DashScopeChatFormatter` 可接受 `enable_thinking` 等参数。
  - `GeminiChatFormatter` 支持 `system_instruction`、`safety_settings` 等。
- 多模态辅助结构
  - `ToolResultBlock`、`ImageBlock`、`AudioBlock`、`VideoBlock` 来源于 `src/agentscope/message/_message_block.py`；Formatter 需兼容其 `source` 字段形式。
  - `TokenCounterBase`（在 `src/agentscope/token/_token_base.py`）用于估算 token。

## 四、与其他模块交互（调用链与责任边界）
- **标准流程**：Agent 汇总消息（含 memory/plan 提示）→ Formatter 格式化 → Model API 调用 → Agent 根据模型输出继续流程。
- **工具交互**：Formatter 将工具调用插入对话；模型返回的工具结果由 Agent/Toolkit 处理，Formatter 不参与执行。
- **多模态资源**：当工具返回 base64/URL 时，Formatter 负责转换为模型支持的格式；若需临时文件，会调用 `_save_base64_data`。
- **Token 约束**：当模型有长度限制时，Formatter 使用 TokenCounter 截断，否则交由模型处理。
- **责任边界**：
  - 不生成业务 Prompt；系统提示/角色描述由调用方提供；
  - 不缓存消息历史；
  - 网络请求（如下载图像）仅在转换需要的情况下进行，失败时抛出异常由上层决定是否重试。

## 五、测试文件
- 绑定文件：`tests/formatter_openai_test.py`、`tests/formatter_dashscope_test.py`、`tests/formatter_gemini_test.py`、`tests/formatter_ollama_test.py`、`tests/formatter_deepseek_test.py`
- 覆盖点：消息与工具 schema 映射、多模态处理、截断/计数行为。
