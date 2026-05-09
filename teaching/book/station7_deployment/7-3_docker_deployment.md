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

## 🔬 关键代码段解析

### 代码段1：Dockerfile的核心组成

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

**思路说明**：

| 指令 | 作用 | 类似于 |
|------|------|--------|
| FROM | 选择基础镜像 | Java的maven/gradle基础镜像 |
| WORKDIR | 设置工作目录 | cd命令 |
| COPY | 复制文件 | cp命令 |
| RUN | 执行命令 | shell命令 |
| CMD | 容器启动命令 | java -jar |

```
┌─────────────────────────────────────────────────────────────┐
│              Dockerfile构建流程                            │
│                                                             │
│   FROM python:3.10-slim                                   │
│        │                                                 │
│        ▼                                                 │
│   WORKDIR /app  ──► 创建工作目录                       │
│        │                                                 │
│        ▼                                                 │
│   COPY requirements.txt .  ──► 复制依赖文件             │
│        │                                                 │
│        ▼                                                 │
│   RUN pip install  ──► 安装Python依赖                   │
│        │                                                 │
│        ▼                                                 │
│   COPY agent.py .  ──► 复制应用代码                     │
│        │                                                 │
│        ▼                                                 │
│   CMD ["python", "agent.py"]  ──► 容器启动命令         │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Dockerfile是**构建配方**——描述如何从零开始构建一个包含应用和环境的镜像。

---

### 代码段2：docker build的完整过程

```bash showLineNumbers
# 构建镜像
docker build -t my-agent .

# 参数说明
# -t my-agent  ──► 给镜像起名
# .             ──► 使用当前目录的Dockerfile
```

**思路说明**：

| 步骤 | 操作 | 说明 |
|------|------|------|
| 1 | 读取Dockerfile | 从当前目录查找 |
| 2 | 执行每条指令 | 每条指令创建一层 |
| 3 | 生成镜像 | 存储在本地Docker引擎 |

```
┌─────────────────────────────────────────────────────────────┐
│              docker build 构建过程                        │
│                                                             │
│   docker build -t my-agent .                              │
│        │                                                 │
│        ▼                                                 │
│   Step 1: FROM python:3.10-slim  ──► 拉取基础镜像      │
│   Step 2: WORKDIR /app            ──► 创建层            │
│   Step 3: COPY requirements.txt   ──► 创建层            │
│   Step 4: RUN pip install        ──► 创建层            │
│   Step 5: COPY agent.py         ──► 创建层            │
│   Step 6: CMD [...]             ──► 创建层            │
│        │                                                 │
│        ▼                                                 │
│   镜像: my-agent:latest                                │
│                                                             │
│   每条指令都会创建一个新的镜像层                       │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Docker镜像是**分层构建**的——每条指令产生一层，层叠叠加形成完整镜像。

---

### 代码段3：docker run的完整过程

```bash showLineNumbers
# 运行容器
docker run -p 5000:5000 \
    -e OPENAI_API_KEY="sk-xxx" \
    my-agent

# 参数说明
# -p 5000:5000  ──► 端口映射（主机:容器）
# -e KEY=VALUE  ──► 设置环境变量
# my-agent      ──► 镜像名
```

**思路说明**：

| 参数 | 作用 | 说明 |
|------|------|------|
| -p 5000:5000 | 端口映射 | 主机5000 → 容器5000 |
| -e OPENAI_API_KEY | 环境变量 | 传递给容器的配置 |
| my-agent | 镜像名 | 指定要运行的镜像 |

```
┌─────────────────────────────────────────────────────────────┐
│              docker run 运行流程                          │
│                                                             │
│   docker run -p 5000:5000 -e API_KEY=xxx my-agent       │
│        │                                                 │
│        ▼                                                 │
│   1. 创建容器（基于my-agent镜像）                      │
│        │                                                 │
│        ▼                                                 │
│   2. 设置端口映射（5000:5000）                         │
│        │                                                 │
│        ▼                                                 │
│   3. 设置环境变量（API_KEY=xxx）                        │
│        │                                                 │
│        ▼                                                 │
│   4. 执行CMD启动应用                                     │
│        │                                                 │
│        ▼                                                 │
│   容器内: python agent.py                               │
│                                                             │
│   主机访问: http://localhost:5000                       │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：容器是镜像的**运行时实例**——镜像只读，容器可写。端口映射让外部能访问容器内的服务。

---

### 代码段4：完整部署流程

```bash showLineNumbers
# 1. 本地构建镜像
docker build -t my-agent .

# 2. 本地测试运行
docker run -p 5000:5000 -e OPENAI_API_KEY="sk-xxx" my-agent

# 3. 推送到镜像仓库（可选）
docker tag my-agent registry.example.com/my-agent:latest
docker push registry.example.com/my-agent:latest

# 4. 服务器拉取并运行
docker pull registry.example.com/my-agent:latest
docker run -p 5000:5000 -e OPENAI_API_KEY="sk-xxx" \
    registry.example.com/my-agent:latest
```

**思路说明**：

| 阶段 | 命令 | 说明 |
|------|------|------|
| 构建 | `docker build` | 本地构建镜像 |
| 测试 | `docker run` | 本地运行测试 |
| 发布 | `docker push` | 推送到仓库 |
| 部署 | `docker pull + run` | 服务器运行 |

```
┌─────────────────────────────────────────────────────────────┐
│              完整部署流程                                │
│                                                             │
│   本地开发 ──► docker build ──► 镜像                    │
│        │                                    │              │
│        │                                    ▼              │
│        │                              docker push          │
│        │                                    │              │
│        │                                    ▼              │
│        │                              镜像仓库             │
│        │                                    │              │
│        │                                    ▼              │
│        │                              docker pull          │
│        │                                    │              │
│        └──────────────────────────────────┘              │
│                                                             │
│   服务器: docker run ──► 容器 ──► 服务运行           │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：Docker实现**一处构建，到处运行**——构建一次镜像，可以在任何有Docker的地方运行。

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
