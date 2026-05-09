# 第15章 Docker部署

> **目标**：掌握AgentScope服务的容器化部署

---

## 🎯 学习目标

学完之后，你能：
- 创建Docker镜像
- 编写docker-compose配置
- 环境变量管理
- 健康检查与监控

---

## 🚀 Dockerfile

```dockerfile
FROM python:3.10-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

CMD ["python", "-m", "agentscope", "serve"]
```

---

## docker-compose

```yaml
version: '3.8'
services:
  agentscope:
    build: .
    ports:
      - "5000:5000"
    environment:
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
```

---

★ **Insight** ─────────────────────────────────────
- **Docker** = 标准化部署环境
- **docker-compose** = 多服务编排
- **环境变量** = 敏感信息管理
─────────────────────────────────────────────────
