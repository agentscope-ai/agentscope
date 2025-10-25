# SubAgent Design: Search / Browser / File / Generate

本文件给出四类子代理（SubAgent）的正确设计思路与最小工具面，作为实现与验收的参考。所有工具 JSON Schema 严禁暴露密钥/令牌；如需外部会话或驱动，通过 host 注入句柄（preset_kwargs）或进程环境变量解决。

## Scope / 非目标
- 仅定义四类子代理的最小工具面与协作方式；不包含具体业务提示词与 UI。
- 不在子代理内开启对外广播与打印；由 Host+MsgHub 统一协调。
- 不在工具参数/Schema 中暴露任何密钥、cookie、会话标识。

## 依赖与注入规范
- 外部依赖（HTTP/MCP/headless 驱动等）通常通过 Host 注入为句柄或客户端对象：
  - Toolkit.register_mcp_client(...)，或
  - Toolkit.register_tool_function(func, preset_kwargs={"client": client})。
- Search 类工具为特例：每次调用在工具内部就地创建/关闭运行时（不使用 preset_kwargs），以保持参数面仅有 `{query}`。
- 密钥仅以环境变量或进程配置被客户端读取；绝不写入工具参数面。

## 通用约束（FS / MsgHub / 结构化完成）
- 文件写入（建议）：适合时将复杂结果沉淀为受控工件；具体前缀由宿主授权控制。
- 子代理执行期默认静默：不向控制台或 MsgHub 输出；Host 将 ToolResponse 统一转换为 Msg 并广播。
- 需要确定性收束时，由 Host 在完成函数上 `set_extended_model(...)` 绑定结构化模型。
- 所有工具统一返回 `ToolResponse`：`metadata` 为框架保留的固定集合（不得增删业务字段）；业务结果一律写入 `content`（可为 Markdown/纯文本或 JSON 文本）。

## 总体协作
- 拓扑建议：Search → Browser → File → Generate，通过 Host 的 MsgHub 广播联动；子代理仅返回单个 ToolResponse，流控与广播在 Host 层完成。
- 命名空间：具体写入位置由宿主的 FileDomainService 授权；读取和写入均应通过受控接口完成。
- 契约：零偏差；子代理对外工具参数只含必要字段，不夹带“便利”参数。

---

## 1) Agent Search（多来源搜索）
- 目标：面向公开/私域的检索，只负责“查到入口 URL 与摘要”。不负责抓取详情与页面下载。
- 最小工具面（每个来源一个工具，仅暴露 `query: str`）：
  - `search_google(query: str)`
  - `search_bing(query: str)`
  - `search_sogou(query: str)`
  - 私域：`search_github(query: str)`、`search_wiki(query: str)`（可扩展更多私域源）
- 约束：
  - 不出现任何密钥字段；运行期依赖（如 Playwright/WebKit 或 HTTP 客户端）由工具在每次调用内自行创建与关闭，不通过 preset_kwargs 注入，且不进入 schema。
  - 返回仅写入 `ToolResponse.content` 的纯文本列表，每条包含 `title`/`url`/`snippet`；不修改 `metadata`。

- 最小 JSON Schema（示例：search_google）
```json
{
  "type": "function",
  "function": {
    "name": "search_google",
    "description": "Search Google and return top results.",
    "parameters": {
      "type": "object",
      "properties": {"query": {"type": "string"}},
      "required": ["query"]
    }
  }
}
```

- 运行期（伪代码，按调用就地初始化）：
```python
async def search_google(query: str) -> ToolResponse:
    # create ephemeral runtime here (e.g., launch web client per call)
    rows = await do_google_search(query)
    rows = truncate_rows(rows)
    text = render_text_list(rows)  # plain text: title/url/snippet
    return ToolResponse(content=[TextBlock(type="text", text=text)])
```

- ToolResponse 示例（search_google）
```json
{ "content": [{"type": "text", "text": "- title: ...\n  url: https://...\n  snippet: ..."}] }
```

## 2) Agent Browser（网页查看器 / 自动化）
分为两类：

### A. Viewer（网页查看器，不操控浏览器）
- 目标：对给定 URL 进行查看、转换与（可选）下载；不做类型特化。
- 最小工具面：
  - `viewer_fetch(url: str, query: str | None)`：
    - 当 `query` 为空：抓取页面并转换为 Markdown（可落盘至受控路径）。
    - 当 `query` 存在：基于抓取的正文交由 LLM 做定向抽取后返回（answer + evidence[{url, span}]）。
  - `http_download(url: str, save_path: str)`：下载资源到授权的工件路径（示例：`<artifact_path>/downloads/`）。

