# 行业知识问答 Agent 示例

完整的行业知识问答 Agent 示例，演示如何实现：

- **7 个自定义工具** 按 4 组分类管理（订单/客户/发票/报表）
- **工具权限分组控制**，不同部门看到不同工具集
- **数据权限过滤中间件**，按行过滤工具返回的业务数据
- **预创建 + 幂等更新**，启动后自动为所有人创建 Agent
- **对接第三方系统**，工厂函数设计方便替换真实数据源

## 架构

```
┌──────────────────────────────────────────────────────────┐
│                    Web UI (Settings)                      │
│        Username: user_sales  →  X-User-ID: user_sales    │
└────────────────────────┬─────────────────────────────────┘
                         │
┌────────────────────────▼─────────────────────────────────┐
│              Agent Service (main.py)                      │
│              http://127.0.0.1:8000                       │
│                                                          │
│  ┌──────────────────────────────────────────────────┐    │
│  │  create_industry_tools(user_id, ...)             │    │
│  │  → 查权限系统 → 返回分组名 → 展开为工具实例         │    │
│  │                                                │    │
│  │  TOOL_GROUPS = {                               │    │
│  │    "order":    [query_order, statistics, ...],  │    │
│  │    "customer": [query_customer],                │    │
│  │    "invoice":  [query_invoice, ...],            │    │
│  │    "report":   [query_sales_performance],       │    │
│  │  }                                             │    │
│  └──────────────────────────────────────────────────┘    │
│  ┌──────────────────────────────────────────────────┐    │
│  │  create_industry_middlewares(user_id, ...)       │    │
│  │  → AuditMiddleware（所有用户）                    │    │
│  │  → DataPermissionMiddleware（管理员）             │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

**数据流**：`X-User-ID` → 工厂函数 → 分组展开 → 工具注入 → Agent 对话

## 文件说明

| 文件 | 说明 |
|------|------|
| `main.py` | Agent Service 入口，定义工具分组和工厂函数 |
| `tools.py` | 7 个工具函数，按 4 组注释分隔 |
| `biz_system.py` | 模拟第三方业务系统（用户/分组权限/数据/数据过滤） |
| `middlewares.py` | `DataPermissionMiddleware` + `AuditMiddleware` |
| `precreate_agents.py` | 预创建/更新 16 个用户的 Agent（幂等） |
| `reset_agents.py` | 删除所有同名 Agent 并重建 |
| `biz_proxy.py` | 独立业务后端代理（可选，端口 8001） |

## 工具清单（7 个，4 组）

### [订单组] — 3 个工具
| 工具 | 说明 |
|------|------|
| `query_order` | 查询订单详情，支持按 order_id 筛选 |
| `query_order_statistics` | 汇总统计（总量/金额/状态分布） |
| `query_customer_orders` | 查询某客户的所有订单及总金额 |

### [客户组] — 1 个工具
| 工具 | 说明 |
|------|------|
| `query_customer` | 查询客户信息，支持按名称筛选 |

### [发票组] — 2 个工具
| 工具 | 说明 |
|------|------|
| `query_invoice` | 查询发票信息，支持按 invoice_id 筛选 |
| `query_invoice_by_order` | 根据订单 ID 查关联发票 |

### [报表组] — 1 个工具
| 工具 | 说明 |
|------|------|
| `query_sales_performance` | 销售业绩汇总，每人订单数/金额 |

## 测试用户（16 个，8 部门）

| 用户ID | 部门 | 工具分组 | 工具数 |
|--------|------|----------|--------|
| `user_sales` / `_2` / `_3` | 销售 | order, customer | 4 |
| `user_finance` / `_2` | 财务 | order, invoice, report | 6 |
| `user_admin` | 管理 | order, customer, invoice, report | 7 |
| `user_hr` / `_2` | HR | customer | 1 |
| `user_tech` / `_2` | 技术 | order | 3 |
| `user_marketing` / `_2` | 市场 | customer, invoice | 3 |
| `user_support` / `_2` | 客服 | order, customer | 4 |
| `user_logistics` / `_2` | 物流 | order | 3 |

## 数据权限（仅管理员启用）

数据权限过滤由 `DataPermissionMiddleware` 实现，在工具返回结果后按行过滤。当前仅在 `user_admin` 上启用，各用户的数据权限规则定义在 `biz_system.DATA_PERMISSIONS` 中。

## 快速开始

### 前置条件

- Python 3.10+
- Redis 5.x（`127.0.0.1:6379`）
- DeepSeek API Key

### 1. 启动 Redis

```powershell
Start-Process -FilePath "C:\googoe\Redis-x64-5.0.14.1\redis-server.exe" -WindowStyle Hidden
```

### 2. 启动 Agent Service

```powershell
cd examples/industry_agent
$env:DEEPSEEK_API_KEY="sk-xxx"
uv run python main.py
```

服务启动后访问 API 文档：http://127.0.0.1:8000/docs

### 3. 预创建 Agent（新窗口）

```powershell
cd examples/industry_agent
uv run python precreate_agents.py
```

输出示例：

```
============================================================
  预创建/更新用户 Agent
