# 7-3 怎么做Docker部署

> **目标**：理解如何使用Docker容器化Agent服务

---

## 🎯 这一章的目标

学完之后，你能：
- 创建Agent的Docker镜像
- 运行Docker容器
- 部署Agent服务

---

## 🚀 Docker部署示例

### 第一步：创建Dockerfile

```dockerfile showLineNumbers
# Dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装依赖
COPY requirements.txt .
RUN pip install -r requirements.txt

# 复制代码
COPY agent.py .

# 运行
CMD ["python", "agent.py"]
```

### 第二步：构建镜像

```bash
docker build -t my-agent .
```

### 第三步：运行容器

```bash
docker run -p 5000:5000 \
    -e OPENAI_API_KEY="sk-xxx" \
    my-agent
```

---

## 🔍 Docker部署流程

```
┌─────────────────────────────────────────────────────────────┐
│                    Docker部署流程                           │
│                                                             │
│  Python代码 ──► Dockerfile ──► 镜像 ──► 容器 ──► 服务    │
│                                                             │
│     │              │              │           │            │
│     ▼              ▼              ▼           ▼            │
│  agent.py     构建镜像       docker build   docker run     │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 💡 Java开发者注意

Docker类似Java的**WAR包部署到Tomcat**：

| Docker | Java | 说明 |
|--------|------|------|
| Dockerfile | build.xml/pom.xml | 构建配置 |
| docker build | mvn package | 构建 |
| docker run | java -jar app.jar | 运行 |
| 容器 | JVM进程 | 运行环境 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **Docker的优势是什么？**
   - 环境一致：开发环境和生产环境相同
   - 隔离：容器之间互不影响
   - 轻量：比虚拟机启动更快

2. **为什么要用Docker部署Agent？**
   - 环境配置简单
   - 易于扩展
   - 跨平台部署

</details>

---

★ **Insight** ─────────────────────────────────────
- **Docker = 集装箱**，把应用和环境打包在一起
- 一处构建，到处运行
─────────────────────────────────────────────────
