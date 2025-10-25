# TODO：SubAgent 工具落地（Search / Browser / File / Generate）

本清单将四类子代理的工具面“规范→不变量→示例→代码”落为可执行任务。严格遵守铁律：
- 零偏差契约：工具参数集合与文档完全等价（一个不能少、一个不能多）。
- schema 不含密钥/cookie/会话；外部客户端以 `preset_kwargs`/环境注入。
- 业务结果只写入 `ToolResponse.content`；`metadata` 属于框架保留集合，禁止增加/变更业务字段（保持缺省）。
- 全部写操作走受控 FileDomainService；写入仅限 Host 授权的可写前缀（由 FsHandle grants 决定）。
- 子代理执行期静默：不向控制台/MsgHub 直接输出；Host 统一广播。

---

## 自我检视（忏悔）与新增铁律（必须遵守）

- 忏悔：此前在 Search 工具上擅自加入 `client` 注入参数、输出使用 Markdown、未提供真实原样结果，只给“命令/OK”式表述，均违背了“零偏差契约/结果为先”的原则。

- 铁律·输入契约：
  - Search 类工具入参仅 `{query}`，严禁以任何形式新增参数（包括隐式注入对象出现在 schema/签名层）。
  - Search 运行期依赖（如 Playwright/WebKit）必须在工具内部“按调用创建→关闭”，生命周期局限于本次调用。

- 铁律·结果呈现：
  - E2E 必须打印“原样输出”（完整内容块），PR/评审须粘贴真实结果；严禁只报“功能 OK”或仅贴命令。
  - 未经 SOP 明示，禁止将结果渲染为 Markdown；默认使用纯文本；`metadata` 一律不写业务字段。

- 铁律·描述与导出：
  - 工具 docstring（description）必须英文、紧凑、面向模型决策；不得罗列参数/返回/实现细节。
  - 模块用 `__all__` 仅导出工具函数，内部 helper/常量不得外泄。

- 铁律·一致性与阻断：
  - 文档→todo→代码→测试严格同步；任何偏离需先改文档再改代码，并补充验收项。
  - 遇执行阻断（依赖、网络等）必须立即上报“具体错误+环境+重现步骤”，优先解除阻断后再交付结果。

---

## 0) 依赖与参考
- 规范：docs/example/subagent_design.md（当前版本）。
- FS 与授权：docs/filesystem/SOP.md；示例 examples/filesystem_agent。
- Browser 生命周期与 MCP：examples/agent_browser。
- DeepResearch 编排与截断：examples/agent_deep_research。

---

## 1) agent_search（多来源，一源一工具）

目标：根据 `query:str` 聚合多源搜索结果，并将详细文本写入受控文件后返回逻辑路径。

必备工具（参数契约）
- `search_google(query: str)`
- `search_bing(query: str)`
- `search_sogou(query: str)`
- 私域：`search_github(query: str)`、`search_wiki(query: str)`

实施步骤
1. 工具草案与 schema：为上述 5 个函数生成 JSON Schema（`properties` 仅含 `query`；`required == ["query"]`），并保持 docstring 简洁明确。
2. 运行期就地初始化：工具内部按调用创建并关闭所需运行时（如 Playwright WebKit 或 HTTP 客户端）；不通过 `preset_kwargs` 注入外部依赖；schema 仅暴露 `query`。
3. 结果整形与截断：在工具内将搜索结果转为纯文本列表（title/url/snippet）；大文本按词数截断；文件写入保持纯文本。
4. 注册与分组：`Toolkit.register_tool_function`；置入 `search` 组或直接 `basic`（按子代理 allowlist）。

状态（Search 工具与测试）
- [x] search_bing 工具实现与 E2E（tests/search/test_bing_e2e.py）
- [x] search_sogou 工具实现与 E2E（tests/search/test_sogou_e2e.py）
- [x] search_github 工具实现与 E2E（tests/search/test_github_e2e.py）
- [x] search_wiki 工具实现与 E2E（tests/search/test_wiki_e2e.py）
- [x] Schema 等价契约测试（tests/search/test_search_schema_contracts.py）
- [x] SearchAgent 子代理输出工件或聚合文本 & Host 读取反馈（tests/agent/test_search_agent_integration.py）
- [ ] SearchAgent InputModel 契约测试（验证 `model_json_schema` 与参数校验一致）
- [ ] search_google（同页/无持久化约束下受 consent/反爬阻断，详见“已知阻塞”）

### Search Agent（基于 SubAgentBase）