============================================================
  [user_sales] → 新建 abc123...
  [user_sales_2] → 新建 def456...
  ...
  新建: 16  更新: 0  跳过: 0  错误: 0
  总计: 16/16
============================================================
```

> **幂等**：脚本检查同名 Agent 是否存在，内容一致则跳过，不一致则用 `PATCH /agent/{id}` 更新。

> **重建**：如需删除所有 Agent 并重建，运行 `uv run python reset_agents.py`

### 4. Web UI 测试

#### 4.1 启动 Web UI

```powershell
cd examples/web_ui
pnpm dev
```

#### 4.2 配置连接

打开 `http://localhost:5173`，在设置页填写：

| 设置 | 值 |
|------|-----|
| Server URL | `http://127.0.0.1:8000` |
| Username | `user_sales`（或其他用户ID） |

#### 4.3 添加 DeepSeek 凭证

点击左侧 **Credential** → 创建凭证：DeepSeek API，填入 API Key。

#### 4.4 开始对话

点击 **Chat**，列表中会自动出现预创建的 Agent，选择一个 → 创建 Session → 开始对话。

#### 4.5 切换用户测试

在设置页修改 **Username** 即可切换不同用户，观察工具集差异：

```
user_sales    → 4 个工具（订单组 + 客户组）
user_finance  → 6 个工具（订单组 + 发票组 + 报表组）
user_admin    → 7 个工具（全部）
user_hr       → 1 个工具（客户组）
```

> **原理**：Username 作为 `X-User-ID` 头传给 Agent Service，工厂函数根据 ID 返回不同分组对应的工具。

## API 测试

### 直接调用 Agent Service

```bash
# 1. 创建凭证
curl -X POST http://127.0.0.1:8000/credential/ \
  -H "X-User-ID: user_sales" \
  -H "Content-Type: application/json" \
  -d '{"data": {"type": "deepseek_credential", "api_key": "sk-xxx"}}'

# 2. 获取已预创建的 Agent 列表
curl http://127.0.0.1:8000/agent/ \
  -H "X-User-ID: user_sales"

# 3. 创建 Session
curl -X POST http://127.0.0.1:8000/sessions/ \
  -H "X-User-ID: user_sales" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<agent_id>",
    "chat_model_config": {
      "type": "deepseek",
      "credential_id": "<credential_id>",
      "model": "deepseek-v4-flash",
      "parameters": {}
    }
  }'

# 4. 发消息
curl -X POST http://127.0.0.1:8000/chat/ \
  -H "X-User-ID: user_sales" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "<agent_id>",
    "session_id": "<session_id>",
    "input": {
      "name": "user",
      "role": "user",
      "content": [{"type": "text", "text": "查一下所有订单"}]
    }
  }'
```

### 更新 Agent 配置

```bash
curl -X PATCH http://127.0.0.1:8000/agent/<agent_id> \
  -H "X-User-ID: user_sales" \
  -H "Content-Type: application/json" \
  -d '{"system_prompt": "新的提示词"}'
```

## 核心代码

### 工具分组定义（main.py）

```python
TOOL_GROUPS: dict[str, list] = {
    "order":    [query_order, query_order_statistics, query_customer_orders],
    "customer": [query_customer],
    "invoice":  [query_invoice, query_invoice_by_order],
    "report":   [query_sales_performance],
}
```

### 工具工厂函数（分组展开）

```python
async def create_industry_tools(user_id, agent_id, session_id):
    # 从业务系统获取用户的工具分组权限
    groups = biz_system.get_tool_permissions(user_id)
    # 如 user_sales → ["order", "customer"]
    #    user_admin → ["order", "customer", "invoice", "report"]

    tools = []
    for group in groups:
        for func in TOOL_GROUPS.get(group, []):
            tools.append(FunctionTool(func=func, is_read_only=True))

    return tools
```

### 中间件工厂函数

```python
async def create_industry_middlewares(user_id, agent_id, session_id):
    middlewares = [AuditMiddleware()]  # 所有用户都有审计日志

    if user_id == "user_admin":
        middlewares.append(DataPermissionMiddleware())

    return middlewares
```

### 权限配置（biz_system.py）

