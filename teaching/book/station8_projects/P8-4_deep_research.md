# P8-4 深度研究助手

> **目标**：构建一个能搜索和综合信息的深度研究助手

---

## 📋 需求分析

**我们要做一个**：能搜索网络并综合信息的研究助手

**核心功能**：
1. 接收研究主题
2. 搜索相关信息
3. 综合成结构化报告

---

## 🏗️ 技术方案

```
┌─────────────────────────────────────────────────────────────┐
│                    深度研究架构                              │
│                                                             │
│  研究主题 ──► Agent ──► WebSearch ──► 收集结果            │
│                    │                                        │
│                    └──► 综合分析 ──► 结构化报告            │
└─────────────────────────────────────────────────────────────┘
```

---

## 💻 完整代码

```python showLineNumbers
# P8-4_deep_research.py
import agentscope
from agentscope import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.tool import Tool

# 1. 定义搜索工具
@Tool
def web_search(query: str) -> str:
    """搜索网络获取相关信息"""
    # 实际项目中调用搜索API
    return f"关于'{query}'的搜索结果..."

# 2. 初始化
agentscope.init(project="DeepResearch")

# 3. 创建研究Agent
agent = ReActAgent(
    name="Researcher",
    model=OpenAIChatModel(api_key="your-key", model="gpt-4"),
    sys_prompt="""你是一个专业的研究助手。
    收到研究主题后：
    1. 搜索相关信息
    2. 分析整理
    3. 输出结构化报告""",
    tools=[web_search]
)

# 4. 运行
import asyncio

async def main():
    response = await agent("请研究人工智能对教育行业的影响")
    print(f"研究报告: {response.content}")

asyncio.run(main())
```

---

★ **项目总结** ─────────────────────────────────────
- 学会了构建研究Agent
- 理解了多步骤信息收集流程
- 完成了深度研究助手
─────────────────────────────────────────────────
