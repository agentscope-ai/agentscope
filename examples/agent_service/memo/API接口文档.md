# BocomADP 接口文档（curl 速查）

- 网关地址：`http://192.168.0.106`（nginx 80 端口，统一入口）
- 转发规则：`/api/xxx` → 剥掉 `/api` 前缀 → `agentscope-service:8000/xxx`（`Docker-agentscope/nginx/nginx.conf`）
- 直连方式（绕过网关）：`http://192.168.0.106:8000` + 服务端原始路径（**不带** `/api` 前缀）
- 路径参数 `{...}` 需替换为实际 ID

## 0. 健康检查

```bash
curl http://192.168.0.106/api/healthz
curl http://192.168.0.106/api/readyz
curl http://192.168.0.106/api/platform/health
curl http://192.168.0.106/api/stats/ping
curl http://192.168.0.106/api/stats/storage
```

## 1. DeerFlow 风格场景对话（`/api/threads`）

> 2026-08 起替代已删除的 `/api/chat/run` + `/api/chat/stop`。执行引擎复用原生
> `ChatService`（agent 构建 / 模型 / 工具 / 审计中间件与原生 `/chat/` 完全一致），
> 输出 deer-flow 2.0（LangGraph Platform）SSE 协议：`event:` → `data:` → `id:`
> 帧 + `Last-Event-ID` 断线续传 + `Content-Location` 响应头。
>
> - `thread_id` 即原生 `session_id`（同一资源）；`agent_id` 选场景：
>   default / customer_service / risk_control
> - 所有端点需携带 `x-user-id` 请求头
> - 同 session 已有活跃 run 时再次创建 → `409 Conflict`

```bash
# ① 创建 run + SSE 流式（-N 实时输出；响应头 Content-Location 携带 run_id）
curl -N -X POST http://192.168.0.106/api/threads/t1/runs/stream \
  -H 'Content-Type: application/json' -H 'x-user-id: u1' \
  -d '{"agent_id":"customer_service","input":"你好，帮我查一下余额"}'
# 帧序列：event: metadata（首帧）→ event: messages / custom（增量）→ event: end（结束）
# 帧格式：event: <名> \n data: <JSON> \n id: <游标>（Last-Event-ID 断线续传用）

# ② 创建 run + 阻塞等待（返回终态 JSON：run_id / thread_id / status / error）
curl -X POST http://192.168.0.106/api/threads/t1/runs/wait \
  -H 'Content-Type: application/json' -H 'x-user-id: u1' \
  -d '{"agent_id":"customer_service","input":"简单回答：1+1=?"}'

# ③ join 已有 run（回放全部事件；带 Last-Event-ID 则从断点续传）
curl -N http://192.168.0.106/api/threads/t1/runs/{run_id}/stream \
  -H 'x-user-id: u1'
curl -N http://192.168.0.106/api/threads/t1/runs/{run_id}/stream \
  -H 'x-user-id: u1' -H 'Last-Event-ID: 1-0'

# ④ 取消 run（映射原生 session 级 interrupt；join 方随后收到 end 帧收敛）
curl -X POST http://192.168.0.106/api/threads/t1/runs/{run_id}/cancel \
  -H 'x-user-id: u1'
```

请求体（`CreateRunRequest`）字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `agent_id` | 是 | 场景 ID（同 `/chat/`） |
| `input` | 否 | 输入消息（同 `/chat/` 的 `ChatRequest.input` 同构） |
| `session_id` | 否 | 省略即 `thread_id`；显式提供且不一致 → 400 |
| `stream_mode` / `multitask_strategy` | 否 | 接受但忽略（固定 messages+custom 流、reject 并发策略） |
| `on_disconnect` | 否 | `cancel`（默认，断线即中断 run）/ `continue`（仅断开订阅） |

## 2. 场景种子（config.yaml `agents` 段）

场景不再提供独立 CRUD API——启动时由 lifespan 幂等同步进框架
StorageBase（`user_id="default"`，所有用户经 default fallback 可见），
chat / deerflow 按 `agent_id` 解析。场景的增删改走框架内置 `/agent` 路由。

