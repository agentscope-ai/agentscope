# Agent Service（bocomadp）配置加载流程设计文档

> 面向对象：需要阅读或维护 `bocomadp` 扩展包配置机制的开发者。
> 作用：说明配置从「磁盘文件」到「业务代码取值」的完整加载链路、优先级、数据变换与热加载语义。
> 本文件是 `docs/` 文档集的一部分，总览见同目录 [README.md](./README.md)。

---

## 1. 设计目标与原则

| 原则 | 说明 |
|------|------|
| **单一配置源** | 所有配置收敛到一个 `config.yaml`，不散落多处 |
| **按模块拆分读取** | 各业务配置由独立 `config/*_config.py` 模块负责解析，互不耦合 |
| **优先级链** | 进程环境变量 > `.env` 文件 > `config.yaml` > 代码默认值 |
| **环境变量注入** | YAML 字符串值支持 `$VAR` / `${VAR}` 引用，运行时注入敏感/易变配置 |
| **路径稳定** | 相对路径基于配置目录归一化为绝对路径，与启动工作目录无关 |
| **热加载** | `get_xxx_config()` 每次调用重新解析 YAML，修改配置即时生效（无需重启） |

---

## 2. 文件与模块结构

```
agent_service/
├── config.yaml                  # 统一配置源（主入口）
├── config.yaml.example          # 配置模板（复制为 config.yaml）
├── .env                         # 可选：环境变量注入
└── bocomadp/
    └── config/                  # 配置包
        ├── __init__.py          # 汇总导出（from bocomadp.config import ...）
        ├── base.py              # 公共加载层（.env / yaml / env 展开 / 类型工具）
        ├── audit_config.py      # AuditConfig     —— 审计留痕配置（yaml 体系）
        └── app_config.py        # AppConfig       —— 唯一配置 schema（yaml 主源 + env 覆盖）
```

### 2.1 各配置模块职责

| 模块 | 配置类 | 对应配置源 | 消费方 |
|------|--------|-----------|--------|
| `base.py` | （公共工具） | `config.yaml` / `.env` / 进程环境变量 | 各配置模块 |
| `audit_config.py` | `AuditConfig` | `config.yaml` 的 `audit` 节点（`enabled` / `log_path`） | `middleware/factory.py`、`middleware/audit.py` |
| `app_config.py` | `AppConfig` | `config.yaml` 全部节点（`log_level` / `logging` / `service` / `redis` / `runtime` / `tools` / `middlewares` / `mcp` / `providers` / `app_name` / `workspace_dir`）+ `BOCOMADP_` env 覆盖 | `main.py`（日志 / 服务 / Redis / 注册表开关 / 模型注册 / 工作区）、`routers/platform_health.py` |

> **单源化**：`config.yaml` 为唯一配置载体。`AppConfig`（pydantic-settings）
> 通过 `settings_customise_sources` 将 `config.yaml`（$VAR 展开后）作为主源插入
> 优先级链，`BOCOMADP_` 前缀环境变量 / `.env` 仅用于部署期覆盖，优先级最高：
> 进程环境变量 > `.env` > `config.yaml` > 代码默认值。

> **全局根节点**：`app_name` / `workspace_dir` 为 `AppConfig` 顶层字段，
> 随 `get_app_config()` 每次调用重建，热加载语义保持。

---

## 3. 加载流程图