- 参数 JSON Schema（viewer_fetch）
```json
{
  "type": "function",
  "function": {
    "name": "viewer_fetch",
    "parameters": {
      "type": "object",
      "properties": {
        "url": {"type": "string"},
        "query": {"type": "string"}
      },
      "required": ["url"]
    }
  }
}
```

- 参数 JSON Schema（http_download）
```json
{
  "type": "function",
  "function": {
    "name": "http_download",
    "parameters": {
      "type": "object",
      "properties": {
        "url": {"type": "string"},
        "save_path": {"type": "string"}
      },
      "required": ["url", "save_path"]
    }
  }
}
```

- ToolResponse 示例（viewer_fetch）
```json
// 无 query 分支
{
  "content": [
    {"type": "text", "text": "# Page Title\n...markdown..."}
  ]
}

// 有 query 分支
{
  "content": [
    {"type": "text", "text": "Answer: 123.45 USD\nEvidence: https://... :: ...text..."}
  ]
}
```

- ToolResponse 示例（http_download）
```json
{
  "content": [
    {"type": "text", "text": "Downloaded: <artifact_path>/downloads/file.pdf"}
  ]
}
```

### B. Automation（可操控浏览器）
- 目标：对必须交互才能获取的数据执行自动化（参考 `examples/agent_browser`）。
- 常用工具面（示例）：
  - `browser_navigate(url: str)`、`browser_snapshot()`、`browser_screenshot()`
  - `browser_click(selector: str)`、`browser_type(selector: str, text: str)`、`browser_wait(selector: str, timeout: int)`
- 场景：如“获取阿里巴巴当前股价”这类需交互/脚本执行的页面。
- 生命周期钩子：`pre_reply`（首次导航）、`pre_reasoning`（快照）、`post_reasoning`（清理观测）、`post_acting`（规范化输出）。
- 典型序列（股价）：navigate → wait("price_selector") → snapshot → viewer_fetch(query="current price")。

## 3) Agent File（按类型处理文件，全部走 FileDomainService）
- 目标：针对常见类型提供最小读/写/解析能力；所有路径经受控句柄，遵守 grants/namespace。
- 最小工具面（示例签名，按需扩展）：
  - 通用：`list_allowed_directories()`、`list_directory(path: str)`、`get_file_info(path: str)`、`read_text_file(path: str, range?: [int,int])`、`write_file(path: str, content: str)`、`edit_file(path: str, edits: list[dict])`、`delete_file(path: str)`
  - CSV：`csv_read(path: str)`、`csv_write(path: str, rows: list[list[str]])`
  - PDF：`pdf_read_text(path: str, pages?: list[int])`（可封装 `rag.PDFReader`）
  - XLSX：`xlsx_read_cells(path: str, range: str)`、`xlsx_write_cells(path: str, range: str, values: list[list[str]])`
  - PPTX：`pptx_read_outline(path: str)`、`pptx_write(path: str, slides_spec: list[dict])`
  - IMG（VLM 问答）：`image_qa(img_path: str, query: str | None)`（仅路径与问题，内部选择模型）

- 受控 FS 注册与 grants（片段）
```python
fs = DiskFileSystem()
handle = fs.create_handle([
  {"prefix": "/userinput/", "ops": {"list","file","read_file","read_re","read_binary"}},
  {"prefix": "<write-namespace>/", "ops": {"list","file","read_file","read_re","read_binary","write","delete"}},
])
svc = FileDomainService(handle)

for func, svc2 in fs.get_tools(svc):
    toolkit.register_tool_function(func, preset_kwargs={"service": svc2})
```

- ToolResponse 示例（节选）
```json
// read_text_file
{ "content": [{"type": "text", "text": "line1: ...\nline2: ..."}] }

// csv_read（以 JSON 文本承载行数据）
{ "content": [{"type": "text", "text": "{\"rows\":[[\"a\",\"b\"],[\"c\",\"d\"]]}"}] }

// pdf_read_text（预览片段）
{ "content": [{"type": "text", "text": "...extracted text preview... (pages:1,2,3)"}] }

// xlsx_write_cells（操作回执）
{ "content": [{"type": "text", "text": "wrote cells to A1:B2 @ <artifact_path>/book.xlsx"}] }

// image_qa（回答）
{ "content": [{"type": "text", "text": "There are 3 people"}] }
```