```yaml
# config.yaml
agents:
  - agent_id: default
    name: "通用助手"
    system_prompt: "..."
    model_provider: ""    # 留空使用全局 active provider
    model_name: ""
    max_iters: 20
    enabled_tools: []     # 空 = 全部工具；非空在首次创建时写入工具白名单
    enabled_skills: []    # 暂无技能白名单落地机制
```

## 3. 模型（`/models`、`/model`）

```bash
# 列出可用模型（config.yaml models 段注册的）
curl http://192.168.0.106/api/models

# 切换 active 模型
curl -X POST http://192.168.0.106/api/models/active \
  -H 'Content-Type: application/json' -d '{"provider_id":"deepseek"}'

# 按凭证查询模型 / 单模型绑定过滤
curl http://192.168.0.106/api/model/credential
curl -X PATCH http://192.168.0.106/api/model/credential/{credential_id} \
  -H 'Content-Type: application/json' -d '{}'
```

## 4. 文件上传（`/uploads`）

```bash
curl http://192.168.0.106/api/uploads/limits
curl http://192.168.0.106/api/uploads/files

# 上传文件（multipart）
curl -X POST http://192.168.0.106/api/uploads/files \
  -F 'file=@./test.txt'
curl -X POST http://192.168.0.106/api/uploads/files/streaming \
  -F 'file=@./test.txt'

# 删除 / 下载
curl -X DELETE http://192.168.0.106/api/uploads/files \
  -H 'Content-Type: application/json' -d '{"filename":"test.txt"}'
curl 'http://192.168.0.106/api/uploads/files/download?filename=test.txt'
```

## 5. 框架内置：会话（`/sessions`）

```bash
curl http://192.168.0.106/api/sessions/
curl -X POST http://192.168.0.106/api/sessions/ \
  -H 'Content-Type: application/json' -d '{}'
curl -X PATCH http://192.168.0.106/api/sessions/{session_id} \
  -H 'Content-Type: application/json' -d '{}'
curl -X DELETE http://192.168.0.106/api/sessions/{session_id}
curl -X POST http://192.168.0.106/api/sessions/{session_id}/interrupt \
  -H 'Content-Type: application/json' -d '{}'
curl http://192.168.0.106/api/sessions/{session_id}/messages
curl http://192.168.0.106/api/sessions/{session_id}/status
curl -N http://192.168.0.106/api/sessions/{session_id}/stream
```

## 6. 框架内置：智能体（`/agent`）

```bash
curl http://192.168.0.106/api/agent/schema
curl http://192.168.0.106/api/agent/schema/v2
curl http://192.168.0.106/api/agent/
curl -X POST http://192.168.0.106/api/agent/ \
  -H 'Content-Type: application/json' -d '{"type":"researcher"}'
curl -X PATCH http://192.168.0.106/api/agent/{agent_id} \
  -H 'Content-Type: application/json' -d '{}'
curl -X DELETE http://192.168.0.106/api/agent/{agent_id}
```

## 7. 框架内置：凭证 / 聊天（`/credential`、`/chat`）

```bash
curl http://192.168.0.106/api/credential/schemas
curl http://192.168.0.106/api/credential/
curl -X POST http://192.168.0.106/api/credential/ \
  -H 'Content-Type: application/json' -d '{}'
curl -X PATCH http://192.168.0.106/api/credential/{credential_id} \
  -H 'Content-Type: application/json' -d '{}'
curl -X DELETE http://192.168.0.106/api/credential/{credential_id}

# 框架官方聊天接口（fire-and-forget + 订阅模式：POST 后事件经 GET /sessions/{sid}/stream
# 推送；与 deerflow /api/threads 端点并存，web_ui 前端使用）
curl -X POST http://192.168.0.106/api/chat/ \
  -H 'Content-Type: application/json' -d '{"session_id":"s1","input":"你好"}'
```

## 8. 框架内置：知识库（`/knowledge_bases`）

