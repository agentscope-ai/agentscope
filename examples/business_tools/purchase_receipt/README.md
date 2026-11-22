# 用原客户端验收采购入库单工具

生成时提供元境 ERP 源码目录、OpenAPI JSON 和业务名称“采购入库单”。AgentScope 从 API 找到新增和详情操作，再沿客户端的导入、保存事件、请求体和计算函数生成工具。采购入库的字段名、页面路径和测试预期均由验收程序在生成之后使用。

当前验收范围是**新增采购入库草稿并按 ID 回查**。同一份草稿分别经过原客户端和生成工具，比较允许或拒绝保存、发出的完整请求，以及 ERP 最终回查的业务数据。

## 运行验收

在 AgentScope 仓库根目录运行。ERP 源码可以是整个仓库或客户端的 src 目录。Node 运行时依赖与生成依赖一起安装。

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e .
pnpm --dir src/agentscope/business install --frozen-lockfile

ERP=../yuanjing-erp-integration
API="$ERP/docs/erp-api/openapi/pc-erp.openapi.json"
CASE=examples/business_tools/purchase_receipt
OUT=build/business-tools/purchase-receipt.json
mkdir -p build/business-tools
.venv/bin/python "$CASE/trial.py" --source "$ERP" --openapi "$API" --output "$OUT"
```

脚本通过 Toolkit 调用发现工具，随后读取 `client_cases.json`。它核对原页面的 SHA-256，逐字段比较 HTTP 方法、路径和完整 JSON。字段缺失、多余、值不同或明细顺序变化都会使验收失败，并给出第一个差异的位置。每个成功样本也核对回查路径。每个失败样本核对原客户端是否拦下保存及实际提示。

| 同一份输入的变化 | 原客户端与工具的结果 |
| --- | --- |
| 首行应收 10 片、实收 9 片、单价 3、装车费 2，第二行应收 4 片、实收 3 片、单价 5、装车费 1 | 行应付 32 和 21，表头货物总额 53 |
| 第二行应收改为 5 片 | 第二行应付 26，表头 58 |
| 第二行单价改为 5.00125 或 6 | 表头分别为 53.01 或 57 |
| 加入破损、运费、入筐批次、工人和叉车计件 | 明细、费用、批次单价、工人及叉车分摊和表头均纳入全量比较 |
| 加入付款金额 5、免付金额 1 的付款子单 | 保存请求包含付款子单，相关表头金额一致 |
| 清空明细、付款和免付同时为零、批次缺少色号 | 分别在保存前拒绝，提示指出对应明细或字段 |
| 单据已结案、已审核、已作废 | 三组分别拒绝保存 |
| 在配置中将表头到货说明、采购明细验收依据和批次存放位置设为保存必填 | 顺次补齐前一项，两边依次提示同一处缺填。三项填完后完整请求一致 |

原页面的对照样本由浏览器执行所得。测试在新建页中装入同一份编辑数据，运行原始行计算并调用实际保存按钮绑定的处理函数。固定主数据和 HTTP 响应使离线验收可重复。附件草稿键在分流前由页面创建，完整请求对比也包含这些键。

### 重新采集原客户端

在受信任的原 ERP 前端安装其锁定依赖。指定该前端以及本机 Chromium，脚本会启动只监听本机的 Vite 服务，通过内存插件读取编辑状态，拦截测试 API 请求，结束时关闭页面和服务。采集器属于独立的采购入库验收用例。生成工具不导入采集器。

```bash
node "$CASE/capture.cjs" --client "$ERP/frontend-user" --chromium "$CHROMIUM" \
  --output build/business-tools/client-reference.json
.venv/bin/python "$CASE/trial.py" --source "$ERP" --openapi "$API" \
  --reference build/business-tools/client-reference.json
```

### 在测试 ERP 对比落库结果

将 `BUSINESS_API_TOKEN` 设为已登录的测试账号令牌，`ERP_API_BASE` 设为该测试账套的 API 根地址（含 `/api`）。`--compare-persisted` 对改单价、批次计件和付款三组分别创建两张草稿，共六张。脚本逐张输出 ID，核对自己生成的主键、单号和子单外键，然后比较两张单的全部回查业务字段。两次创建各自的服务器审计时间单独校验格式。

```bash
.venv/bin/python "$CASE/trial.py" --source "$ERP" --openapi "$API" \
  --base-url "$ERP_API_BASE" --compare-persisted \
  --results build/business-tools/purchase-receipt-parity.json