```mermaid
flowchart TB
    subgraph SRC["配置源"]
        A1[进程环境变量 os.environ]
        A2[.env 文件]
        A3[config.yaml]
        A4[代码默认值]
    end

    subgraph BASE["base.py 公共加载层"]
        B0[定位 CONFIG_YAML_FILE / DOTENV_FILE / BASE_DIR]
        B1[load_config_yaml 读取并 safe_load]
        B2[expand_env_vars 递归展开 $VAR/${VAR}]
    end

    subgraph MOD["各配置模块"]
        C2[AuditConfig.from_yaml]
        F1[AppConfig pydantic-settings]
        F3[_ExpandedYamlSource config.yaml 主源]
    end

    subgraph FACTORY["工厂（每次调用重建 = 热加载）"]
        D2[get_audit_config]
        F2[get_app_config]
    end

    subgraph USE["业务消费"]
        E1[health 路由 / 工作区]
        E2[审计中间件装配]
    end

    A3 --> B1 --> B2
    A1 --> B2
    A2 --> B2

    B2 --> C2
    C2 --> D2 --> E2
    A4 -.兜底默认值.-> C2

    A1 --> F1
    A2 --> F1
    A3 --> F3 --> F1
    F1 --> F2 --> E1
    A4 -.兜底默认值.-> F1
```

---

## 4. 分层详解

### 4.1 路径定位（`base.py`）

```python
BASE_DIR    = Path(__file__).resolve().parent.parent.parent   # agent_service/
CONFIG_YAML_FILE = BASE_DIR / "config.yaml"
DOTENV_FILE = BASE_DIR / ".env"
```

- `BASE_DIR` 由 `__file__` 推导（base.py 位于 `bocomadp/config/` 下，向上三级），**与启动时的工作目录（CWD）无关**，保证从任何位置启动路径一致。

### 4.2 `.env` 加载（`_load_dotenv_once`）

- 逐行解析 `KEY=VALUE`，跳过空行、`#` 注释、无 `=` 的行。
- 用 `os.environ.setdefault()` 写入，**不覆盖**外部已注入的环境变量。
- `@lru_cache` 保证进程内只加载一次。
- **优先级体现**：外部注入 > `.env`。

### 4.3 YAML 读取（`load_config_yaml`）

- `@lru_cache` 缓存原始解析结果。
- 文件不存在返回 `{}`；解析结果非 dict 归一化为 `{}`。

### 4.4 环境变量展开（`expand_env_vars`）—— 核心环节

正则：`\$(?:{(\w+)}|(\w+))`

- 匹配 `$VAR` 与 `${VAR}` 两种写法。
- 对 **字符串、列表、字典递归展开**。
- **未定义变量原样保留，不报错**（留待运行时/后续校验兜底）。
- 先调用 `_load_dotenv_once()`，保证 `.env` 中的值可被引用。

**展开顺序关键点**：`load_config_yaml` 的 `lru_cache` 只缓存 YAML 原始 dict；`expand_env_vars` 在缓存之外执行。因此即使 YAML 被缓存，**环境变量的变更依然实时生效**。

### 4.5 类型归一化工具（`base.py`）

| 函数 | 作用 |
|------|------|
| `resolve_path` | 相对路径基于 `BASE_DIR` 解析为绝对路径 |
| `yaml_section` | 按层级路径取子节点（非 dict 返回 `{}`） |
| `yaml_val` | 按层级路径取标量值（缺省返回默认值） |

---

## 5. 各模块构建细节

### 5.1 全局根节点（`app_config.py`）

- `app_name` / `workspace_dir` 作为 `AppConfig` 顶层字段，由 `_ExpandedYamlSource` 从 `config.yaml` 根节点读取。
- `workspace_dir` 经 `field_validator(mode="before")` 调用 `resolve_path` 归一化为绝对路径
  （相对路径基于 `BASE_DIR`），与启动工作目录无关。
- 默认值：`app_name="交通银行智能体平台"`，`workspace_dir=BASE_DIR / "workspaces"`。

### 5.2 `AuditConfig.from_yaml()`（`audit_config.py`）

- `yaml_section(data, ["audit"])` 取子节点。
- `enabled` 缺省为 `True`。
- `log_path` 经 `resolve_path` 归一化，缺省为 `BASE_DIR / "logs" / "audit.jsonl"`。

### 5.3 `AppConfig`（`app_config.py`，yaml 主源 + env 部署期覆盖）