目标：实现 `SearchAgent(SubAgentBase)`，允许在子代理内部使用搜索工具（bing/sogou/github/wiki）交叉检索，并通过继承的 FileDomainService 在授权命名空间内落地工件（如适用），或直接返回文本摘要；子代理对外通过 Pydantic `InputModel` 定义请求契约。提供一个仅装配该子代理的 Host，用以端到端验证“Host → 子代理 → 搜索工具 → 返回文件路径 → Host 读取”的完整链路。

实施步骤
1. 代码位置：`src/agentscope/agent/_search_agent.py`
   - 定义 `class SearchAgent(SubAgentBase)` 并显式覆写 `InputModel = SearchQuery`
     ```python
     class SearchQuery(BaseModel):
         query: str
         context: str | None = None
     ```
   - 在构造时创建独立 `Toolkit()` 并批量注册传入的搜索工具（`spec.tools`），例如：`search_bing`、`search_sogou`、`search_github`、`search_wiki`（暂不包含 google）。
   - 继承骨架约束：关闭对外流（console/msg queue 禁用），不修改 metadata，使用受控短期 memory（ephemeral）。
2. 行为：
   - `reply(self, input_obj: SearchQuery, ...)` 中根据 `input_obj.query` 调度所有 `search_*` 工具，结合 `input_obj.context` 追加到文件头或结果摘要；如宿主授权写入，则将内容保存为受控工件（例如 `result_<timestamp>.txt`）并返回其逻辑路径；若无法落盘，则退化为返回聚合文本。
3. Host 装配示例（测试内实现即可）：
   - 使用 `ReActAgent` 作为 Host，注入 FileDomainService（`DiskFileSystem.create_handle` + `FileDomainService`），并注册受控文件工具（`svc.tools()`）。
   - 通过 `make_subagent_tool(SearchAgent, spec)` 注册子代理工具，`spec.tools` 指定 `search_bing`、`search_sogou`、`search_github`、`search_wiki`；注册时校验 `SearchQuery.model_json_schema()` 与工具描述一致。
   - Host system prompt 提醒“委派搜索并读取返回的文件路径”；SearchAgent system prompt 提醒“汇总结果写入文件，仅返回路径”。
4. 触发与输出：发送用户消息（如“搜索谁是 speed … 多维度结果”）；子代理调用各搜索工具写文件，Host 使用 `read_text_file` 读取路径并完成总结。

不变量（验收）
- SearchAgent 必须通过 `InputModel` 声明入参，`delegate`/`reply` 仅接受模型实例；禁止回退到 dict 或任何单字段临时契约。
   - `model_json_schema()` 显式列出字段（`query`、`context`），并由 `make_subagent_tool` 注册；测试需断言 schema 与 InputModel 等价。
- `ToolResponse.content` 仅返回文本块；metadata 保持缺省。
- 若产生文件：必须通过受控 FileDomainService 写入授权命名空间并返回逻辑路径；亦可不落盘，直接返回简明文本预览。
- 多个 `search_*` 工具均需尝试执行；成功结果按工具名分节记录在文件内容中，保障“多维度”视角；单源失败不得影响整体流程。

已知阻塞（Google 搜索，当前阶段暂缓）
- 约束前提：同页完成（不可多页跳转）、不持久化状态/同意（不得写入 storage_state/cookie 文件）、优先 headless 移动端。
- 现象：首次无状态会话下，Google 常呈现同意/隐私页或进入反自动化保护，导致结果锚点未渲染，抓取为空（<no results>）。
- 已尝试：
  - 原生 Playwright WebKit：同页点击常见同意按钮 + 选择器回退 → 仍不稳定。
  - patchright(WebKit)：本机环境 new_page 即断开（ProtocolError），无法进入页面。
  - 本机 Chrome（CDP/系统通道）：同页流程仍受 consent/反爬影响，结果为空。
- 结论：在不放宽上述约束前，无法稳定获得 ≥1 条真实结果。若后续允许，可放宽为“同页内可处理 consent 并允许一次性重载”或改用合规 API（仍保持 schema 仅 `{query}`）。

---

## 2) agent_browser

### A) Viewer（不操控浏览器）
工具与参数
- `viewer_fetch(url: str, query: str | None)`
- `http_download(url: str, save_path: str)`（写入 Host 授权的可写前缀；具体路径由 grants 决定）

实施步骤
1. http 客户端注入：`preset_kwargs={"http": http_client}`；工具内实现抓取与字符集/压缩处理。
2. 分支逻辑：
   - `query is None`：HTML→Markdown 转换，写入单个 TextBlock。
   - `query provided`：基于已抓取正文，调用注入的 LLM 提取答案；答案与引用以 Markdown 文本写入 content（列表“Evidence: - url :: span”）。
3. 下载：校验 `save_path` 必须在受控前缀；成功后在 content 输出“Downloaded: <path> (size=…)”。
4. 截断与清洗：复用 Browser 示例中的清洗策略（去噪/裁剪）。

