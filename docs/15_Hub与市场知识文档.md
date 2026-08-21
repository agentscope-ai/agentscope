# AgentScope Hub 与市场知识文档

> **版本**：v2.0.6（含 main 未发版更新，截至 2026-08-20）  
> **适用对象**：开发者、架构师、技术面试官  
> **文档目标**：理解 Hub 系统（MCP 市场 / Skill 市场）的设计原理、安装流程、数据模型和面试要点  
> **覆盖提交**：#2197 feat(hub): support to install MCP and skills from MCP/skill hubs、#2214 fix(hub): scope ClawHub card ids by owner
> **main 未发版更新（截至 2026-08-20）**：#2291 接受 owner-scoped skill card ids（`owner/slug`）；#2230 保留 GitHub MCP 环境输入

---

## 一、核心概念

### 1.1 什么是 Hub？

**Hub（市场）** 是 AgentScope 在 v2.0.5 引入的全新子系统，实现**可发现、可安装的工具与技能生态**。

一个 Hub 是一个"厂商注册表"：MCP 工具或 Skill 的作者将产品发布到 Hub，用户从前端市场浏览、搜索、一键安装到自己的工作区。

```
┌──────────────────────────────────────────────────────────────────┐
│                     Hub 系统整体架构                                │
│                                                                   │
│  ┌─────────────────┐    ┌─────────────────┐                      │
│  │   GitHub MCP     │    │   ClawSkillHub  │   ← Hub 厂商源        │
│  │   Registry       │    │   (ClawHub)    │                      │
│  └────────┬─────────┘    └────────┬────────┘                      │
│           │                       │                               │
│  ┌────────▼──────────────────────▼────────────────────┐          │
│  │                 Hub 抽象层                            │          │
│  │  HubBase → MCPHubBase / SkillHubBase                │          │
│  │  MCPCard / SkillCard / MCPHubPage / SkillHubPage   │          │
│  └────────────────────────┬───────────────────────────┘          │
│                           │                                       │
│  ┌────────────────────────▼─────────────────────────────────┐    │
│  │              REST 路由 & 服务层                              │    │
│  │  /hub/mcp → 浏览 / 搜索 / 查看卡片                          │    │
│  │  /hub/skill → 浏览 / 搜索 / 查看卡片 / 查看 SKILL.md       │    │
│  │  /mcp → 用户 MCP 库（CRUD）                                │    │
│  │  /skill → 用户 Skill 库（CRUD）                            │    │
│  └────────────────────────┬─────────────────────────────────┘    │
│                           │                                       │
│  ┌────────────────────────▼─────────────────────────────────┐    │
│  │              存储层                                         │    │
│  │  MCPRecord（用户已安装 MCP 的期望状态）                      │    │
│  │  SkillRecord（用户已安装 Skill 的期望状态）                  │    │
│  └───────────────────────────────────────────────────────────┘    │
│                           │                                       │
│  ┌────────────────────────▼─────────────────────────────────┐    │
│  │              工作区同步                                     │    │
│  │  用户库（desired state） ↔ 工作区 MCP 文件（actual state）│    │
│  │  MCP：converge 两者，按 name 匹配                           │    │
│  │  Skill：从 Hub 拉取 archive 写入 workspace                 │    │
│  └─────────────────────────────────────────────────────────┘      │
└──────────────────────────────────────────────────────────────────┘
```

**关键理解** 🔴：
- **Hub 是发现渠道，不是运行载体**——MCP/Skill 最终安装到**用户库**（user-level），再同步到**工作区**（workspace-level）
- **用户库 = desired state**，工作区 `.mcp` 文件 = actual state，二者通过 **converge** 保持一致
- MCP 的 config 内联存储在 `MCPRecord.client.mcp_config`；Skill 的 archive 按需从 Hub 重新拉取
- 扩展性设计：`HubBase` 抽象基类支持第三方 Hub 接入（GitHub Registry、ClawHub 是最初两个实现）

### 1.2 为什么需要 Hub？