- pydantic-settings 基类：`env_prefix="BOCOMADP_"`，嵌套字段用 `__` 分隔（如 `BOCOMADP_REDIS__HOST`）。
- **单源化**：通过 `settings_customise_sources` 插入 `_ExpandedYamlSource`（`YamlConfigSettingsSource`
  子类，读取 `config.yaml` 后先做 `$VAR` 展开），使 `config.yaml` 的框架级节点成为主源；
  `BOCOMADP_*` 环境变量 / `.env` 优先级更高，用于部署期覆盖。
- `env_file` 使用 `DOTENV_FILE`（`agent_service/.env` 绝对路径），与启动工作目录无关。
- 分组：核心（`log_level` / `logging` / `service` / `redis`）、QwenPaw 移植占位
  （`providers` / `governance` / `hooks` / `checkpoints` / `token_usage` / `local_models`）、
  框架模块（`tools` / `middlewares` / `mcp`）。
- `get_app_config()`：**唯一入口 + 热加载工厂**，每次调用重建 `AppConfig`
  （pydantic-settings 无缓存，config.yaml 实时重读），适合运行时按需获取最新配置；
  `main.py` 模块级调用一次形成**启动快照**。
- **键拼写校验**：`_ExpandedYamlSource._read_file` 中（与读取**同源、仅一次
  文件读取**）递归比对 `config.yaml` 键与 schema 字段，疑似拼写错误
  （`difflib` 相似度 ≥ 0.7，如 `redis.hots`）fail-fast 报错；顶层业务节点
  白名单 `_BUSINESS_KEYS = {models, audit}` 之外的未知键同样报错，
  杜绝 `extra="ignore"` 静默吞错。不走 `load_config_yaml` 的 lru_cache，
  文件内容修改后校验实时生效。
- `load_models_from_yaml()` / `build_model_instance()`：从 `config.yaml` 的 `models` 节点
  加载模型条目（读取后先 `$VAR` 展开），经 `CredentialFactory` 动态实例化并
  注册到 `ProviderManager` —— 即「配置驱动模型注册」。默认路径为
  `CONFIG_YAML_FILE`（绝对路径），不依赖启动工作目录。
- `is_trace_correlation_enabled()`：trace 关联开关的唯一真源，供 ASGI 中间件与日志配置共用。

> **重要**：`main.py` 中 `RedisStorage` 必须通过 `config.redis.host` / `config.redis.port`
> 读取（与 `BOCOMADP_REDIS__HOST` 环境变量体系一致），禁止 `os.getenv("REDIS_HOST")` 绕过。

---

## 6. 消费链路

### 6.1 全局配置 → 健康检查 & 工作区

```
routers/platform_health.py → get_app_config().app_name  （每次请求重建，热加载）
main.py                   → config.workspace_dir       → LocalWorkspaceManager(basedir=...)
```

### 6.2 审计配置 → 中间件装配

```
middleware/factory.py  → build_enterprise_middlewares()
                             if get_audit_config().enabled:
                                 append AuditMiddleware(user_id, session_id)
middleware/audit.py    → on_reply / 写入时再查 get_audit_config()
```

> 说明：`factory` 在每次 agent 组装时调用一次（按 user/session 返回中间件组合），因此可在运行时动态开启/关闭审计。

### 6.3 模型配置 → Provider 注册（`app_config.py`）

```
main.py → load_models_from_yaml(config.providers.config_file)
        → build_model_instance(entry) → provider_manager.register(...)
```

> 模型条目来自 `config.yaml` 的 `models` 节点，`api_key` / `base_url` 支持 `${ENV_VAR}` 展开；
> 注册失败仅告警不影响启动（fail-soft）。

---

## 7. 优先级与热加载语义

### 7.1 优先级链（高 → 低）

```
① 进程环境变量  →  ② .env 文件  →  ③ config.yaml（含 $VAR 展开）  →  ④ 代码默认值
```