## 4) Agent Generate（基于要求生成产物）
- 目标：依据“前序总结 + 要求/大纲”，生成目标文件。尽量简单直接，不承担检索/解析。
- 最小工具面（示例签名）：
  - `generate_markdown(requirements: str, out_path: str, from_paths: list[str] | None)`
  - `generate_html(requirements: str, out_path: str, from_paths: list[str] | None)`
  - `generate_pptx(requirements: str, out_path: str, from_paths: list[str] | None)`
  - `generate_pdf(requirements: str | None, out_path: str, from_paths: list[str] | None)`

---

## 验收映射（规范 → 不变量 → 示例 → 测试要点）
## MsgHub 协作
- 建议链路：Search 推 URL → Browser 产 Markdown/抽取/下载 → File 做类型化解析 → Generate 汇总写入。
- 子代理内部默认关闭对外流式输出；Host 接收 ToolResponse 后转换为 Msg 并通过 MsgHub 广播。

## 安全与密钥
- 工具 schema 不得出现密钥/令牌/cookie。
- 外部客户端/会话通常通过 preset_kwargs 注入或环境变量读取；Search 工具例外（按调用就地初始化，不注入）。

---

## 验收映射（规范 → 不变量 → 示例 → 测试要点）

### A. Agent Search
- 规范
  - 工具：`search_google`/`search_bing`/`search_sogou`/`search_github`/`search_wiki`。
  - 入参：仅 `query: string`；Schema 字段集合等价于 `{query}`。
  - 返回：统一 `ToolResponse`；业务结果写入 `content`（可为 JSON 文本）；`metadata` 不增删。
  - 运行期：按调用就地初始化依赖（不注入），密钥不入 schema。
  - 后处理：对 `snippet` 做长度/词数截断，保持总输出可控。
- 不变量
  - JSON Schema 等价检查：`parameters.properties == {query}`，`required == ["query"]`。
  - 结果元素包含四字段，类型正确；`source` == 工具名。
  - 子代理静默（无 console/MsgHub 外泄）。
- 示例
  - `search_google({"query": "site:example.com roadmap"})` → 3–5 条结果。
- 测试要点
  - Schema 等价断言；输出结构断言；`source` 一致性；截断生效；工具白名单仅含上述 5 名称。

### B. Agent Browser — Viewer
- 规范
  - 工具：`viewer_fetch(url: string, query?: string)`、`http_download(url: string, save_path: string)`。
  - 行为：
    - `viewer_fetch`：`query` 为空 → HTML→Markdown；有值 → LLM 抽取，返回 `ToolResponse`（answer 文本置于 `content`、`metadata.evidence=[{url,span}]`）。
    - `http_download`：保存到受控工件路径（示例：`<artifact_path>/downloads/`）。
- 不变量
  - `viewer_fetch` Schema：`required == ["url"]`，`query` 可选；返回类型与分支一致。
  - `http_download` 非前缀写入必须失败。
- 示例
  - `viewer_fetch({"url": "https://example.com/docs"})` → Markdown。
  - `viewer_fetch({"url": "https://example.com/quote", "query": "current price"})` → answer+evidence。
- 测试要点
  - 分支覆盖；Markdown 非空；evidence 结构；下载路径前缀限制。

### C. Agent Browser — Automation
- 规范
  - 工具：`browser_navigate(url)`、`browser_snapshot()`、`browser_screenshot()`、`browser_click(selector)`、`browser_type(selector, text)`、`browser_wait(selector, timeout)`。
  - 生命周期钩子：`pre_reply`/`pre_reasoning`/`post_reasoning`/`post_acting`。
- 不变量
  - 快照/截图工具返回非空；钩子按顺序各触发一次；可完成“导航→等待→快照”。
- 示例
  - 股价序列：navigate → wait("#price") → snapshot → 交 `viewer_fetch(query)` 抽取。
- 测试要点
  - 钩子计数/顺序断言；工具序列执行；异常封装为 `unavailable=True`。