| 需求 | 没有 Hub | 有 Hub |
|------|---------|--------|
| 发现 MCP 工具 | 用户需去 GitHub 搜索、手动复制配置 | 市场浏览 + 搜索 + 详情 + 一键安装 |
| 安装 Skill | 手动归档上传 | 浏览 + 安装 + 自动注入工作区 |
| 版本管理 | 无 | 版本检测 + 升级提示 |
| 配置重填 | 每次重新输入 API key | `MCPRecord.values` 记住上次填写 |
| 统一生态 | 各管各 | GitHub MCP Registry / ClawHub 统一入口 |

---

## 二、核心抽象：HubBase

### 2.1 类层次

```
HubBase (src/agentscope/app/hub/_base.py)
├── MCPHubBase (src/agentscope/app/hub/_mcp/_base.py)
│   └── GitHubMCPHub (_mcp/_github_hub.py)     ← GitHub MCP Registry
└── SkillHubBase (src/agentscope/app/hub/_skill/_base.py)
    └── ClawSkillHub (_skill/_claw_hub.py)       ← ClawHub Skill Registry
```

### 2.2 HubBase 接口

```python
class HubBase(ABC):
    """所有 Hub 的抽象基类"""

    # ===== 标识 =====
    hub_id: str                # 唯一 ID，如 "github_mcp"、"claw_skill"
    display_name: str          # 用户可见名称
    description: str           # 描述文本
    url: str | None            # 前端指向的 URL
    priority: int              # 多 Hub 排序优先级（越大越靠前）

    # ===== 来源注册 =====
    source_id: str             # 数据来源类型（如 "github_v1"、"clawhub"）
    source_strength: float     # 0~1，作为 badge 显示（1.0=官方第一方）

    # ===== 核心方法 =====
    async def browse(page: int, page_size: int) -> Page
        """分页浏览"""
    async def search(query: str, page: int, page_size: int) -> Page
        """关键词搜索"""
    async def get_card(card_id: str) -> Card
        """获取单张卡片详情"""
```

**设计决策**：
- `source_id` vs `hub_id`：一个 Hub 可能有多个数据来源（如 "github_v1" → "github_v2"），`hub_id` 是用户绑定的当前源
- `source_strength`：0~1 连续值，用于前端 badge（"官方" / "第三方"），替代二元的 `is_official` 布尔值

### 2.3 卡片与分页通用结构

```python
class HubCard:
    """单张卡片（MCP/Skill 共用抽象）"""
    owner: str               # 作者（owner/slug 前半）
    repo: str               # 仓库名（owner/slug 后半）
    name: str               # 展示名称
    description: str        # 介绍（前端截断）
    tags: list[str]         # 搜索标签
    author: str             # 发布者
    icon_url: str | None    # 图标
    url: str | None         # 原始链接
    version: str | None     # 版本号
    rating: RatingInfo | None  # 评分/下载/issue 等元信息

class RatingInfo:
    stars: int | None
    downloads: int | None
    issues: int | None
    updated_at: str | None

class Page:
    cards: list[Card]       # 当前页卡片
    total: int              # 总卡片数
    page: int               # 当前页码
    page_size: int          # 每页大小
```

---

## 三、MCP Hub

### 3.1 MCPHubBase 与 MCPCard

```python
class MCPCard(HubCard):
    """MCP 卡片（比通用 HubCard 多安装所需信息）"""
    install_id: str         # 安装 ID，格式 "owner/slug"
    inputs_schema: InputSchema | None  # 安装时需要用户填写的 schema（如 API key）
    readme: str             # 自述文件内容
    template: MCPTemplate | None  # 配置模板（含环境变量/参数等）

class MCPTemplate:
    """MCP 配置模板 —— 卡片持有"模板"，用户填"值"，渲染出最终 .mcp 配置"""
    type: str               # 传输类型：'http'(SSE) / 'stdio'(本地进程)
    command_or_url: str     # type=stdio → 命令；type=http → URL
    envs: dict[str, str]    # 环境变量模板
    args: list[str]        # 命令行参数模板
    headers: dict[str, str] # HTTP headers 模板

class MCPHubBase(HubBase):
    """MCP Hub 抽象基类"""
    pass  # 继承 HubBase 全部接口，卡片类型为 MCPCard

class InputSchema:
    """用户安装时需填写的配置项"""
    type: str               # JSON Schema type（如 "object"）
    properties: dict        # 各项 field → {type, title, description, format, ...}
    required: list[str]     # 必填项列表
    ui_order: list[str] | None  # 前端展示顺序
```