不变量（验收）
- `viewer_fetch`：schema `required == ["url"]`；`query` 可选；content 在两种分支均非空；不修改 metadata。
- `http_download`：拒绝非前缀写入；content 明示写入路径与大小。

### B) Automation（可操控浏览器）
工具与参数（最小集合）
- `browser_navigate(url: str)`、`browser_snapshot()`、`browser_screenshot()`
- `browser_click(selector: str)`、`browser_type(selector: str, text: str)`、`browser_wait(selector: str, timeout: int)`

实施步骤
1. 通过 MCP 客户端接入 Playwright；`register_mcp_client` 批量注册工具；或以 `preset_kwargs` 注入已连接客户端。
2. 生命周期钩子（Host）：`pre_reply` 首次导航、`pre_reasoning` 注入快照、`post_reasoning` 修剪观测、`post_acting` 清洗输出。
3. content 输出：各工具返回简明回执（文本），如“navigated <url>”、“clicked <selector>”。

不变量（验收）
- 能完成“navigate→wait→snapshot”序列；所有返回仅在 content；子代理静默。

---

## 3) agent_file（全部走 FileDomainService）

通用工具（复用现有受控 FS 工具）
- `list_allowed_directories`、`list_directory(path)`、`get_file_info(path)`、`read_text_file(path, range?)`、`write_file(path, content)`、`edit_file(path, edits)`、`delete_file(path)`。

类型专项（最小能力）
- CSV：`csv_read(path)`、`csv_write(path, rows)`（content 展示表格/JSON 文本预览）。
- PDF：`pdf_read_text(path, pages?)`（content 为预览片段）。
- XLSX：`xlsx_read_cells(path, range)`、`xlsx_write_cells(path, range, values)`（content 为范围/表格预览或写入回执）。
- PPTX：`pptx_read_outline(path)`、`pptx_write(path, slides_spec)`（content 为提纲/写入回执）。
- IMG‑VLM：`image_qa(img_path, query?)`（content 为回答）。

实施步骤
1. 受控句柄：以 `DiskFileSystem.create_handle(grants)` + `FileDomainService` 注入服务对象，所有新工具以 `preset_kwargs={"service": svc}` 注册；禁止直触 OS 路径。
2. 第三方库封装：按需注入 `pdf_reader/xlsx/pptx/vlm` 客户端，均通过 `preset_kwargs`；返回预览/回执文本至 content。
3. 路径校验：所有入参 path 必须在授权命名空间；写入仅限 Host 授权的可写前缀（由 FsHandle grants 决定）。

补充说明（继承与自动装配）
- 若子代理继承到 `filesystem_service`，骨架在构造/导出时会自动装配受控文件工具全集（通过 `preset_kwargs={"service": …}` 注入），无需在 `spec.tools` 中逐一列出。

不变量（验收）
- schema 不暴露 `service`/句柄；content 非空；写操作仅在受控前缀；禁止原始 OS 文本 I/O。
- FsHandle.describe_grants_markdown 渲染多前缀行。
- FileDomainService.describe_permissions_markdown 与 handle 输出一致。
- fs_describe_permissions_markdown 工具经 Toolkit 返回相同文本。
- 继承到 `filesystem_service` 时，子代理 Toolkit 自动包含受控 FS 工具（如 list_directory/get_file_info/read_text_file/write_file/edit_file/delete_file），且 schema 无 `service` 字段。

---

## 4) agent_generate（基于总结与受控文件生成）

工具（统一签名）
- `generate_markdown(requirements: str, out_path: str, from_paths: list[str] | None)`
- `generate_html(requirements: str, out_path: str, from_paths: list[str] | None)`
- `generate_pptx(requirements: str, out_path: str, from_paths: list[str] | None)`
- `generate_pdf(requirements: str | None, out_path: str, from_paths: list[str] | None)`

实施步骤
1. 模板/渲染：基于 `requirements` 与（可选）`from_paths` 渲染产物；仅写入受控前缀；content 输出“Generated <fmt> at <out_path>”。
2. 注入：如需渲染器/转换器（markdown→html、html→pdf 等），以 `preset_kwargs` 注入；schema 不暴露实现细节。

不变量（验收）
- 参数集合与顺序严格一致；content 明示产物路径；不改写 metadata；写入仅在受控前缀。

---

## 5) Host 编排与广播（协同）
实施步骤
1. Host 将四类工具按分组注册并设置 allowlist；子代理只暴露本组工具。
2. 执行链：Search→Browser(Viewer/Automation)→File→Generate；各步返回的 `ToolResponse` 由 Host 转 `Msg` 并通过 MsgHub 广播给后续代理。
3. 截断与清洗：在 postprocess 中裁剪长文本与噪音；保证“可读 + 可控长度”。

