# SOP：src/agentscope/_utils 模块

本模块为框架的“底层工具层”，遵循 AGENTS.md 的“文档先行、简单可组合”。

## 一、功能定义（Scope）
- 层次：提供最小必要的通用工具，服务于模型、格式化器、工具系统、RAG、评测与追踪等模块。
- 作用：
  - 提供鲁棒 JSON 解析与 schema 处理；
  - 统一同步/异步函数调度；
  - 将 Pydantic/BaseModel 或 MCP Tool 转为工具调用所需 JSON Schema；
  - 基础 I/O 辅助：本地文件可达性、Base64 落盘、简单 URL 取字节、时间戳；
  - 文本→稳定 UUID 映射（向量库主键）。
- 非目标：不承载业务策略、不扩展到复杂网络/缓存/加密；仅提供通用最小能力。

## 二、文件 / 类 / 函数 / 成员变量

### 文件：src/agentscope/_utils/_common.py
- 类：无
- 函数：
  - `_json_loads_with_repair(json_str) -> dict|list|primitive`
    - 逻辑：先调用 `json_repair.repair_json` 修复缺失逗号/引号等常见问题，再执行 `json.loads`。若两者任一步失败，捕获异常并抛出 `ValueError`，错误信息包含原串与修复串，方便排查。
  - `_is_accessible_local_file(path: str) -> bool`
    - 逻辑：使用 `os.path.exists` 和 `os.path.isfile` 检查路径是否存在且类型为文件；遇到异常（权限等）返回 `False`。
  - `_get_timestamp(add_random_suffix=False) -> str`
    - 逻辑：通过 `datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]` 生成毫秒级时间戳；当 `add_random_suffix=True` 时追加 `shortuuid.uuid()[:4]`，用于去重。
  - `_is_async_func(func) -> bool`
    - 逻辑：依次检测 `inspect.iscoroutinefunction`、`iscoroutine`, `isasyncgenfunction`，并解包 `functools.partial` 以及带 `__wrapped__` 的装饰器，确保最终目标函数被正确识别。
  - `_execute_async_or_sync_func(func, *args, **kwargs) -> Any`
    - 逻辑：基于 `_is_async_func` 判断：若为协程/异步生成器，则 `await` 后返回结果；若为同步函数，则在同一协程中直接调用。异常不吞噬，原样抛出。
  - `_get_bytes_from_web_url(url: str, max_retries=3) -> str`
    - 逻辑：使用 `requests.get`（默认 headers）拉取内容，失败时指数退避重试；`Content-Type` 含 `text/` 则返回原文本，否则使用 `base64.b64encode` 输出 ASCII 字符串。
  - `_save_base64_data(media_type: str, base64_data: str) -> str`
    - 逻辑：根据 `media_type` 推导扩展名（默认 `.bin`），在临时目录创建文件，`base64.b64decode` 后写入，返回文件路径。未识别类型使用通用扩展。
  - `_extract_json_schema_from_mcp_tool(tool) -> dict`
    - 逻辑：遍历 `tool.input_schema`，提取 `properties`、`required` 等字段，剔除与 MCP 绑定的元信息，生成符合 JSON Schema Draft-07 的结构。
  - `_remove_title_field(schema: dict) -> None`
    - 逻辑：递归遍历 schema dict，删除 `title` 字段，防止模型对标题产生偏置；嵌套结构（`properties`、`items`）同样处理。
  - `_create_tool_from_base_model(structured_model: BaseModel, tool_name="generate_structured_output") -> dict`
    - 逻辑：读取 Pydantic BaseModel 的 `model_json_schema()`，调用 `_remove_title_field` 清洗后包装成 `{name, description, parameters}` 字段的工具描述，供模型以函数调用方式返回结构化结果。
  - `_map_text_to_uuid(text: str) -> str`
    - 逻辑：使用 `uuid.uuid3(uuid.NAMESPACE_DNS, text)` 生成稳定 ID，确保同一文本映射一致；常用于向量库主键。
- 成员变量：无

### 文件：src/agentscope/_utils/_mixin.py
- 类：`DictMixin(dict)`
  - 成员变量：无（继承自 `dict`）
  - 关键方法：`__getattr__`/`__setattr__` 代理到 `dict`，支持属性式访问。
- 函数：无

### 文件：src/agentscope/_utils/__init__.py
- 类：无
- 函数：无
- 成员变量：无

## 三、与其他组件的交互关系
- 模型（model）
  - `model/_openai_model.py`：解析工具入参与结构化结果时使用 `_json_loads_with_repair`。
  - `model/_anthropic_model.py`、`_dashscope_model.py`：结构化输出用 `_create_tool_from_base_model`；解析入参与结果用 `_json_loads_with_repair`。
  - `model/_model_response.py`：ID/时间使用 `_get_timestamp`。
  - `model/_ollama_model.py`、`_gemini_model.py`：将流式文本解析为元数据时使用 `_json_loads_with_repair`。
- 格式化器（formatter）
  - `_formatter_base.py`：将多模态工具结果转文本时用 `_save_base64_data`。
  - `_dashscope_formatter.py`：本地文件判断 `_is_accessible_local_file`。
  - `_gemini_formatter.py`、`_ollama_formatter.py`：URL 字节拉取 `_get_bytes_from_web_url`。
- 工具系统（tool）
  - `_registered_tool_function.py`、`_toolkit.py`：清洗 JSON Schema 用 `_remove_title_field`。
  - `_response.py`：ID/时间使用 `_get_timestamp`。
- RAG 向量库（rag/store）
  - `_qdrant_store.py`：点位主键生成使用 `_map_text_to_uuid`。
- 评测/计划/嵌入（evaluate/plan/embedding）
  - 多处对象的 `id/created_at` 使用 `_get_timestamp`。
- Agent/Plan 执行
  - `agent/_agent_meta.py`、`plan/_plan_notebook.py`：统一调度装饰/回调用 `_execute_async_or_sync_func`。
- MCP
  - `mcp/_mcp_function.py`：从 MCP Tool 生成工具 schema 用 `_extract_json_schema_from_mcp_tool`。

（以上为关键路径；如新增模块引用 `_utils`，需在此补充交互说明并在 `CLAUDE.md` 关联入口。）

## 四、变更流程（与 AGENTS.md 对齐）
对 `_utils` 的改动，先更新本 SOP，再在根目录 `todo.md` 提供执行步骤与验收清单，待审批后实施；合并时确保与引用方文档同步更新（含 `CLAUDE.md` 交互关系）。
代码实现阶段及提交前必须运行 `ruff check src`（或 `pre-commit run --files $(git ls-files 'src/**')`）并清零 `src/` 下的告警。
