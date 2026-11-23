# 对接业务系统的保存与回查

`SaveBusinessRegistry` 将确认操作绑定到用户、目标和草稿。接入新系统时，用稳定的 `target_label` 标识租户或工作空间，以 `tool_prefix` 区分工具名称。已有 ERP 接入沿用默认前缀和存储路径。

宿主提供 `capture` 执行客户端操作、`read_detail(route, record_id)` 读取实际记录，以及可选的 `readback_view(plan, detail)` 做字段映射。回执支持正整数和非空字符串，编号 `"00042"` 会以字符串原样回传。映射返回记录 ID 和业务字段，字段名、类型和单位应与员工确认时一致。缺失的字段应从映射中省略。以下示例将工单系统的嵌套响应映射到确认字段。

```python
def ticket_view(plan, detail):
    ticket = detail["ticket"]
    properties = ticket["attributes"]
    fields = {"title": properties["subject"]} if "subject" in properties else {}
    return ticket["key"], fields


registry = SaveBusinessRegistry(
    source, openapi, storage, capture, read_detail,
    target_label="客服工作区 A", tool_prefix="support", readback_view=ticket_view,
)
```

在已确认的保存中，适配器一旦收到目标系统的记录 ID，即可用 `agentscope.business.write_save_receipt(actions[-1]["saveReceipt"], record_id)` 将其落盘，再等待后续 I/O。这样即使适配器进程退出，恢复时也能直接按 ID 回查。适配器返回的 `detail` 应来自实际回查。未提供详情时，注册器会调用 `read_detail`。

| 员工保存结果 | 含义 |
| --- | --- |
| `denied` | 员工已拒绝本次确认。可以用原操作 ID 查询决定，再次提交需要发起新的确认。 |
| `saved` | 记录 ID 与回执一致，且所有确认字段均核对通过。 |
| `saved_needs_review` | 已取得回执，字段缺失、内容不同或历史操作缺少可核对的草稿。存在字段差异时，`differing_fields` 列出对应名称。 |
| `saved_needs_readback` | 已取得回执，尚未读到可验证的对应记录。可用原操作 ID 再次回查。 |
| `needs_reconciliation` | 请求可能已送达，尚无可靠回执，需要在原系统核实。 |

发布工具的验收会分别验证原客户端和生成工具的写入。遇到不完整回执后，重复验收只回查已有 ID。仅当适配器明确返回 `attempted: false` 且没有记录 ID，才允许重新发起原写入。缺少任一份验收证据时，工具保持未发布。

## 在聊天中打开刚才列表的一行

宿主为每个业务绑定 Vue 列表页、行点击处理函数，以及已授权的列表和详情 GET。Agent 的源码检查工具会沿着表格插槽、点击函数、本地函数调用和请求封装找到详情路径参数的来源，返回文件、行号和源码版本。字段可以嵌套，多个路径参数可以分别取自不同字段。缺少映射或详情依赖尚未支持的请求配置时，工具会给出缺失证据。

采购入库单可在宿主的会话工具工厂中使用以下绑定。三个工具分别用于检查源码、查询最近记录，以及打开先前展示的一行。

```python
from agentscope.business import RowActionSource
from agentscope.tool import Toolkit
from examples.business_tools.purchase_receipt.recent import purchase_receipt_row_tools

source = RowActionSource(
    erp_frontend_src, openapi_file,
    "views/purchase/PurchaseOrderView.vue", "handleEdit",
    "/purchase/order/page", "/purchase/order/{id}",
)
rows = purchase_receipt_row_tools(
    source, private_state_dir / "row-selections.sqlite",
    scope=current_row_scope, can_read=can_read, client_for=erp_client_for,
)
# 在当前会话的 Toolkit 中安装这组工具，取代旧的单独列表工具。
toolkit = Toolkit(tools=rows.tools())
```

`current_row_scope()` 从宿主登录态取得系统、账套、用户和会话，返回 `RowScope`。`can_read(scope, operation)` 检查当前的源码读取权限（`source`）或指定 GET 的权限。`erp_client_for(scope)` 打开该身份和账套对应的 HTTP 客户端，业务后端继续检查记录级权限。其他系统用 `RowDetailTools` 提供自己的列表、展示字段及详情读取函数；列表回调交付与原客户端一致的行对象。

一次查询产生一个 `list_ref`，返回的记录按原顺序从 1 开始编号。详情工具接受该引用和行号。SQLite 只保留下次调用所需的参数，多个进程共用同一份私有数据库即可恢复原选择。引用有效期为 24 小时。源码或接口契约变化后，员工可以重新列出记录取得新引用。

手机和桌面聊天共用列表卡。适配器可以通过 `title` 和 `field_labels` 提供标题和列名。点击旧卡的一行，或在该卡的“回复这张卡片”中输入“打开第二张”，都会在当前聊天中产生一条回复。服务端按原消息和工具调用找回已保存的列表，用当前权限读取详情。过期后需要重新查询。
