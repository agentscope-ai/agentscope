# 第 27 章 高级扩展

> 本章你将：组合多种扩展，创建复杂的 Agent 系统。

> **源码验证日期**: 2026-05-11, commit `f17cfd0a`

---

## 27.1 组合扩展

前几章我们分别创建了 Tool、Model、Memory、Agent。现在组合它们：

```python
# 自定义工具
toolkit = Toolkit()
toolkit.register_tool_function(calculator)
toolkit.register_mcp_client(mcp_client)  # MCP 工具

# 自定义记忆
memory = FileMemory("chat_history.json")

# 自定义模型
model = MyCustomModel(model_name="my-model", stream=True)

# 组合成 Agent
agent = ReActAgent(
    name="super_agent",
    model=model,
    toolkit=toolkit,
    memory=memory,
    ...
)
```

---

## 27.2 多 Agent 协作

```python
from agentscope.pipeline import MsgHub

# 创建多个 Agent
researcher = ReActAgent(name="researcher", ...)
writer = ReActAgent(name="writer", ...)

# 建立订阅关系
researcher.subscribe(writer)

# 或使用 MsgHub 管理多 Agent 通信
```

---

## 27.3 试一试

1. 创建一个"研究-写作"双 Agent 系统
2. 让一个 Agent 专注搜索，另一个专注总结
3. 用订阅机制让它们协作

---

## 27.4 检查点

你现在已经能：

- 组合自定义 Tool + Model + Memory + Agent
- 设置多 Agent 协作
- 用订阅机制建立通信

---

## 下一章预告
