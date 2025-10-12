# SOP：src/agentscope/tracing 模块

## 一、功能定义（Scope）
- 层次：可观测性层，使用 OpenTelemetry 记录核心调用链，支持普通/异步/生成器场景。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/tracing/_setup.py
- 函数：`setup_tracing(endpoint: str)`
  - 设置 OTLP/HTTP 导出器并启用 `_config.trace_enabled`。

### 文件：src/agentscope/tracing/_trace.py
- 装饰器：`trace(name)`
  - 统一包装同步与异步函数；对（异步）生成器使用包装器在 `finally` 写入最后一块输出；异常时 `record_exception` 并设置 ERROR 状态。
- 装饰器：`trace_llm`
  - 用于模型调用，入参/出参包含消息、工具与流式分块；在流式场景下逐块产出并记录用量。
- 装饰器：`trace_toolkit`
  - 用于 `Toolkit.call_tool_function`，记录工具调用输入块与产出块；支持流式。
- 装饰器：`trace_format`
  - 用于 Formatter 的 `format()`，记录格式化前后消息结构。

## 三、与其他组件的交互关系
- Agent/Model/Toolkit/Formatter/Embedding：均可被装饰以记录调用链；启用需先调用 `agentscope.init(tracing_url=...)` 或传入 `studio_url`。

## 四、Docs‑First 变更流程与验收
1) 在本 SOP 增补改动目标与受影响装饰器；
2) 更新 `CLAUDE.md`：标注各装饰器覆盖的入口；
3) `todo.md` 写用例：同步/异步/生成器/流式四类路径均需覆盖；
4) 获批后实施与合入。
