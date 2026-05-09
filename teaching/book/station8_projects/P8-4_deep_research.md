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
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.tool import Toolkit, ToolResponse

# 1. 定义搜索工具函数
def web_search(query: str) -> ToolResponse:
    """搜索网络获取相关信息"""
    # 实际项目中调用搜索API
    result = f"关于'{query}'的搜索结果..."
    return ToolResponse(result=result)

# 2. 初始化
agentscope.init(project="DeepResearch")

# 3. 创建工具箱并注册
toolkit = Toolkit()
toolkit.register_tool_function(web_search, group_name="search")

# 4. 创建研究Agent
agent = ReActAgent(
    name="Researcher",
    model=OpenAIChatModel(api_key="your-key", model="gpt-4"),
    sys_prompt="""你是一个专业的研究助手。
    收到研究主题后：
    1. 搜索相关信息
    2. 分析整理
    3. 输出结构化报告""",
    toolkit=toolkit
)

# 5. 运行
import asyncio

async def main():
    response = await agent("请研究人工智能对教育行业的影响")
    print(f"研究报告: {response.content}")

asyncio.run(main())
```

---

---

## 🔍 代码解读

### 1. 搜索工具定义

```python showLineNumbers
def web_search(query: str) -> ToolResponse:
    """搜索网络获取相关信息"""
    result = f"关于'{query}'的搜索结果..."
    return ToolResponse(result=result)
```

**设计要点**：
- 工具函数签名：输入是搜索词，输出是`ToolResponse`
- 实际项目中应调用真实搜索API
- 可以扩展支持更多搜索参数

---

### 2. 研究Agent配置

```python showLineNumbers
agent = ReActAgent(
    name="Researcher",
    model=OpenAIChatModel(api_key="your-key", model="gpt-4"),
    sys_prompt="""你是一个专业的研究助手。
    收到研究主题后：
    1. 搜索相关信息
    2. 分析整理
    3. 输出结构化报告""",
    toolkit=toolkit
)
```

**设计要点**：
- `sys_prompt` 定义了研究流程
- Agent会自动决定何时调用搜索工具
- 多次搜索可以收集更全面的信息

---

## 🔬 项目实战思路分析

### 项目结构

```
deep_research/
├── P8-4_deep_research.py    # 主程序
├── tools.py                   # 工具函数
└── README.md                # 说明文档
```

### 开发步骤

```
Step 1: 定义搜索工具
        ↓
Step 2: 创建工具箱
        ↓
Step 3: 创建研究Agent
        ↓
Step 4: 测试运行
```

### ReAct在研究场景的应用

```
┌─────────────────────────────────────────────────────────────┐
│              ReAct研究循环                                 │
│                                                             │
│   Thought: "用户想知道AI对教育的影响，我需要搜索相关信息"    │
│       │                                                    │
│       ▼                                                    │
│   Action: web_search("人工智能 教育 影响")                   │
│       │                                                    │
│       ▼                                                    │
│   Observation: "搜索到100条结果..."                         │
│       │                                                    │
│       ▼                                                    │
│   Thought: "信息不够全面，我再搜索具体案例"                 │
│       │                                                    │
│       ▼                                                    │
│   Action: web_search("AI教育案例 成功故事")                 │
│       │                                                    │
│       ▼                                                    │
│   Observation: "找到更多案例..."                            │
│       │                                                    │
│       ▼                                                    │
│   Thought: "现在有足够信息写报告了"                         │
│       │                                                    │
│       ▼                                                    │
│   Final Response: [结构化研究报告]                          │
└─────────────────────────────────────────────────────────────┘
```

### 扩展工具集

```python showLineNumbers
# 更完善的研究工具集
toolkit = Toolkit()

# 网页搜索
toolkit.register_tool_function(web_search, group_name="search")

# 读取网页内容
toolkit.register_tool_function(read_webpage, group_name="search")

# 翻译工具
toolkit.register_tool_function(translate_text, group_name="util")

# 摘要生成
toolkit.register_tool_function(summarize, group_name="util")
```

---

## 🐛 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| 搜索结果不准确 | 关键词不精确 | 优化搜索策略 |
| 信息过时 | 网络数据时效性 | 添加时间筛选 |
| 报告太长 | 没有限制长度 | 在prompt中限制字数 |

---

## 🎯 扩展思考

1. **如何实现自动摘要？**
   - 使用更强大的LLM进行摘要
   - 分段处理长文档

2. **如何添加数据可视化？**
   - 研究报告加入图表
   - 调用绘图工具生成图片

3. **如何支持持续追踪？**
   - 定期自动更新研究
   - 监控信息变化并提醒

4. **如何实现多源交叉验证？**
   - 从多个来源收集信息
   - 标记信息冲突或矛盾

---

★ **项目总结** ─────────────────────────────────────
- 学会了构建研究Agent实现多步骤信息收集
- 理解了WebSearch与Agent的集成
- 掌握了结构化报告生成的方法
- 完成了深度研究助手项目
─────────────────────────────────────────────────