- `①②` 通过 `expand_env_vars` 注入到 `config.yaml` 的 `$VAR` 引用中；同时
  `AppConfig` 由 pydantic-settings 保证 `①②③④` 的完整优先级链（`③` 为
  `config.yaml` 框架级节点，`①②` 为 `BOCOMADP_*` 部署期覆盖）。
- `④` 仅在该节点缺失或非法时兜底。

### 7.2 热加载

- `load_config_yaml` 有 `@lru_cache`，但 **`get_xxx_config()` 每次都重新调用 `from_yaml()`**，缓存仅命中 YAML 原始解析。
- 效果：修改 `config.yaml` 或环境变量后，**下次取值即生效，无需重启**。
- 注意：`_load_dotenv_once` 有缓存，**新增/修改 `.env` 需重启进程**才生效；外部环境变量则实时生效。
- `AppConfig` 在 `main.py` 启动时读取一次（模块级 `get_app_config()`）；
  `platform_health.py` 每次请求调用 `get_app_config()` 重建，等效热加载；
  运行时任意消费点均可随时调用 `get_app_config()` 获取最新配置（成本低）。

---

## 8. 扩展新配置的规范（约定）

新增一个工具/模块配置时，遵循统一模式：

1. 在 `config.yaml` 增加一个顶层节点（如 `my_tool:`），字符串值如需动态注入用 `$VAR`。
2. 新建 `bocomadp/config/my_tool_config.py`：
   - `@dataclass` 定义 `MyToolConfig`（字段带合理默认值）。
   - `@classmethod from_yaml()`：取 `yaml_section(data, ["my_tool"])` → `expand_env_vars` → 类型归一化。
   - `def get_my_tool_config()` 工厂（每次读最新）。
3. 在 `config/__init__.py` 汇总导出。
4. 业务代码中通过 `get_my_tool_config()` 取值。

> 若新增的是**框架级**配置（日志 / Redis / 注册表开关等）或**全局根节点**字段
> （如 `app_name` / `workspace_dir` 这类），在 `config.yaml` 增加顶层节点
> （如 `logging:` / `redis:`），并在 `app_config.py` 的 `AppConfig` 中扩展
> pydantic 嵌套模型字段；部署期覆盖可用 `BOCOMADP_` 前缀环境变量。
> 仅 `audit` 类独立业务分组保留 `from_yaml` + `get_xxx_config()` 的 dataclass 模式。
>
> ⚠️ **键校验白名单**：新增**业务节点**（不在 `AppConfig` schema 内，如
> `models` / `audit`）时，必须加入 `app_config.py` 的 `_BUSINESS_KEYS`
> 集合，否则启动时会被未知键校验 fail-fast 拦截。

---

## 9. 潜在注意点 / 改进建议

| 项 | 现状 | 建议 |
|----|------|------|
| `.env` 热加载 | `@lru_cache` 只加载一次 | 如需热更新需去掉缓存或改为监听 mtime |
| 未定义 `$VAR` | 原样保留字面量，可能产生误导 | 可在加载时对必填项显式报错（暂无必填项） |
| `lru_cache` 名称易误读 | 名为缓存但热加载语义依赖外层 | 已在 `base.py` 注释明确「仅缓存原始 YAML，展开实时」 |
| 校验时机 | 部分在消费时才校验 | 可选：加载期即校验必填项，fail-fast |
| 单源化 | `config.yaml` 为唯一配置载体，`AppConfig` 为唯一 schema（含 `app_name` / `workspace_dir` 字段），env 仅部署期覆盖 | — |
| 热加载 | `get_app_config()` 每次重建，运行时消费点按需调用；启动装配用模块级快照 | — |
| 键拼写校验 | YAML 源读取时递归校验键，疑似拼写/未知键 fail-fast；业务节点走 `_BUSINESS_KEYS` 白名单；与读取同源仅一次文件读取 | — |

---

*文档基于 `agentscope/examples/agent_service` 实际代码整理，与 `bocomadp/config/` 包结构一致。*
