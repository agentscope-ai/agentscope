# Docker 生产部署

> **Level 7**: 能独立开发模块
> **前置要求**: [Runtime 服务化](./10-runtime.md)
> **后续章节**: [天气 Agent 项目](../11-projects/11-weather-agent.md)

---

## 学习目标

学完本章后，你能：
- 使用 Docker 容器化 AgentScope 应用
- 配置生产环境变量和卷
- 优化 Docker 镜像大小
- 实现健康检查和日志收集

---

## 背景问题

如何在生产环境中部署 AgentScope 应用？需要：
1. 容器化（Docker）
2. 环境隔离
3. 资源限制
4. 日志和监控

---

## 源码入口

| 项目 | 值 |
|------|-----|
| **参考配置** | `pyproject.toml` (依赖声明), `examples/` (可部署示例) |
| **Runtime 基类** | `src/agentscope/agent/_agent_base.py` (Agent 实例化入口) |
| **Session 持久化** | `src/agentscope/session/` (JSON/Redis/Tablestore) |

---

---

## Dockerfile 最佳实践

### 基本镜像

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 非 root 用户运行
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 5000

CMD ["python", "-m", "quart", "app", "--host", "0.0.0.0"]
```

### 多阶段构建

```dockerfile
# 构建阶段
FROM python:3.10-slim AS builder
RUN pip install --user build
COPY requirements.txt .
RUN pip install --user -r requirements.txt

# 运行阶段
FROM python:3.10-slim
WORKDIR /app
COPY --from=builder /root/.local /root/.local
COPY . .
ENV PATH=/root/.local:$PATH
CMD ["python", "-m", "quart", "app"]]
```

---

## Docker Compose 配置

### 开发环境

```yaml
version: '3.8'

services:
  agent:
    build: .
    ports:
      - "5000:5000"
    environment:
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - LOG_LEVEL=DEBUG
    volumes:
      - ./sessions:/app/sessions
    command: python -m quart app --debug
```

### 生产环境

```yaml
version: '3.8'

services:
  agent:
    build: .
    ports:
      - "5000:5000"
    environment:
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - LOG_LEVEL=INFO
    volumes:
      - agent_sessions:/app/sessions
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  agent_sessions:
```

---

## 生产环境配置

### 环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `DASHSCOPE_API_KEY` | 阿里云 API Key | 是 |
| `OPENAI_API_KEY` | OpenAI API Key | 是 |
| `LOG_LEVEL` | 日志级别 | 否 |
| `SESSION_DIR` | Session 存储目录 | 否 |

### 健康检查

```python
@app.route("/health", methods=["GET"])
async def health():
    return {"status": "healthy"}
```

### 日志配置

```python
import logging
import sys

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO"),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
```

---

## 资源限制

```yaml
services:
  agent:
    deploy:
      resources:
        limits:
          cpus: '2'
          memory: 2G
        reservations:
          cpus: '0.5'
          memory: 512M
```

---

## CI/CD 集成

### GitHub Actions

```yaml
name: Build and Deploy

on:
  push:
    branches: [main]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Build Docker image
        run: docker build -t agentscope:${{ github.sha }} .

      - name: Run tests
        run: docker run agentscope:${{ github.sha }} pytest

      - name: Push to registry
        run: |
          docker push registry/agentscope:${{ github.sha }}
```

---

## 工程现实与架构问题

### 技术债 (源码级)

| 位置 | 问题 | 影响 | 优先级 |
|------|------|------|--------|
| `Dockerfile` | 无镜像大小优化 | 镜像过大影响部署速度 | 中 |
| `docker-compose.yml` | Session 卷无备份机制 | 数据丢失风险 | 高 |
| `Dockerfile` | 非 root 用户配置缺失 | 安全风险 | 中 |
| `docker-compose.yml` | 无资源限制 | 单一容器耗尽系统资源 | 中 |
| `Dockerfile` | 无健康检查 | 容器不健康时无自动恢复 | 中 |

**[HISTORICAL INFERENCE]**: Docker 配置主要面向开发环境，生产环境需要的安全加固、资源限制、健康检查是后来发现的需求。

### 性能考量

```python
# Docker 镜像大小估算
python:3.10-slim 基础: ~150MB
pip 安装依赖: ~200-500MB (取决于依赖数量)
应用代码: ~10-50MB
最终镜像: ~400-700MB

# 优化后镜像大小
多阶段构建: ~200-300MB
 Alpine 基础: ~100MB
```

### Session 数据安全

```yaml
# 当前问题: Session 卷无备份，定期备份缺失
services:
  agent:
    volumes:
      - agent_sessions:/app/sessions  # 无备份机制

# 解决方案: 添加备份策略
services:
  agent:
    volumes:
      - agent_sessions:/app/sessions

  backup:
    image: agentscope:latest
    volumes:
      - agent_sessions:/app/sessions
      - ./backups:/app/backups
    command: python scripts/backup_sessions.py
    restart: daily  # 每日备份
```

### 渐进式重构方案

```dockerfile
# 方案 1: 优化后的 Dockerfile
FROM python:3.10-slim AS builder

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# 运行阶段
FROM python:3.10-slim

# 安装运行时依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /root/.local /root/.local
ENV PATH=/root/.local/bin:$PATH

# 非 root 用户
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

EXPOSE 5000
CMD ["python", "-m", "quart", "app", "--host", "0.0.0.0"]
```

---

## 常见问题

**问题：镜像过大**
- 使用 `python:3.10-slim` 而非完整镜像
- 使用多阶段构建
- 清理 pip 缓存

**问题：API Key 安全**
- 使用 Docker secrets 或环境变量
- 不要在镜像中硬编码

**问题：Session 丢失**
- 使用持久化卷（Docker volumes）
- 定期备份

### 危险区域

1. **非 root 用户缺失**：安全风险，可能被利用
2. **无健康检查**：容器故障无法自动检测
3. **无资源限制**：可能影响宿主机其他服务

---

## 下一步

接下来学习 [天气 Agent 项目](../11-projects/11-weather-agent.md)。


