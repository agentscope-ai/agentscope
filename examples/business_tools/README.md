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
| `saved` | 记录 ID 与回执一致，且所有确认字段均核对通过。 |
| `saved_needs_review` | 已取得回执，字段缺失、内容不同或历史操作缺少可核对的草稿。存在字段差异时，`differing_fields` 列出对应名称。 |
| `saved_needs_readback` | 已取得回执，尚未读到可验证的对应记录。可用原操作 ID 再次回查。 |
| `needs_reconciliation` | 请求可能已送达，尚无可靠回执，需要在原系统核实。 |

发布工具的验收会分别验证原客户端和生成工具的写入。遇到不完整回执后，重复验收只回查已有 ID。仅当适配器明确返回 `attempted: false` 且没有记录 ID，才允许重新发起原写入。缺少任一份验收证据时，工具保持未发布。