```

首次真实验证使用独立的本地 Java ERP 和临时 MySQL 账套。额外一次浏览器直连验证加载了该账套实际的料品、公司、团队和配置，原页面保存了含批次、工人、叉车费用的草稿。工具从 API 自行读取主数据，只传业务表头和明细也得到了相同的回查业务值。精简业务输入走 API 时，ERP 会填入创建人及时间；完整编辑快照允许进一步核对整个原始请求。

原有的 `--submit` 可创建单张示例草稿，`--read-id ID` 可单独复核这张单。其他账套可通过 `--input` 提供采用其主数据 ID 的两行输入。成对的落库验收使用 `--reference` 中的样本，换账套可用 `capture.cjs --input` 重新采集。

### 验收账套自定义必填

在临时测试账套的字段说明中设置下表。前三个字段设为保存必填，并对测试账号可见、可编辑。最后一个字段只设为审核必填，验收时保持空值。

| 模块 | 字段 | 测试标签 |
| --- | --- | --- |
| `purchase.order` | `remark` | 到货说明 |
| `purchase.order.detail` | `remark1` | 验收依据 |
| `purchase.order.batch` | `warehouseLocation` | 存放位置 |
| `purchase.order.detail` | `remark2` | 仅审核填写 |

用同一个测试 token 和临时账套分别跑原客户端和生成工具。浏览器命令顺次尝试三组缺填样本并保存一张补齐的草稿。第二条命令通过 Toolkit 重放相同四组输入，检查三组拒绝的提示和 HTTP 调用，再创建一张工具草稿并对比两张单据的回查值。`ERP_API_BASE` 使用 `http://127.0.0.1:<port>/api`。

```bash
node "$CASE/capture.cjs" --client "$ERP/frontend-user" --chromium "$CHROMIUM" \
  --base-url "$ERP_API_BASE" --output build/business-tools/required-reference.json
.venv/bin/python "$CASE/trial.py" --source "$ERP" --openapi "$API" \
  --base-url "$ERP_API_BASE" --required-reference build/business-tools/required-reference.json \
  --results build/business-tools/purchase-receipt-required-parity.json
```

## 执行依据与适用范围

生成物记录保存事件、请求构造、逐行计算、保存前校验、主数据读取和对应的源码位置及指纹。规则在独立的 QuickJS 运行时执行。采购入库案例复用原始行计算、表头汇总和请求构造函数，保留源客户端的舍入、计件分摊和批次清理行为。在线调用会按已识别的客户端读取请求获取主数据；离线试算可以传入同一份编辑快照的 `editorState`。

保存时使用当前账号重新获取账套的字段说明、列设置、字段权限、隐藏字段和只读字段。发现过程从保存事件追到原始明细校验，再按表头组件提供的规则计算必填提示。采购明细、批次、工人、叉车和付款沿用原始校验对有效行、字段权限和字段别名的处理。在线调用从 ERP 重新读取这些配置，离线验收使用原客户端采集时的配置快照。

`discovery.editor.requiresClient` 记录编辑控件提交仍由页面负责。工具面向已整理成业务字段的草稿。覆盖范围为新增草稿保存、保存时必填及回查。

```bash
.venv/bin/python -m agentscope.business generate "$OUT" --business 采购入库单 --source "$ERP" --openapi "$API"
.venv/bin/python -m agentscope.business inspect "$OUT"
.venv/bin/python -m agentscope.business check "$OUT" --source "$ERP" --openapi "$API"
```

接入 Agent 时将 `discovery_tool(source, openapi, output)` 注册到 Toolkit，模型只需提供 `business="采购入库单"`。用 `as_tool(bundle["tools"]["save"], base_url, token)` 和 `as_tool(bundle["tools"]["detail"], base_url, token)` 注册生成的两个业务工具。

已有的离线工作流内含生成时的行计算代码。用同一份源码和 OpenAPI 重新执行上面的 `generate` 命令，即可采用原客户端的保存分支和调用顺序。

使用已发布的 ERP 密码登录适配器时，创建通用查询注册器的宿主应传入 `get_read_auth=purchase_auth.registered_read_auth`。已注册查询会沿用当前会话的私密登录弹窗，并在登录成功后按原参数重试一次 GET。

文字聊天会在当前用户和 Agent 已发布的工具中，按业务名称、工具描述和参数名检索。员工可以直接说“查最近的采购入库单”或“按操作 ID 回查采购入库单”。助手调用 `search_business_tools` 后，下一轮会看到最多三个匹配工具的完整参数。再次检索会替换本会话的选择。检索只读取本地目录。实际查询、保存确认和回查由对应的业务工具执行。

接入 `SaveBusinessRegistry` 时，由宿主传入与保存连接对应的 `target_label`。默认权限模式会在验收前向用户显示该账套名称和将创建的两张草稿。省略账套名称时只执行预览。旧版验收记录可继续回查，重新验收通过后恢复保存。多 worker 部署应共用注册目录，并为私密登录配置共享消息总线。

已发布的草稿保存工具在确认框中显示账套和本次操作 ID。刷新、重复确认或换 worker 时沿用同一 ID。宿主须为不同账套配置唯一且稳定的 `target_label`，并将页面操作原样传给 `capture_client.cjs`。浏览器收到保存回执后，会先将单据 ID 写入共享注册目录，再由服务端回查详情。

恢复时将 `operation_id` 传给该业务的回查工具。返回 `saved_needs_readback` 表示已取得单据 ID，可再次回查。返回 `needs_reconciliation` 表示保存请求可能已送达，须在原账套核对。另行确认新增草稿会产生新的操作 ID。