**关键理解** 🔴：

- **安装流程**：Hub → Card.template（模板占位符）→ **用户填 values** → `render_mcp()` 渲染 → `MCPClient` 实例
- 模板中的占位符用 `$` 标记：如 `"url": "$API_BASE_URL/"`、`"args": ["--port", "$PORT"]`
- 用户填的 `values` 保存在 `MCPRecord.values` 中，下次安装（或升级）时预填，避免重新输入

### 3.2 GitHubMCPHub

GitHub MCP Registry 是第一个 MCP Hub 实现：

```python
class GitHubMCPHub(MCPHubBase):
    hub_id = "github_mcp"
    source_id = "github_v1"      # 数据来源版本标识
    source_strength = 0.8        # 接近官方生态

    # registry 索引文件位置
    registry_url: str            # 如 GitHub raw URL → JSON index
    client: httpx.AsyncClient | None

    async def _load_index(self) -> list[dict]:
        """从 GitHub raw URL 拉取 registry.json"""
```

**工作原理**：
1. 注册表是一个 JSON 文件（如 GitHub raw URL），含所有 MCP 列表的 metadata
2. 查询/分页在本地内存完成（全量索引一次性加载）
3. 每张卡片指向一个 GitHub repo（install_id = `owner/repo`）
4. 卡片的 metadata（description/readme 等）存储在 repo 的 `.mcp-card` 目录下

---

## 四、Skill Hub

### 4.1 SkillHubBase 与 SkillCard

```python
class SkillCard(HubCard):
    """Skill 卡片"""
    install_id: str         # 安装 ID
    markdown: str           # SKILL.md 内容（安装时快照到 SkillRecord）
    skill_archive: SkillArchive | None  # 技能归档文件

class SkillArchive:
    """Skill 归档文件（zip/tar）"""
    url: str                # 下载地址
    size: int | None        # 文件大小
    sha256: str | None      # 校验和

class SkillHubBase(HubBase):
    """Skill Hub 抽象基类"""
    async def get_skill_archive(self, card_id: str) -> SkillArchive:
        """获取 Skill 归档文件的下载信息"""
```

### 4.2 ClawSkillHub

```python
class ClawSkillHub(SkillHubBase):
    hub_id = "claw_skill"
    source_id = "clawhub"
    source_strength = 1.0    # 官方第一方 Hub

    base_url: str | None     # ClawHub API 地址
    client: httpx.AsyncClient | None
```