```python
# 分组权限：控制用户能用的工具组
TOOL_PERMISSIONS = {
    "user_sales":   ["order", "customer"],              # 4 个工具
    "user_finance": ["order", "invoice", "report"],     # 6 个工具
    "user_admin":   ["order", "customer", "invoice", "report"],  # 7 个工具
    "user_hr":      ["customer"],                       # 1 个工具
}
```

### 控制台日志示例

```
[FACTORY] 创建工具: user=user_sales, agent=xxx
[FACTORY] 用户 user_sales 的工具分组: ['order', 'customer']
[FACTORY]   [order] + query_order
[FACTORY]   [order] + query_order_statistics
[FACTORY]   [order] + query_customer_orders
[FACTORY]   [customer] + query_customer

[FACTORY] 创建工具: user=user_finance, agent=yyy
[FACTORY] 用户 user_finance 的工具分组: ['order', 'invoice', 'report']
[FACTORY]   [order] + query_order
[FACTORY]   [order] + query_order_statistics
[FACTORY]   [order] + query_customer_orders
[FACTORY]   [invoice] + query_invoice
[FACTORY]   [invoice] + query_invoice_by_order
[FACTORY]   [report] + query_sales_performance
```

## 扩展指南

### 添加新工具

1. 在 `tools.py` 添加新函数并放入对应分组注释下：

```python
# ═══════════════════════════════════════
# 5. [库存组] 库存查询
# ═══════════════════════════════════════

def query_stock(product_id: str = "") -> str:
    """查询库存信息。"""
    ...
```

2. 在 `main.py` 的 `TOOL_GROUPS` 中注册：

```python
TOOL_GROUPS = {
    "order":    [...],
    "customer": [...],
    "invoice":  [...],
    "report":   [...],
    "stock":    [query_stock],  # 新增
}
```

3. 在 `biz_system.py` 的 `TOOL_PERMISSIONS` 中分配：

```python
"user_admin": ["order", "customer", "invoice", "report", "stock"],
```

> **不需要改工厂函数** —— 分组展开逻辑自动支持新增分组。

### 添加新用户

在 `biz_system.py` 中补充即可：

```python
USERS["user_new"] = {"id": "user_new", "name": "新用户", "department": "新部门"}
TOOL_PERMISSIONS["user_new"] = ["order", "customer"]
```

然后在 `precreate_agents.py` 的 `_PRECREATE_CONFIG` 中加一行：

```python
("user_new", "新用户助手", "你是新部门的新用户助手..."),
```

### 对接真实第三方系统

替换 `biz_system.py` 中 `TOOL_PERMISSIONS` 为远程 API 调用：

```python
def get_tool_permissions(user_id: str) -> list[str]:
    resp = requests.get(
        "https://your-biz-api/user/tool-groups",
        params={"user_id": user_id},
        timeout=5,
    )
    return resp.json()["groups"]  # 返回 ["order", "customer"]
```

工厂函数不需要改 —— 第三方只需返回分组名列表，服务端 `TOOL_GROUPS` 负责展开为具体的工具实现。

### 添加更多中间件

```python
async def create_industry_middlewares(user_id, agent_id, session_id):
    middlewares = [AuditMiddleware()]

    if user_id in VIP_USERS:
        middlewares.append(CacheMiddleware())
    if user_id == "user_admin":
        middlewares.append(DataPermissionMiddleware())

    return middlewares
```

## 常见问题

### Q: 为什么 Agent 调用了错误的工具？

A: 检查是否有多个服务在监听 8000 端口：

```powershell
netstat -ano | findstr :8000
```

### Q: 如何查看工厂函数是否被调用？

A: 查看 Agent Service 控制台日志，应看到 `[FACTORY]` 输出。

### Q: 页面改了提示词，怎么还原？

A: 运行 `uv run python precreate_agents.py`，脚本会检测差异并通过 PATCH 还原。

### Q: 预创建时出现重复 Agent？

A: 脚本会自动清理同名重复 Agent；也可运行 `uv run python reset_agents.py` 全量重建。

## 总结

这个项目演示了 AgentScope 2.0 的核心能力：

1. **分组工具管理**：`TOOL_GROUPS` 定义工具库，`TOOL_PERMISSIONS` 分配分组
2. **权限动态注入**：工厂函数 `create_industry_tools` 按 `user_id` 展开分组
3. **数据权限过滤**：中间件 `DataPermissionMiddleware` 按用户过滤返回数据
4. **审计日志**：中间件 `AuditMiddleware` 记录所有工具调用
5. **第三方对接**：`biz_system.py` 模拟业务系统，替换数据源只需改一处
6. **预创建 + 幂等**：`precreate_agents.py` 自动创建/更新 Agent 配置

核心设计理念：**单 Agent + 分组工具 + 动态注入 = 多租户权限隔离**。用户级别差异由工厂函数在运行时注入，同一 Agent 代码服务于所有用户。
