# BocomADP API 文档

---

## 一、智能体工具管理

Base: `/api/agents/{agent_id}/tools`

### 1.1 获取工具列表

```
GET /api/agents/{agent_id}/tools
```

**Response 200:**

```json
{
  "agent_id": "85c542fd...",
  "tools": [
    {
      "name": "bash",
      "description": "Execute a bash command in the workspace sandbox...",
      "enabled": true,
      "toggleable": true
    },
    {
      "name": "echo",
      "description": "Echo the input text back to the caller.",
      "enabled": false,
      "toggleable": true
    }
  ],
  "mcps": [
    {
      "name": "browser-use",
      "description": "https://mcp.example.com/...",
      "enabled": true,
      "toggleable": true
    }
  ]
}
```

**`tools` 字段（内置工具 + 项目工具合并）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 工具唯一标识，用于启用/禁用接口 |
| `description` | string | 工具描述 |
| `enabled` | boolean | 当前是否启用 |
| `toggleable` | boolean | 是否可切换（目前均为 `true`） |

**`mcps` 字段（MCP 服务单独列出）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | MCP 服务名，与 `tools` 共用启用/禁用接口 |
| `description` | string | MCP 服务地址或描述 |
| `enabled` | boolean | 当前是否启用 |
| `toggleable` | boolean | 是否可切换 |

**常见内置工具：** bash, read, write, edit, glob, grep

**Response 404:** agent 不存在

---

### 1.2 启用工具

```
PUT /api/agents/{agent_id}/tools/{tool_name}
```

无请求体。

**Response 200:**

```json
{"ok": true}
```

**Response 404:** agent 或 tool 不存在

> 若该 agent 当前为「全部启用」状态（`enabled_tools` 为空），此操作是空操作，仍返回 200。

---

### 1.3 禁用工具

```
DELETE /api/agents/{agent_id}/tools/{tool_name}
```

无请求体。

**Response 200:**

```json
{"ok": true}
```

**Response 404:** agent 或 tool 不存在

> 若该 agent 当前为「全部启用」状态，首次禁用会自动将 `enabled_tools` 展开为「全部工具名 − 被禁用的那个」，之后就是普通的列表移除。

---

### 启用/禁用语义

| `enabled_tools` 值 | 含义 |
|---------------------|------|
| `[]`（空列表） | 全部工具启用 |
| `["echo", "bash"]` | 仅 `echo` 和 `bash` 启用，其余禁用 |

前端切换建议：调用 PUT/DELETE 后重新 GET 列表刷新 UI。

---

## 二、会话 Token 用量

```
GET /api/sessions/{session_id}/usage?agent_id=xxx&user_id=xxx
```

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `session_id` | string | ✅ (路径) | — | 会话 ID |
| `agent_id` | string | query | `"default"` | 智能体 ID |
| `user_id` | string | query | `"default"` | 用户 ID |

**Response 200:**

```json
{
  "session_id": "5998cfc7...",
  "agent_id": "85c542fd...",
  "input_tokens": 32965,
  "output_tokens": 379,
  "total_tokens": 33344,
  "message_count": 6
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `session_id` | string | 会话 ID |
| `agent_id` | string | 智能体 ID |
| `input_tokens` | number | 累计输入 token |
| `output_tokens` | number | 累计输出 token |
| `total_tokens` | number | 累计总 token（input + output） |
| `message_count` | number | 已持久化的消息总数 |

**Response 404:** session 不存在

**Response 503:** 存储后端不可用