**Card ID 隔离 (#2214)**：
- v2.0.5 修复前：`card_id` 格式为 `"slug"`（全局不唯一，可能跨 owner 冲突）
- 修复后：`card_id` 格式为 `"owner/slug"`（owner 级隔离，精确锚定到发布者）

---

## 五、REST API 端点

### 5.1 Hub 浏览路由（`/hub`）

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/hub/mcp` | 列出所有 MCP Hub 的 metadata |
| GET | `/hub/mcp/{hub_id}` | 浏览指定 MCP Hub 的卡片 |
| GET | `/hub/mcp/{hub_id}/search?q=xxx` | 搜索 MCP Hub |
| GET | `/hub/mcp/{hub_id}/cards/{card_id}` | 查看单张 MCP 卡片（含 inputs_schema + template） |
| POST | `/hub/mcp/{hub_id}/install` | 安装 MCP 到用户库 |
| GET | `/hub/skill` | 列出所有 Skill Hub 的 metadata |
| GET | `/hub/skill/{hub_id}` | 浏览指定 Skill Hub 的卡片 |
| GET | `/hub/skill/{hub_id}/search?q=xxx` | 搜索 Skill Hub |
| GET | `/hub/skill/{hub_id}/cards/{card_id}` | 查看单张 Skill 卡片 |
| GET | `/hub/skill/{hub_id}/cards/{card_id}/readme` | 查看 SKILL.md 内容 |
| GET | `/hub/skill/{hub_id}/cards/{card_id}/archive` | 下载 Skill 归档文件 |

### 5.2 用户 MCP 库路由（`/mcp`）

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/mcp` | 列出当前用户已安装的 MCP |
| POST | `/mcp` | 手动添加 MCP（不来自 Hub） |
| GET | `/mcp/{name}` | 查看单个已安装 MCP |
| PUT | `/mcp/{name}` | 更新 MCP 配置（含启用/禁用） |
| DELETE | `/mcp/{name}` | 删除已安装 MCP |

### 5.3 用户 Skill 库路由（`/skill`）

| 方法 | 端点 | 功能 |
|------|------|------|
| GET | `/skill` | 列出当前用户已安装的 Skill |
| GET | `/skill/{name}` | 查看单个已安装 Skill |
| PUT | `/skill/{name}` | 更新 Skill（含启用/禁用） |
| DELETE | `/skill/{name}` | 删除已安装 Skill |

### 5.4 完整安装流程

```
用户点击"安装"
    ↓
POST /hub/mcp/{hub_id}/install  { card_id, name, values }
    ↓
服务端：
  1. Hub.get_card(card_id)         → 获取 MCPCard（含 template）
  2. render_mcp(template, values)  → 渲染为 MCPClient
  3. MCPRecord(client, ...)        → 写入存储（desired state）
    ↓
工作区同步（下次进入工作区或手动触发）：
  4. converge 用户库 ↔ 工作区 .mcp 文件
  5. 工作区动态加载 MCP Client
```

---

## 六、数据模型

### 6.1 MCPRecord

```python
class MCPRecord(_RecordBase):
    """用户已安装的 MCP 记录（user-level desired state）"""

    user_id: str                    # 所属用户
    client: MCPClient               # 可连接的实例（含 mcp_config）
    name: str                       # 计算字段 = client.name，用户唯一标识

    # ── 快照字段（安装时从 Hub Card 复制）──
    display_name: str | None        # 用户可见名称
    description: str                # 描述
    author: str | None              # 发布者
    icon_url: str | None            # 图标 URL
    url: str | None                 # Hub 原始页面链接
    tags: list[str]                 # 标签列表

    # ── 安装输入 ──
    values: dict                    # 用户填写的安装配置（如 API keys）

    # ── 来源追踪 ──
    hub_id: str | None              # 从哪个 Hub 安装（None=手动添加）
    card_id: str | None             # 卡片 ID（"owner/slug"）
    version: str | None             # 安装时版本号

    enabled: bool = True            # 是否启用
```

**设计决策 🔴**：

| 决策 | 理由 |
|------|------|
| 快照字段（display_name/description 等）存于 Record 而非实时查询 | 手动添加的 MCP 无卡片可查；Hub 离线或删除卡片时不丢信息 |
| `values` 明文存储（含 API key） | `client.mcp_config` 已存明文，多存一份不扩大泄露面；避免换 key 或升级时重填 |
| `name` 是 `computed_field`（计算属性） | 避免与 `client.name` 不同步，且无需迁移历史记录 |
| `hub_id` + `card_id` 配对定位来源 | 回答"从哪装的、有没有新版本" |

### 6.2 SkillRecord

```python
class SkillRecord(_RecordBase):
    """用户已安装的 Skill 记录（user-level desired state）"""

    user_id: str                    # 所属用户
    name: str                       # 唯一名称（与 workspace 引用匹配）

    # ── 快照字段 ──
    display_name: str | None
    description: str
    tags: list[str]
    author: str | None
    icon_url: str | None
    url: str | None
    markdown: str                   # SKILL.md 内容快照

    # ── 来源追踪 ──
    hub_id: str | None
    card_id: str | None
    version: str | None

    enabled: bool = True
```

**与 MCPRecord 的关键差异**：

| 属性 | MCPRecord | SkillRecord |
|------|-----------|-------------|
| 实体存储 | `client` 含完整 `mcp_config`（内联） | 无 skill 实体（archive 按需从 Hub 拉取） |
| workspace 同步 | converge（配置同步） | 从 Hub 重新下载 archive 写入 workspace |
| 离线支持 | ✅ 配置完整自包含 | ❌ 依赖 Hub 在线（TODO：local blob store） |

---

## 七、面试要点

### 7.1 设计模式

| 模式 | 位置 | 说明 |
|------|------|------|
| **模板方法** | HubBase → MCPHubBase / SkillHubBase | 抽象基类定义接口骨架，子类实现具体逻辑 |
| **适配器** | MCPTemplate → render_mcp → MCPClient | 将 Hub 卡片格式适配为 AgentScope 运行时格式 |
| **快照模式** | MCPRecord / SkillRecord 显示字段 | 安装时复制而非实时查询，保证离线可用、避免 Hub 不可达 |
| **Converge Pattern** | MCP 用户库 ↔ workspace `.mcp` | desired state vs actual state 收敛，与 Kubernetes reconciliation loop 同构 |

### 7.2 面试高频考点

1. **为什么 MCPReco​rd 要快照字段而不是实时查 Hub？**
   - 手动添加的 MCP 没有 Hub Card 可查
   - Hub 可能离线、删除卡片、或改名，不能依赖外部可达性

2. **MCP 和 Skill 的存储策略为什么不同？**
   - MCP config 只有几十字节，内联存储成本低
   - Skill 是目录文件，可能很大，需要 blob store（目前 TODO）

3. **用户库（user-level）和工作区 `.mcp` 文件（workspace-level）是什么关系？**
   - 用户库 = desired state（期望状态），工作区文件 = actual state（实际状态）
   - 通过 converge 操作同步两者，按 name 匹配
   - 设计灵感来自 Kubernetes reconciliation loop

4. **`hub_id` vs `source_id` 的区别？**
   - `hub_id` 是用户绑定的 Hub 标识（如 `"github_mcp"`）
   - `source_id` 是数据来源版本（如 `"github_v1"`），同一 hub_id 可切换 source_id
   - 类比：hub_id = 数据库名，source_id = schema version

5. **#2214 修复了什么 bug？**
   - ClawSkillHub 的 card_id 从 `"slug"` 改为 `"owner/slug"`
   - 原问题：不同 owner 可能有同名 slug，card_id 全局不唯一
   - 修复后 owner 级隔离，精确锚定发布源

### 7.3 易忽略的陷阱

1. **`values` 包含明文 API key** — 与 `mcp_config` 中已存在的明文一致，不扩大泄露面，但对开发者而言是需要注意的安全设计选择
2. **Skill 归档不缓存** — 每次工作区同步都从 Hub 重新下载，如果 Hub 离线则无法同步
3. **MCPRecord.name 是计算字段** — 不可直接修改，改名必须通过 `client.name` 间接操作
4. **`enabled=false` 不等于删除** — 禁用记录保留配置，方便重新启用；只有真正删除才清理

---

## 八、与现有模块关系

| 模块 | 关系 |
|------|------|
| **工具（Toolkit）** | 安装的 MCP 最终通过 `Toolkit.prepare()` 注册为 Agent 可用工具 |
| **工作区（Workspace）** | 安装的 MCP/Skill 通过 converge 流程注入到 `.mcp` 文件 |
| **存储（Storage）** | MCPRecord / SkillRecord 通过 `RedisStorage` / SQL 持久化 |
| **权限（Permission）** | 安装 MCP 后工具权限沿用现有 permission 系统 |
| **事件（Event）** | 安装/卸载操作产生事件通知工作区刷新 |
