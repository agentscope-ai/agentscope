# SOP：src/agentscope/model 模块

## 一、功能定义（Scope）
- 层次：模型接入层，负责与各提供方 API 的最小封装。
- 作用：统一流式/非流式返回、工具调用协议、结构化输出与用量统计；按提供方能力差异提供最小必要适配。
- 非目标：不内置业务提示词与复杂重试策略；不修改 Formatter 的消息/工具协议。

## 二、文件 / 类 / 函数 / 执行逻辑

### 文件：src/agentscope/model/_model_base.py
- 类：`ChatModelBase`
  - 约束：`__call__(messages, tools=None, tool_choice=None, structured_model=None, **kwargs)` 必须异步；实现类需处理流式与非流式两条路径。

### 文件：src/agentscope/model/_openai_model.py
- 类：`OpenAIChatModel`
  - 调用流程（非结构化）：
    1) 组装参数：`model`、`messages`、`stream`、`tools`（如有）、`tool_choice`（如有）、以及 `generate_kwargs`；
    2) 非流式：`client.chat.completions.create(**kwargs)` → `_parse_openai_completion_response`：
       - 提取 `thinking`（reasoning_content）、`text`、`audio`、`tool_calls`，封装为内容块；
       - 统计 `usage`（prompt/completion tokens, time）；
    3) 流式：`client.chat.completions.stream(**kwargs)` → `_parse_openai_stream_response`：
       - 逐块累积 `thinking/text/audio`，维护 `tool_calls` 映射；
       - 每次有增量就产出 `ChatResponse`（content + usage）；
  - 结构化输出（`structured_model` 非空）：
    - 忽略 `tools/tool_choice/stream`，使用 `response_format=structured_model`；流式下逐块解析 `parsed` 为 `metadata`；非流式将 `choice.message.parsed` 转入 `metadata`。
  - 工具协议：
    - `tools` 直接接受 Formatter 产出的 JSON schema；`tool_choice` 允许 `auto/none/any/required/具体函数名`；
    - 流式工具调用时，分片累加 `function.arguments`；最终在 `content` 中以 `ToolUseBlock` 体现。
  - 错误与边界：
    - OpenAI 客户端抛错向上冒泡；调用方负责重试/退避；
    - 计时起点在进入 API 前；`usage` 的 time 基于 `datetime.now()` 差值。

### 文件：src/agentscope/model/_anthropic_model.py
- 类：`AnthropicChatModel`
  - 流式/结构化/工具协议与 OpenAI 同构思想；实现细节以文件为准（结构化输出通过 `_create_tool_from_base_model` 注入强制工具）。

### 文件：src/agentscope/model/_dashscope_model.py
- 类：`DashScopeChatModel`
  - 与 Anthropic 类似，遵循相同的内容块与工具调用抽象；注意提供方字段命名差异。

### 文件：src/agentscope/model/_gemini_model.py
- 类：`GeminiChatModel`
  - 处理文本/多模态；结构化输出与流式解析细节以实现为准。

### 文件：src/agentscope/model/_ollama_model.py
- 类：`OllamaChatModel`
  - 流式文本累积并在需要时通过 `_json_loads_with_repair` 解析结构化片段。

### 文件：src/agentscope/model/_model_response.py
- 类：`ChatResponse`、`ChatUsage`
  - 统一模型返回的内容块与用量结构；`created_at/id` 使用 `_get_timestamp`。

## 三、与其他组件的交互关系
- Formatter：严格遵循 Formatter 的消息与工具 schema；不得在模型层“改写”消息结构。
- Agent（ReAct）：依赖模型提供工具调用分片与结构化输出 `metadata`，由 Agent/Toolkit 汇总为最终 `Msg`。
- Tracing：`@trace_llm` 产出 spans，记录输入/输出与异常。

## 四、Docs‑First 变更流程与验收
1) 本 SOP 标注改动目的与影响条目；
2) 在 PR 中链接涉及的提供方实现与约束差异；
3) 验收 Checklist：
   - [ ] 非流式/流式路径均能产出预期内容块（thinking/text/audio/tool_use）与 `usage`
   - [ ] 结构化输出路径（`structured_model`）在流式与非流式均能将结构体写入 `metadata`
   - [ ] 工具协议：`tools/tool_choice` 行为与 Formatter 契合；流式工具分片正确累积
   - [ ] 异常向上冒泡且被 Tracing 捕获；无静默失败
   - [ ] `ruff check src`（仅检测）无待处理告警；`mypy/pytest` 通过
