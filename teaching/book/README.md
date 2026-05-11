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

## 自包含原则

本书面向**离线读者**——内测用户可能没有网络访问。因此：

1. **不依赖外部链接**：所有参考资料的内容必须**直接融入正文**，不能仅放一个 URL
2. **官方文档内嵌**：原来放在"官方文档对照"侧边栏中的 docs.agentscope.io 内容，改为在正文中以"官方文档的用法是……"自然融入
3. **论文核心观点摘录**：原来放在"推荐阅读"中的 AgentScope 论文引用，改为在对应位置直接摘录论文中的关键段落（1-3 句），并注明"来自 AgentScope 1.0 论文"
4. **外部教程要点提取**：MarkTechPost 等教程的关键示例代码和要点，直接写进正文或"试一试"环节
5. **视频内容文字化**：Bilibili 视频引用改为文字说明其核心内容
6. **技术规范内嵌**：PEP、Python 官方文档等引用，把相关规范条款直接摘录到"知识补全"或正文

**具体做法**：

| 原来的形式 | 改为 |
|-----------|------|
| `> **官方文档对照**：本章对应 [URL]...` | 删除整个侧边栏，把官方文档的用法示例直接写进正文（如"官方文档建议的配置方式是……"） |
| `> **推荐阅读**：[论文 URL] 第 X 节讨论了……` | 删除整个侧边栏，在正文中摘录论文原文（1-3 句）并标注出处 |
| `> **推荐阅读**：[MarkTechPost URL] Part X 展示了……` | 删除整个侧边栏，把教程中的关键示例代码直接写进"试一试"或正文 |
| `> **推荐阅读**：[Bilibili URL]` | 删除整个侧边栏，用文字描述视频核心内容 |
| `> **推荐阅读**：[PEP URL]` | 把 PEP 的关键规范条款摘录到"知识补全"节 |

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
