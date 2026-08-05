# Agent Service

Agent service is a FastAPI-based, multi-tenant and multi-session service built with AgentScope 2.0.

This example demonstrates

- how to set up the agent service with Redis storage, and
- how to launch the service and its companion Web UI

Details about the agent service please refer to the [tutorial](https://docs.agentscope.io/latest/en/deploy/agent-service).

## Prerequisites

- Python ≥ 3.11
- Node.js ≥ 20 with `npx`
- [optional] Gaode/AMap API key in `AMAP_API_KEY` (for the `amap` MCP)

## Quickstart

Install AgentScope from PyPI or source:

```bash
uv pip install agentscope[full]
# or
# uv pip install -e [full]
```

Install Redis and start it as backend storage:

```bash
# macOS (Homebrew)
brew install redis
brew services start redis

# Linux (systemd)
sudo apt install redis-server
sudo systemctl start redis-server

# Docker (cross-platform)
docker run --rm -p 6379:6379 redis:7
```

Start the agent service:

```bash
cd examples/agent_service

python main.py
```

Launch the Web UI in a separate terminal to experience a chat-style interface:

```bash
cd examples/web_ui/

pnpm install
# or npm install

# Run in dev mode
pnpm dev
```

After that, you can set the API endpoint `http://localhost:8000` in the Web UI and start experiencing the agent service.

<img src="https://gw.alicdn.com/imgextra/i2/O1CN01Phmg1G1brIVC8WXyU_!!6000000003518-2-tps-2938-1736.png" alt="Web UI Screenshot" width="100%">

## What Next

- You can customize the service in `main.py` by adding your own MCPs, middlewares, or workspace manager implementations.

- Experience the agent service, including
    - human-in-the-loop interactions & permission system
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01vGGiBw20agWwpzmjy_!!6000000006866-2-tps-2934-1732.png" alt="Permission System" width="100%">

    - schedule tasks
<img src="https://gw.alicdn.com/imgextra/i1/O1CN01Xi3Qw71E2haKKu4z0_!!6000000000294-2-tps-2932-1738.png" alt="Schedule Tasks" width="100%">

    - and more! (stay tuned for future updates)

## Enterprise Extension (`bankcomm_adp`)

本示例在官方入口之上叠加了一个企业扩展包 `bankcomm_adp/`，承载企业内部智能体
平台所需的基础能力，同时保持与官方 `web_ui` 和 `Docker-agentscope` 启动脚本完全兼容：

| 能力 | 位置 | 说明 |
|---|---|---|
| 审计留痕 | `bankcomm_adp/middlewares/audit.py` | 记录每次 agent 调用（谁、何时、用了哪些工具、输出摘要），以 JSONL 写入 `ADP_AUDIT_LOG_PATH` |
| 数据脱敏（DLP） | `bankcomm_adp/middlewares/dlp.py` | 对发往模型的输入做手机号 / 身份证 / 银行卡号掩码 |
| 企业内部工具 | `bankcomm_adp/tools/` | HR / 内部文档库 / ITSM 工单 占位实现，可替换为真实系统调用 |
| 平台健康检查 | `bankcomm_adp/routers/health.py` | `GET /platform/health` 返回服务状态 |

认证保持官方默认的 `X-User-ID` 头方式，前端 `examples/web_ui` 无需任何改动。

### 配置项（前缀 `ADP_`，可选，见 `.env.example`）

```bash
ADP_APP_NAME="交通银行智能体平台"   # Web UI 展示的应用名
ADP_AUDIT_ENABLED=true             # 审计留痕开关
ADP_AUDIT_LOG_PATH=./logs/audit.jsonl
ADP_DLP_ENABLED=true               # 数据脱敏开关
# ADP_WORKSPACE_DIR=./workspaces   # 工作区目录
```

### 用 Docker 启动（推荐）

`Docker-agentscope/docker-compose.yml` 已适配企业扩展：它会将
`examples/agent_service/bankcomm_adp` 一并挂载进容器，`main.py` 可直接 import。
参考 `Docker-agentscope/` 下的 `README` 与 `compose-images.sh` 构建并启动。