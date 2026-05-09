# 第14章 Runtime运行时

> **目标**：理解Runtime如何管理Agent生命周期

---

## 🎯 学习目标

学完之后，你能：
- 理解Runtime的作用
- 使用Runtime管理Agent
- 配置运行时参数
- 实现服务的热更新

---

## 🚀 Runtime结构

```python
from agentscope import DS

@DS
class MyRuntime(RuntimeBase):
    def __init__(self):
        self.agents = []
    
    def add_agent(self, agent):
        self.agents.append(agent)
    
    async def run(self):
        for agent in self.agents:
            await agent.start()
```

---

## 💡 Java开发者注意

Runtime类似Java的ApplicationContext或Spring BeanFactory：

```python
# Python Runtime
@DS
class App:
    agents = []
    runtime = Runtime(agents)
    runtime.start()
```

---

★ **Insight** ─────────────────────────────────────
- **Runtime = Agent容器**，管理生命周期
- **@DS装饰器** = 简化Runtime创建
- **start/stop** = 服务的启动/停止
─────────────────────────────────────────────────
