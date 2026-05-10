# AgentScope 源码之旅——从零基础到架构贡献者

> 一本源码分析书。不需要 Agent 框架经验，只需要 Python 基础。
> 读完你会：读懂源码 → 能改源码 → 能造新模块 → 能参与架构讨论。

## 前置知识

- Python 基础：函数、类、列表/字典
- 不需要：LLM API 经验、Agent 框架经验、async/await、设计模式
- 所有进阶知识在书中逐步引入

## 如何使用本书

1. **clone 仓库**：`git clone https://github.com/modelscope/agentscope.git`
2. **安装开发模式**：`cd agentscope && pip install -e .`
3. **边读边改**：每章都有"试一试"环节，改一行源码观察变化

## 五卷简介

| 卷 | 你会获得什么 | 章节 |
|----|------------|------|
| **卷零：出发前的地图** | 理解 LLM 和 Agent 是什么，能跑通第一个 Agent | ch01-ch02 |
| **卷一：一次 agent() 调用的旅程** | 能追踪请求流程、定位 bug、修改源码验证理解 | ch03-ch12 |
| **卷二：拆开每个齿轮** | 能理解设计模式、读懂任意模块的代码组织 | ch13-ch20 |
| **卷三：造一个新齿轮** | 能独立添加新功能模块、提交 PR | ch21-ch28 |
| **卷四：为什么要这样设计** | 能参与架构讨论、理解设计权衡 | ch29-ch36 |

## 章节目录

### 卷零：出发前的地图
1. 什么是大模型（LLM）
2. 什么是 Agent

### 卷一：一次 agent() 调用的旅程
3. 准备工具箱
4. 第 1 站：消息诞生
5. 第 2 站：Agent 收信
6. 第 3 站：工作记忆
7. 第 4 站：检索与知识
8. 第 5 站：格式转换
9. 第 6 站：调用模型
10. 第 7 站：执行工具
11. 第 8 站：循环与返回
12. 旅程复盘

### 卷二：拆开每个齿轮
13. 模块系统：文件的命名与导入
14. 继承体系：从 StateModule 到 AgentBase
15. 元类与 Hook：方法调用的拦截
16. 策略模式：Formatter 的多态分发
17. 工厂与 Schema：从函数到 JSON Schema
18. 中间件与洋葱模型
19. 发布-订阅：多 Agent 通信
20. 可观测性与持久化

### 卷三：造一个新齿轮
21. 扩展准备
22. 造一个新 Tool
23. 造一个新 Model Provider
24. 造一个新 Memory Backend
25. 造一个新 Agent 类型
26. 集成 MCP Server
27. 高级扩展：中间件与分组
28. 终章：集成实战

### 卷四：为什么要这样设计
29. 消息为什么是唯一接口
30. 为什么不用装饰器注册工具
31. 上帝类 vs 模块拆分
32. 编译期 Hook vs 运行时 Hook
33. 为什么 ContentBlock 是 Union
34. 为什么用 ContextVar
35. 为什么 Formatter 独立于 Model
36. 架构的全景与边界

### 附录
- Python 进阶知识速查
- 术语表
- 源码文件速查表

## 阅读路径建议

- **线性阅读**：从 ch01 到 ch36，适合完整学习
- **按需跳读**：如果你已经了解 LLM/Agent，直接从 ch03 开始；如果你只想学怎么加新模块，直接看卷三（ch21-ch28）

## 源码版本

基于 AgentScope `main` 分支当前版本。源码会持续演进，书中引用的行号可能需要更新。

## 贯穿示例

全书追踪这个天气查询 Agent 的一次完整调用：

```python
import agentscope
from agentscope.agent import ReActAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.tool import Toolkit
from agentscope.memory import InMemoryMemory

agentscope.init(project="weather-demo")

model = OpenAIChatModel(model_name="gpt-4o", stream=True)
toolkit = Toolkit()
toolkit.register_tool_function(get_weather)

agent = ReActAgent(
    name="assistant",
    sys_prompt="你是天气助手。",
    model=model,
    formatter=OpenAIChatFormatter(),
    toolkit=toolkit,
    memory=InMemoryMemory(),
)

result = await agent(Msg("user", "北京今天天气怎么样？", "user"))
```

从 `await agent(...)` 这一行开始，我们追踪它从执行到返回的完整旅程。