### D. Agent File（FileDomainService）
- 规范
  - 仅使用受控 FS 工具；通过 `preset_kwargs={"service": svc}` 注册；路径必须在授权命名空间。
  - 最小工具：
    - 通用：`list_allowed_directories`、`list_directory`、`get_file_info`、`read_text_file`、`write_file`、`edit_file`、`delete_file`。
    - CSV：`csv_read(path)`/`csv_write(path, rows)`；返回以 `ToolResponse.metadata.rows` 携带表格内容。
    - PDF：`pdf_read_text(path, pages?)`；返回 `metadata.pages` 和预览文本放入 `content`。
    - XLSX：`xlsx_read_cells(path, range)`/`xlsx_write_cells(path, range, values)`；返回 `metadata.cells`/写入范围。
    - PPTX：`pptx_read_outline(path)`/`pptx_write(path, slides_spec)`；返回 `metadata.slides`/目标路径。
    - IMG‑VLM：`image_qa(img_path, query?)`；答案置于 `content`，请求与路径在 `metadata`。
- 不变量
  - Schema 不暴露 `service`/句柄；写操作必须通过受控 FileDomainService 落地到授权命名空间；`/userinput/` 写入/删除必失败。
- 示例
  - `csv_read("/userinput/data.csv")`；`xlsx_write_cells("<artifact_path>/book.xlsx", "A1:B2", [["H","I"],["J","K"]])`。
- 测试要点
  - 前缀授权与白名单路径；写删策略；类型化返回结构（rows/cells/text 等）。

### E. Agent Generate
- 规范
  - 工具：
    - `generate_markdown(requirements, out_path, from_paths?)`
    - `generate_html(requirements, out_path, from_paths?)`
    - `generate_pptx(requirements, out_path, from_paths?)`
    - `generate_pdf(requirements, out_path, from_paths?)`
  - 行为：基于“前序总结 + 受控文件(from_paths 可选)”生成产物；仅写入受控路径。
- 不变量
  - Schema 字段集合等价于 `{requirements, out_path, from_paths?}`；写入必须走受控 FileDomainService，确保输出位于授权命名空间；成功后目标文件存在。
- 示例
  - `generate_markdown("总结 X", "<artifact_path>/report.md", ["<artifact_path>/notes.txt"])`。
- 测试要点
  - Schema 等价；写入前缀；from_paths 均能读取；产物存在性检查。

- ToolResponse 示例（generate_markdown）
```json
{
  "content": [{"type": "text", "text": "written: <artifact_path>/report.md"}],
  "metadata": {"artifact_path": "<artifact_path>/report.md", "from_paths": ["<artifact_path>/notes.txt"]}
}
```

---

## 参考示例的可复用实践（二次检查后汇总）

本节凝练自三处示例的可借鉴点，用于指导四类子代理的具体实现与落地验收。

### A. DeepResearch（examples/agent_deep_research）
- 编排循环：推理 → 工具调用 → 中间记忆更新；到迭代上限时进行总结回落。
- 中间记忆：将临时提示/中间结果与核心对话分离，避免污染长期上下文。
- MCP 接入：以 `StdIOStatefulClient` 建立会话，`Toolkit.register_mcp_client` 批量注册；所有客户端通过 `preset_kwargs` 注入，不出现在 JSON Schema。
- 后处理：对过长的网页/搜索结果进行截断或提纯，以降低 token 压力（可在 `postprocess_func` 中实现）。

### B. FileSystem Agent（examples/filesystem_agent）
- 沙箱化文件系统：`DiskFileSystem.create_handle(grants)` + `FileDomainService` 强制命名空间与操作权限；工具以 `preset_kwargs={"service": svc}` 方式注册。
- 提示与策略：在系统提示中明确“哪些命名空间只读、哪些可写”（由宿主授权），并要求路径不明确时先澄清。
- 稳定 I/O：将输入材料置于授权的只读命名空间，所有写入仅发生在可写命名空间（均由宿主授权）；演示使用 `fs.get_tools(service)` 批量注册。

### C. Browser Agent（examples/agent_browser）
- 生命周期钩子：`pre_reply`（首次导航）、`pre_reasoning`（快照），`post_reasoning`（清理观测）、`post_acting`（规范化输出）。
- 快照→文本：将页面状态转换为文本加入记忆，有助于 LLM 做出下一步决策；可平移至 `viewer_fetch` 的“无 query”分支。
- 噪声清理：在加入记忆前统一去除执行痕迹/冗余文本，降低干扰。

### D. 工具组与启停（通用）
- 使用 `Toolkit.create_tool_group/update_tool_groups` 定义/启用最小工具集合；在 `SubAgentSpec` 明确 allowlist，做到“一个不能少、一个不能多”。
- 元工具：可选择启用 `reset_equipped_tools` 作为交互式启停入口，但默认由 Host 决定。