不变量（验收）
- 子代理不直接广播；广播顺序与链路可观察；所有工具产出可落盘/可追溯。

---

## 6) 验收映射（示例）

| 类别 | 不变量 | 测试用例建议 |
| --- | --- | --- |
| Search | schema 等价 `{query}`；content 非空；无写入 | tests/search/test_google_schema.py, test_google_output_preview.py |
| Browser.Viewer | `viewer_fetch` 分支覆盖；`http_download` 前缀校验 | tests/browser/test_viewer_fetch.py, test_http_download_prefix.py |
| Browser.Automation | 最小序列可执行；hook 顺序 | tests/browser/test_automation_sequence.py, test_automation_hooks.py |
| File | 受控前缀写入；类型工具 content 预览 | tests/file/test_csv_pdf_xlsx_pptx_img.py |
| Generate | 统一签名与写入前缀；回执文本 | tests/generate/test_generators.py |
| 协同 | Search→Browser→File→Generate 广播链路 | tests/e2e/test_pipeline_broadcast.py |

---

## 7) 风险与护栏
- 禁止在 schema 中暴露密钥/会话；仅用注入/环境变量。
- 禁用原始 OS 文本 I/O 工具；统一通过 FileDomainService 工具访问。
- 保持 `metadata` 不变；业务数据只写入 content。
- 每次提交前跑契约测试（字段集合等价/E2E 广播/前缀校验）。

---

## 8) 目录树（参考范围）

本任务涉及的主要目录/文件（精简）：

```
docs/
  example/
    subagent_design.md                 # 设计规范与最小工具面（当前任务基线）
  filesystem/
    SOP.md
  tool/
    SOP.md

examples/
  agent_browser/                       # 浏览器自动化示例（生命周期钩子、Playwright MCP）
  filesystem_agent/                    # 受控文件系统示例（DiskFileSystem + FileDomainService）
  agent_deep_research/                 # 编排循环、截断与 MCP 搜索 wiring

src/agentscope/
  agent/
    _subagent_base.py                 # 子代理骨架（静默、委派上下文等）
    _subagent_tool.py                 # 注册工厂（allowlist、健康检查）
    _react_agent.py                   # Host 编排、_acting、finish 结构化绑定
  filesystem/
    _tools.py                         # 受控 FS 工具（list/read/write/edit/delete）
    _service.py                       # FileDomainService（命名空间/授权）
    _disk.py                          # DiskFileSystem（物理映射）
  tool/
    _toolkit.py                       # 工具注册/调用/分组与 postprocess
    _response.py                      # ToolResponse（统一返回）
    _multi_modality/                  # 生成/识别示例（按需参考）
    _coding/                          # execute_python_code（慎用，默认不暴露）
    _web/                             # 新增：Search/Viewer/Download 工具实现
      _search.py                      # search_google/bing/sogou/github/wiki（仅 query）
      _viewer.py                      # viewer_fetch(url, query|None) / http_download(url, save_path)
    _filetypes/                       # 新增：受控 FS 的类型工具
      _csv.py                         # csv_read/csv_write（content 预览/回执）
      _pdf.py                         # pdf_read_text（预览片段）
      _xlsx.py                        # xlsx_read_cells/xlsx_write_cells
      _pptx.py                        # pptx_read_outline/pptx_write
      _img_vlm.py                     # image_qa(img_path, query|None)
    _generate/                        # 新增：生成器工具
      _markdown.py                    # generate_markdown(requirements, out_path, from_paths|None)
      _html.py                        # generate_html(requirements, out_path, from_paths|None)
      _pptx.py                        # generate_pptx(requirements, out_path, from_paths|None)
      _pdf.py                         # generate_pdf(requirements|None, out_path, from_paths|None)

tests/
  agent/                              # 子代理相关现有测试（可参考结构）
  filesystem/                         # FS 授权/工具集成测试
  search/                             # 新增：各来源 schema/输出预览
    test_google_schema.py
    test_bing_schema.py
    test_sogou_schema.py
    test_github_schema.py
    test_wiki_schema.py
  browser/                            # 新增：viewer/automation 套件
    test_viewer_fetch.py
    test_http_download_prefix.py
    test_automation_sequence.py
    test_automation_hooks.py
  filetypes/                          # 新增：csv/pdf/xlsx/pptx/img 工具
    test_csv_tools.py
    test_pdf_tools.py
    test_xlsx_tools.py
    test_pptx_tools.py
    test_image_vlm_tool.py
  generate/                           # 新增：生成器工具
    test_generators.py
  e2e/
    test_pipeline_broadcast.py        # 新增：Search→Browser→File→Generate 广播链路
```
