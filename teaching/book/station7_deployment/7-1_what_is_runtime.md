# 7-1 如何部署Agent服务

> **目标**：理解如何使用Quart框架将Agent部署为HTTP服务

---

## 🎯 这一章的目标

学完之后，你能：
- 使用Quart框架部署Agent为HTTP服务
- 理解本地运行和HTTP服务运行的区别
- 实现流式响应

---

## 🚀 部署是什么

### 本地运行 vs HTTP服务运行

```python showLineNumbers
# 本地运行 - 开发阶段
agent = ReActAgent(name="Assistant", ...)
result = await agent("你好")
# 直接调用，适合开发和调试

# HTTP服务运行 - 生产阶段
from quart import Quart, Response, request

app = Quart(__name__)

@app.route("/chat", methods=["POST"])
async def chat():
    data = await request.get_json()
    user_input = data.get("user_input")

    # 调用Agent处理请求
    response = await agent(user_input)
    return {"content": response.content}

app.run(port=5000)
# 变成HTTP服务，可以通过HTTP调用
```

---

## 🔍 部署的作用

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
│                    HTTP服务运行                             │
│                                                             │
│  Agent定义 ──► Quart打包 ──► HTTP服务 ──► API调用        │
│                                                             │
│  适合：生产部署、规模化                                       │
└─────────────────────────────────────────────────────────────┘
```

---

## 🔬 关键代码段解析

### 代码段1：为什么需要HTTP服务？

```python showLineNumbers
# 本地运行 - 开发阶段
agent = ReActAgent(name="Assistant", ...)
result = await agent("你好")  # 直接调用，阻塞式

# HTTP服务运行 - 生产阶段
from quart import Quart, Response, request

app = Quart(__name__)

@app.route("/chat", methods=["POST"])
async def chat():
    data = await request.get_json()
    user_input = data.get("user_input")
    response = await agent(user_input)
    return {"content": response.content}

app.run(port=5000)  # 变成服务，不阻塞
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| 为什么不一直用本地运行？ | 本地运行只能处理一个请求，不能同时服务多人 |
| `app.run(port=5000)`是什么意思？ | 启动HTTP服务监听5000端口 |
| 为什么用POST？ | 因为发送消息给Agent |

```
┌─────────────────────────────────────────────────────────────┐
│           本地运行 vs HTTP服务运行                         │
│                                                             │
│   本地运行：                                                │
│   ┌─────────────────────────────────────────────────┐    │
│   │ 你的电脑                                            │    │
│   │                                                    │    │
│   │ python my_agent.py                                │    │
│   │       │                                           │    │
│   │       ▼                                           │    │
│   │   你输入"你好" ──► Agent处理 ──► 显示结果       │    │
│   │                                                    │    │
│   │   只能一个人用，关闭程序就结束                      │    │
│   └─────────────────────────────────────────────────┘    │
│                                                             │
│   HTTP服务运行：                                             │
│   ┌─────────────────────────────────────────────────┐    │
│   │  服务器                                              │    │
│   │                                                    │    │
│   │  python run_agent.py                              │    │
│   │       │                                           │    │
│   │       ▼                                           │    │
│   │  Quart监听 5000 端口                              │    │
│   │        │                                           │    │
│   │   ┌────┴────┬─────────┬─────────┐                │    │
│   │   │         │         │         │                 │    │
│   │   ▼         ▼         ▼         ▼                 │    │
│   │ 用户A    用户B    用户C    用户D...              │    │
│   │                                                    │    │
│   │  同时服务多人，7x24小时运行                        │    │
│   └─────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：HTTP服务解决**规模化问题**。本地运行适合开发调试，HTTP服务运行适合生产服务。

---

### 代码段2：HTTP服务启动后如何调用？

```python showLineNumbers
# 客户端代码
import requests

# 调用HTTP服务部署的Agent
response = requests.post(
    "http://localhost:5000/chat",  # HTTP API地址
    json={"user_input": "你好"}
)

print(response.json()["content"])
```

**思路说明**：

| 问题 | 答案 |
|------|------|
| `http://localhost:5000/chat`是什么？ | HTTP服务暴露的API |
| 怎么知道有哪些API？ | 访问 `http://localhost:5000/` 看文档 |
| 为什么用POST？ | 因为发送消息给Agent |

```
┌─────────────────────────────────────────────────────────────┐
│              HTTP服务的API调用                             │
│                                                             │
│  客户端                                                    │
│  ┌─────────────────────────────────────────────────┐    │
│  │ requests.post(                                  │    │
│  │     "http://localhost:5000/chat",              │    │
│  │     json={"user_input": "你好"}               │    │
│  │ )                                              │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                                │
│                         ▼ HTTP请求                        │
│  ┌─────────────────────────────────────────────────┐    │
│  │           Quart HTTP服务 (port 5000)              │    │
│  │                                                 │    │
│  │  POST /chat ──► Agent处理 ──► 返回              │    │
│  │                                                 │    │
│  └─────────────────────────────────────────────────┘    │
│                         │                                │
│                         ▼ HTTP响应                        │
│                    {"content": "你好！有什么帮助？"}      │
└─────────────────────────────────────────────────────────────┘
```

**💡 设计思想**：HTTP服务通过REST API暴露Agent能力。任何能发HTTP请求的客户端都能调用Agent。

---

### 代码段3：完整部署示例

```python showLineNumbers
# run_agent.py - 完整的Agent服务
import os
from quart import Quart, Response, request

from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.message import Msg

app = Quart(__name__)

# 在模块级别创建Agent（只创建一次）
agent = ReActAgent(
    name="Assistant",
    model=OpenAIChatModel(
        api_key=os.environ.get("OPENAI_API_KEY"),
        model="gpt-4"
    ),
    sys_prompt="你是一个友好的AI助手。"
)

@app.route("/chat", methods=["POST"])
async def chat():
    """处理用户消息"""
    data = await request.get_json()
    user_input = data.get("user_input", "")

    # 调用Agent
    response = await agent(user_input)

    return {"content": response.content}

if __name__ == "__main__":
    app.run(port=5000, debug=True)
```

**部署步骤**：

```bash
# 1. 设置API Key
export OPENAI_API_KEY="sk-xxx"

# 2. 运行服务
python run_agent.py

# 3. 测试
curl -X POST http://localhost:5000/chat \
  -H "Content-Type: application/json" \
  -d '{"user_input": "你好"}'
```

---

## 💡 Java开发者注意

HTTP服务类似Java的**Spring Boot**：

| Python/Quart | Java/Spring | 说明 |
|------------|------|------|
| `@app.route("/chat")` | `@PostMapping("/chat")` | HTTP端点 |
| `await request.get_json()` | `@RequestBody` | 获取请求数据 |
| `return {"content": "..."}` | `return new ResponseEntity<>("...")` | 返回响应 |
| `app.run(port=5000)` | `spring-boot.run()` | 启动服务 |

---

## 🎯 思考题

<details>
<summary>点击查看答案</summary>

1. **本地运行和HTTP服务运行的区别？**
   - 本地：直接执行，适合开发
   - HTTP服务：变成服务，适合部署

2. **HTTP服务解决了什么问题？**
   - 规模化：同时处理多个请求
   - 远程调用：通过HTTP访问
   - 跨平台：任何语言都能调用

</details>

---

★ **Insight** ─────────────────────────────────────
- **Quart = Python的轻量级Web框架**，把Agent变成可部署的服务
- 开发用本地运行，生产用HTTP服务运行
- HTTP API让Agent可以被任何客户端调用
─────────────────────────────────────────────────
