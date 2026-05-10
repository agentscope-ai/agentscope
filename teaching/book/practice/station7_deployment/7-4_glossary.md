# 7-4 术语其实很简单

> **目标**：用通俗易懂的话解释部署相关的术语

---

## 📖 术语其实很简单

### **Runtime** = **运行时**

> "就是让代码变成服务的**运行环境**"

**说人话**：本地运行是Python脚本，Runtime运行是HTTP服务

**注意**：`agentscope-runtime` 是与核心 `agentscope` 框架分离的独立包，需要单独安装：
```bash
pip install agentscope-runtime
```

```
本地运行：python agent.py
Runtime运行：agentscope run agent.py
```

---

### **Docker** = **容器**

> "就是**集装箱**，把应用和环境打包在一起"

**说人话**：不管在哪台机器，Docker里运行的环境都一样

```
Docker = 集装箱 = 标准化、可移动、不受环境影响
```

---

### **镜像** = **模板**

> "就是创建容器的**模板**"

**说人话**：镜像像类，容器像实例

```
镜像（Image）= 类 = 模板
容器（Container）= 实例 = 基于模板创建
```

---

### **Dockerfile** = **构建脚本**

> "就是告诉Docker**怎么构建镜像**的脚本"

**说人话**：就像Java的build.xml或pom.xml

```dockerfile
FROM python:3.10-slim
COPY . /app
RUN pip install -r requirements.txt
CMD ["python", "app.py"]
```

---

## 📊 部署流程图

```
┌─────────────────────────────────────────────────────────────┐
│                    部署流程                                │
│                                                             │
│  代码 ──► Dockerfile ──► 镜像 ──► 容器 ──► 服务          │
│   │            │            │           │                  │
│   │            │            │           ▼                  │
│   │            │            │        运行中                 │
│   │            │            │                           │
│   ▼            ▼            ▼                           │
│ 编写         构建         docker build                   │
│                                             docker run    │
└─────────────────────────────────────────────────────────────┘
```

---

## 📊 术语对照表

| Docker | 说人话 | Java | 示例 |
|--------|--------|------|------|
| Dockerfile | 构建脚本 | build.xml | 构建配置 |
| Image | 镜像/模板 | Class | 只读模板 |
| Container | 容器/实例 | Object | 运行实例 |
| Volume | 数据卷 | 持久化存储 | 挂载目录 |
| Registry | 镜像仓库 | Maven仓库 | docker hub |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **镜像和容器的区别？**
   - 镜像是模板，只读的
   - 容器是实例，运行中的

2. **Docker的优势？**
   - 环境一致
   - 隔离性好
   - 易于扩展

3. **agentscope 和 agentscope-runtime 有什么区别？**
   - `agentscope` 是核心框架（Agent、Model、Toolkit等）
   - `agentscope-runtime` 是部署运行时（单独安装，提供HTTP服务能力）

</details>

---

★ **Insight** ─────────────────────────────────────
- **Docker = 集装箱**，标准化打包
- **镜像 = 模板**，创建容器的基础
- **容器 = 实例**，运行中的镜像
- **agentscope-runtime** 是独立部署包，不是核心框架的一部分
─────────────────────────────────────────────────
