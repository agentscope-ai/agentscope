# 7-1 Runtime是什么

> **目标**：理解AgentScope Runtime如何把代码变成可部署的服务

---

## 🎯 这一章的目标

学完之后，你能：
- 理解Runtime的作用
- 使用Runtime部署Agent服务
- 理解本地运行和Runtime运行的区别

---

## 🚀 Runtime是什么

### 本地运行 vs Runtime运行

```python showLineNumbers
# 本地运行 - 开发阶段
agent = ReActAgent(name="Assistant", ...)
result = await agent("你好")
# 直接运行，适合开发和调试

# Runtime运行 - 生产阶段
from agentscope.runtime import AgentScopeRuntime

runtime = AgentScopeRuntime(
    agents=[agent],
    host="0.0.0.0",
    port=5000
)
runtime.start()
# 变成服务，可以通过HTTP调用
```

---

## 🔍 Runtime的作用

```
┌─────────────────────────────────────────────────────────────┐
│                    本地运行                                 │
│                                                             │
│  Python脚本 ──► 直接执行 ──► 结果                          │
│                                                             │
│  适合：开发、调试                                           │
└─────────────────────────────────────────────────────────────┘

                    ↓ 变成

┌─────────────────────────────────────────────────────────────┐
│                    Runtime运行                             │
│                                                             │
│  Agent定义 ──► Runtime打包 ──► HTTP服务 ──► API调用        │
│                                                             │
│  适合：生产部署、规模化                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 💡 Java开发者注意

Runtime类似Java的**应用服务器**：

| AgentScope | Java | 说明 |
|------------|------|------|
| Runtime | Tomcat/Jetty | 应用服务器 |
| agent.start() | application.run() | 启动应用 |
| HTTP API | REST API | 远程调用 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **本地运行和Runtime运行的区别？**
   - 本地：直接执行，适合开发
   - Runtime：变成服务，适合部署

2. **Runtime解决了什么问题？**
   - 规模化：同时处理多个请求
   - 远程调用：通过HTTP访问
   - 服务管理：健康检查、监控

</details>

---

★ **Insight** ─────────────────────────────────────
- **Runtime = 应用服务器**，把Agent变成可部署的服务
- 开发用本地运行，生产用Runtime运行
─────────────────────────────────────────────────