```bash
curl http://192.168.0.106/api/knowledge_bases/embedding_models
curl http://192.168.0.106/api/knowledge_bases/supported_content_types
curl http://192.168.0.106/api/knowledge_bases/
curl -X POST http://192.168.0.106/api/knowledge_bases/ \
  -H 'Content-Type: application/json' -d '{"name":"kb1"}'
curl http://192.168.0.106/api/knowledge_bases/{kb_id}
curl -X PATCH http://192.168.0.106/api/knowledge_bases/{kb_id} \
  -H 'Content-Type: application/json' -d '{}'
curl -X DELETE http://192.168.0.106/api/knowledge_bases/{kb_id}
curl http://192.168.0.106/api/knowledge_bases/{kb_id}/documents
curl -X POST http://192.168.0.106/api/knowledge_bases/{kb_id}/documents \
  -F 'file=@./doc.pdf'
curl -X DELETE http://192.168.0.106/api/knowledge_bases/{kb_id}/documents/{doc_id}
curl -X POST http://192.168.0.106/api/knowledge_bases/{kb_id}/search \
  -H 'Content-Type: application/json' -d '{"query":"关键词"}'
```

## 9. 框架内置：工作区 / 技能 / MCP / Hub

```bash
# workspace：MCP 与技能管理
curl http://192.168.0.106/api/workspace/mcp
curl -X POST http://192.168.0.106/api/workspace/mcp \
  -H 'Content-Type: application/json' -d '{}'
curl -X DELETE http://192.168.0.106/api/workspace/mcp/{mcp_name}
curl http://192.168.0.106/api/workspace/skill
curl -X POST http://192.168.0.106/api/workspace/skill/upload \
  -F 'file=@./skill.zip'
curl -X DELETE http://192.168.0.106/api/workspace/skill/{skill_name}

# skill（框架内置 + bocomadp 外部技能市场）
curl http://192.168.0.106/api/skill/
curl http://192.168.0.106/api/skill/{skill_id}
curl -X DELETE http://192.168.0.106/api/skill/{skill_id}
curl http://192.168.0.106/api/workspace/skills/external
curl http://192.168.0.106/api/workspace/skills/uploaded
curl -X POST http://192.168.0.106/api/workspace/skill/download/{skill_full_name}

# mcp
curl http://192.168.0.106/api/mcp/
curl -X PATCH http://192.168.0.106/api/mcp/{mcp_id} \
  -H 'Content-Type: application/json' -d '{}'
curl -X DELETE http://192.168.0.106/api/mcp/{mcp_id}

# hub（ClawHub / GitHub MCP 市场代理）
curl http://192.168.0.106/api/hub/mcp
curl http://192.168.0.106/api/hub/mcp/{hub_id}/cards
curl http://192.168.0.106/api/hub/mcp/{hub_id}/cards/{card_id}
curl -X POST http://192.168.0.106/api/hub/mcp/{hub_id}/cards/{card_id}/install
curl http://192.168.0.106/api/hub/skill
curl -X POST http://192.168.0.106/api/hub/skill/{hub_id}/cards/{card_id}/install
```

## 10. 其他（`/schedule`、`/tts-model`）

```bash
# 定时任务
curl http://192.168.0.106/api/schedule/
curl -X POST http://192.168.0.106/api/schedule/ \
  -H 'Content-Type: application/json' -d '{}'
curl -X PATCH http://192.168.0.106/api/schedule/{schedule_id} \
  -H 'Content-Type: application/json' -d '{}'
curl -X DELETE http://192.168.0.106/api/schedule/{schedule_id}
curl http://192.168.0.106/api/schedule/{schedule_id}/sessions

# TTS 模型
curl http://192.168.0.106/api/tts-model/
```

## 备注

- 场景会话闭环验证只需第 0/1/2 组命令：`POST /api/threads/t1/runs/stream` 用 `agent_id` 验证场景路由（不同场景 → 不同 system_prompt / 模型 / 工具白名单）
- OpenAPI 在线文档：`http://192.168.0.106:8000/docs` 或 `http://192.168.0.106:8000/openapi.json`（直连端口）
- 会话状态与消息由原生 storage 落库（config.yaml `db.url`，PostgreSQL）；工作区文件根目录见 config.yaml `workspace_dir`（docker 挂载 `examples/agent_service/workspaces`）