### E. 结构化完成与委派上下文（通用）
- 若需要确定性收束，Host 可在必要时对完成函数调用 `set_extended_model` 绑定结构化输出模型。
- 使用 `delegation_context` 向子代理传递压缩上下文；子代理短期记忆设为一次性（ephemeral），调用后回收。

### F. 输出后处理与广播（通用）
- 对超长工具返回进行截断/分块；为关键字段补充引用与来源。
- 子代理内部默认禁用对外流；Host 将 `ToolResponse` 转换为 `Msg` 并通过 MsgHub 广播至其他子代理，实现 Search→Browser→File→Generate 的流水线衔接。

---

## 可借鉴模式（并集清单）

跨领域通用
- MCP 一等公民：优先使用 StdIO/HTTP 客户端，`Toolkit.register_mcp_client` 注册；客户端以 `preset_kwargs` 注入，Schema 不出现密钥。
- 工具分组 + 元工具：仅装备子代理所需工具；按 `SubAgentSpec` 维护 allowlist。
- Host 驱动的 MsgHub：子代理仅返回单个 `ToolResponse`；Host 负责转 `Msg` 并广播；子代理默认静默（无 console/队列）。
- Postprocess 清洗：在进入 LLM 前裁剪/规整工具输出（如网页、CSV、PDF），避免 token 爆炸。
- 结构化收束：需要确定性时，绑定 `set_extended_model` 到完成函数以收束结果。
- 只暴露最小参数：Schema 仅含业务必要字段；外部会话/驱动/密钥通过注入实现。
- 并行与审计：并行子任务用 `gather`；所有落盘在命名空间内，并在 metadata 标注来源/步骤。

来自 DeepResearch（examples/agent_deep_research）
- 编排循环：reasoning → tool calls → memory update；到达上限时 summarize。
- 中间记忆：将临时提示/中间结果放入独立缓冲，避免污染核心记忆。
- MCP 搜索接线：启动有状态客户端、批量注册工具、按名称调用。
- 跟随抽取：在搜索后做定向抽取；可复用到 Browser `viewer_fetch(query)` 分支。
- 输出整形：`truncate_search_result` 等策略减少 token 体积；网页抽取也复用同思路。

来自 FileSystem Agent（examples/filesystem_agent）
- 受控文件系统：`DiskFileSystem` + `create_handle(grants)` + `FileDomainService`；以 `preset_kwargs={service}` 注册工具。
- 路径策略入 Prompt：说明哪些命名空间只读、哪些可写（均由宿主授权）；在 File/Generate 代理复用。
- 可重复 I/O：将输入固定放入授权的只读空间，仅在授权可写空间写出。
- 工具批量导出：`fs.get_tools(service)` 一键注册，正好满足 File 代理需求。

来自 Browser Agent（examples/agent_browser）
- 带 Hook 的生命周期：`pre_reply` 首次导航、`pre_reasoning` 注入快照、`post_reasoning` 修剪、`post_acting` 清洗结果。
- 启动与快照：默认起始 URL 与“快照转文本”的做法可复用于 viewer 的无 query 分支。
- 内容清洗：去除执行痕迹/噪声再入记忆；Markdown 转换也需清洗。
- MCP 浏览器工具：`navigate/snapshot/click/type/wait` 最小集合适用于 Automation 模式。

应用到四类子代理
- agent_search：每来源一个工具（google/bing/sogou/github/wiki），签名固定 `query:str`；复用 DeepResearch 的 MCP 接线与截断策略。
- agent_browser：
  - Viewer：实现 `viewer_fetch(url, query|None)`；`query` 存在时走 LLM 抽取；提供 `http_download(url, save_path)`，下载到受控前缀。
  - Automation：沿用 BrowserAgent 的导航/快照/交互最小集合与 Hook。
- agent_file：仅用 FileDomainService 工具；为 csv/pdf/xlsx/pptx/img-VLM 增最小读写/问答；通过服务句柄注册；执行路径策略。
- agent_generate：简单生成器（markdown/html/pptx/pdf），参数包含 `requirements` 与 `from_paths`；输出写入由宿主授权的受控命名空间。

护栏与注意事项
- 禁止在 Schema 暴露 key/cookie；统一注入客户端或用环境变量。
- 禁用原始 OS 文本 I/O 工具，统一改用 FileDomainService 工具。
- 对每个子代理执行 allowlist 与“字段集合等价”契约（一个不能少/一个不能多）。
- 使用 `delegation_context` 传压缩输入；每次调用使用一次性短期记忆。
- 对网页/CSV/PDF 等大输出加后处理截断，避免上下文超限。
