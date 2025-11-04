[**English Homepage**](https://github.com/agentscope-ai/agentscope/blob/main/README.md) | [**Tutorial**](https://doc.agentscope.io/zh_CN/) | [**Roadmap**](https://github.com/agentscope-ai/agentscope/blob/main/docs/roadmap.md) | [**FAQ**](https://doc.agentscope.io/zh_CN/tutorial/faq.html)

<p align="center">
  <img
    src="https://img.alicdn.com/imgextra/i1/O1CN01nTg6w21NqT5qFKH1u_!!6000000001621-55-tps-550-550.svg"
    alt="AgentScope Logo"
    width="200"
  />
</p>

<h2 align="center">AgentScope: Agent-Oriented Programming for Building LLM Applications</h2>

<p align="center">
    <a href="https://arxiv.org/abs/2402.14034">
        <img
            src="https://img.shields.io/badge/cs.MA-2402.14034-B31C1C?logo=arxiv&logoColor=B31C1C"
            alt="arxiv"
        />
    </a>
    <a href="https://pypi.org/project/agentscope/">
        <img
            src="https://img.shields.io/badge/python-3.10+-blue?logo=python"
            alt="pypi"
        />
    </a>
    <a href="https://pypi.org/project/agentscope/">
        <img
            src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fagentscope%2Fjson&query=%24.info.version&prefix=v&logo=pypi&label=version"
            alt="pypi"
        />
    </a>
    <a href="https://doc.agentscope.io/">
        <img
            src="https://img.shields.io/badge/Docs-English%7C%E4%B8%AD%E6%96%87-blue?logo=markdown"
            alt="docs"
        />
    </a>
    <a href="https://agentscope.io/">
        <img
            src="https://img.shields.io/badge/GUI-AgentScope_Studio-blue?logo=look&logoColor=green&color=dark-green"
            alt="workstation"
        />
    </a>
    <a href="./LICENSE">
        <img
            src="https://img.shields.io/badge/license-Apache--2.0-black"
            alt="license"
        />
    </a>
</p>

<p align="center">
<img src="https://trendshift.io/api/badge/repositories/10079" alt="modelscope%2Fagentscope | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
</p>

## 📢 新闻
- **[2025-11]** AgentScope 开源 [**Alias-Agent**](https://github.com/agentscope-ai/agentscope-samples/tree/main/alias) 用于处理多样化的真实任务，以及 [**Data-Juicer Agent**](https://github.com/agentscope-ai/agentscope-samples/tree/main/data_juicer_agent) 用于自然语言驱动的数据处理。
- **[2025-11]** AgentScope 通过集成 [Trinity-RFT](https://github.com/modelscope/Trinity-RFT) 实现对 [**Agentic RL**](https://github.com/agentscope-ai/agentscope/tree/main/examples/training/react_agent) 的支持。
- **[2025-11]** AgentScope 集成 [**ReMe**](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/long_term_memory/reme) 增强长期记忆能力。
- **[2025-11]** AgentScope 推出 [**agentscope-samples**](https://github.com/agentscope-ai/agentscope-samples) 样例库，并升级 [**agentscope-runtime**](https://github.com/agentscope-ai/agentscope-runtime) 支持 Docker/K8s 部署和 VNC 驱动的图形化沙盒。
- **[2025-11]** [Contributing Guide](./CONTRIBUTING.md) 已更新，欢迎贡献到 AgentScope！
- **[2025-09]** AgentScope 1.0 **RAG** 模块已上线！欢迎查看 [文档](https://doc.agentscope.io/zh_CN/tutorial/task_rag.html) 和 [样例](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/rag)。
- **[2025-09]** **Voice agent** 已上线！`ReActAgent` 已原生支持 Qwen-Omni 和 GPT-Audio 系列模型，欢迎查看 [样例](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/voice_agent) 和 [roadmap](https://github.com/agentscope-ai/agentscope/issues/773)。
- **[2025-09]** 一个全新功能强大的 📋**Plan** 模块已经上线 AgentScope！查看[文档](https://doc.agentscope.io/zh_CN/tutorial/task_plan.html)了解更多详情。
- **[2025-09]** **AgentScope Runtime** 现已开源！支持沙盒化工具执行的高效智能体部署，助力打造生产级AI应用。查看 [GitHub 仓库](https://github.com/agentscope-ai/agentscope-runtime)。
- **[2025-09]** **AgentScope Studio** 现已开源！查看 [GitHub 仓库](https://github.com/agentscope-ai/agentscope-studio)。
- **[2025-08]** v1 版本 Tutorial 已上线！查看 [tutorial](https://doc.agentscope.io/zh_CN/) 了解更多详情。
- **[2025-08]** 🎉🎉 AgentScope v1现已发布！在完全拥抱异步执行的基础上提供许多新功能和改进。查看 [changelog](https://github.com/agentscope-ai/agentscope/blob/main/docs/changelog.md) 了解详细变更。


## ✨ Why AgentScope？

浅显入门，精深致用。

<p align="center">
<img src="./assets/images/agentscope_v1_0822.png" alt="AgentScope Framework" width="80%"/>
</p>

- **对开发者透明**: 透明是 AgentScope 的**首要原则**。无论提示工程、API调用、智能体构建还是工作流程编排，坚持对开发者可见&可控。拒绝深度封装或隐式魔法。
- **[实时介入](https://doc.agentscope.io/zh_CN/tutorial/task_agent.html#realtime-steering)**: 原生支持**实时**中断和**自定义**中断处理。
- **更智能化**: 支持[智能体工具管理](https://doc.agentscope.io/zh_CN/tutorial/task_tool.html)、[智能体长期记忆控制](https://doc.agentscope.io/zh_CN/tutorial/task_long_term_memory.html)和智能化RAG等。
- **模型无关**: 一次编程，适配所有模型。
- **“乐高式”智能体构建**: 所有组件保持**模块化**且**相互独立**。
- **面向多智能体**：专为**多智能体**设计，**显式**的消息传递和工作流编排，拒绝深度封装。
- **高度可定制**: 工具、提示、智能体、工作流、第三方库和可视化，AgentScope 支持&鼓励开发者进行定制。

AgentScope v1.0 新功能概览:

| 模块         | 功能                                     | 文档                                                                            |
|------------|----------------------------------------|-------------------------------------------------------------------------------|
| model      | 支持异步调用                                 | [Model](https://doc.agentscope.io/zh_CN/tutorial/task_model.html)             |
|            | 支持推理模型                                 |                                                                               |
|            | 支持流式/非流式返回                             |                                                                               |
|            | 支持工具API                                |                                                                               |
| tool       | 支持异步/同步工具函数                            | [Tool](https://doc.agentscope.io/zh_CN/tutorial/task_tool.html)               |
|            | 支持工具函数流式/非流式返回                         |                                                                               |
|            | 支持用户打断                                 |                                                                               |
|            | 支持工具函数的后处理                             |                                                                               |
|            | 支持分组工具管理                               |                                                                               |
|            | 支持智能体通过 Meta Tool 自主管理工具               |                                                                               |
| MCP        | 支持 Streamable HTTP/SSE/StdIO 传输        | [MCP](https://doc.agentscope.io/zh_CN/tutorial/task_mcp.html)                 |
|            | 支持**有状态**和**无状态**两种模式的MCP客户端           |                                                                               |
|            | 支持客户端和函数级别的精细控制                        |                                                                               |
| agent      | 支持异步执行                                 |                                                                               |
|            | 支持并行工具调用                               |                                                                               |
|            | 支持用户实时介入和自定义的中断处理                      |                                                                               |
|            | 支持自动状态管理                               |                                                                               |
|            | 允许智能体自主控制长期记忆                          |                                                                               |
|            | 支持智能体钩子函数                              |                                                                               |
| tracing    | 支持基于 OpenTelemetry 的 LLM、工具、智能体和格式化器追踪 | [Tracing](https://doc.agentscope.io/zh_CN/tutorial/task_tracing.html)         |
|            | 支持连接到第三方追踪平台（如Arize-Phoenix、Langfuse）  |                                                                               |
| memory     | 支持长期记忆                                 | [Memory](https://doc.agentscope.io/zh_CN/tutorial/task_long_term_memory.html) |
| session    | 提供会话/应用级状态管理                           | [Session](https://doc.agentscope.io/zh_CN/tutorial/task_state.html)           |
| evaluation | 提供分布式和并行评估                             | [Evaluation](https://doc.agentscope.io/zh_CN/tutorial/task_eval.html)         |
| formatter  | 支持多Agent提示格式化与工具API                    | [Prompt Formatter](https://doc.agentscope.io/zh_CN/tutorial/task_prompt.html) |
|            | 支持基于截断的格式化策略                           |                                                                               |
| plan       | 支持任务分解和计划制定                            | [Plan](https://doc.agentscope.io/zh_CN/tutorial/task_plan.html)               |
|            | 支持开发者手动设定计划                            |                                                                               |
| RAG        | 支持 agentic RAG                         | [RAG](https://doc.agentscope.io/tutorial/task_rag.html)                       |
|            | 支持多模态 RAG                              |                                                                               |
| ...        |                                        |                                                                               |

## 💬 联系我们

欢迎加入我们的社区，获取最新的更新和支持！

| [Discord](https://discord.gg/eYMpfnkG8h)                                                                                         | 钉钉                                                                                                                              |
|----------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| <img src="https://gw.alicdn.com/imgextra/i1/O1CN01hhD1mu1Dd3BWVUvxN_!!6000000000238-2-tps-400-400.png" width="100" height="100"> | <img src="https://img.alicdn.com/imgextra/i1/O1CN01LxzZha1thpIN2cc2E_!!6000000005934-2-tps-497-477.png" width="100" height="100"> |

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
## 📑 Table of Contents

- [🚀 快速开始](#-%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B)
  - [💻 安装](#-%E5%AE%89%E8%A3%85)
    - [🛠️ 从源码安装](#-%E4%BB%8E%E6%BA%90%E7%A0%81%E5%AE%89%E8%A3%85)
    - [🔄 使用 uv 安装（推荐，安装更快）](#-%E4%BD%BF%E7%94%A8-uv-%E5%AE%89%E8%A3%85%E6%8E%A8%E8%8D%90%E5%AE%89%E8%A3%85%E6%9B%B4%E5%BF%AB)
    - [📦 从PyPi安装](#-%E4%BB%8Epypi%E5%AE%89%E8%A3%85)
- [📝 样例](#-%E6%A0%B7%E4%BE%8B)
  - [👋 Hello AgentScope！](#-hello-agentscope)
  - [🎯 实时介入](#-%E5%AE%9E%E6%97%B6%E4%BB%8B%E5%85%A5)
  - [🛠️ 细粒度 MCP 控制](#-%E7%BB%86%E7%B2%92%E5%BA%A6-mcp-%E6%8E%A7%E5%88%B6)
  - [🧑‍🤝‍🧑 多智能体对话](#-%E5%A4%9A%E6%99%BA%E8%83%BD%E4%BD%93%E5%AF%B9%E8%AF%9D)
  - [💻 AgentScope Studio](#-agentscope-studio)
- [📖 文档](#-%E6%96%87%E6%A1%A3)
- [🤝 贡献](#-%E8%B4%A1%E7%8C%AE)
- [⚖️ 许可](#-%E8%AE%B8%E5%8F%AF)
- [📚 论文](#-%E8%AE%BA%E6%96%87)
- [✨ 贡献者](#-%E8%B4%A1%E7%8C%AE%E8%80%85)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## 🚀 快速开始

### 💻 安装

> AgentScope需要**Python 3.10**或更高版本。

#### 🛠️ 从源码安装

```bash
# 从 GitHub 拉取源码
git clone -b main https://github.com/agentscope-ai/agentscope.git

# 以可编辑模式安装包
cd agentscope
pip install -e .
```

#### 🔄 使用 uv 安装（推荐，安装更快）

[uv](https://github.com/astral-sh/uv) 是一个用 Rust 编写的快速 Python 包安装程序和解析器。

```bash
# 克隆仓库
git clone -b main https://github.com/agentscope-ai/agentscope.git
cd agentscope

# 使用 uv 安装
uv pip install -e .
```

#### 📦 从PyPi安装

```bash
pip install agentscope
```

或者使用 uv：

```bash
uv pip install agentscope
```

## 📝 样例

### 👋 Hello AgentScope！

使用 AgentScope 显式地创建一个名为“Friday”的助手🤖，并与之对话。

```python
from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import DashScopeChatModel
from agentscope.formatter import DashScopeChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, execute_python_code, execute_shell_command
import os, asyncio


async def main():
    toolkit = Toolkit()
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(execute_shell_command)

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You're a helpful assistant named Friday.",
        model=DashScopeChatModel(
            model_name="qwen-max",
            api_key=os.environ["DASHSCOPE_API_KEY"],
            stream=True,
        ),
        memory=InMemoryMemory(),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    user = UserAgent(name="user")

    msg = None
    while True:
        msg = await agent(msg)
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break

asyncio.run(main())
```

### 🎯 实时介入

在 ``ReActAgent`` 中原生支持**实时介入**功能，提供面向打断的记忆处理机制，将中断转换为智能体的**可观察事件**，让智能体能够无缝恢复对话。

<p align="center">
  <img src="./assets/images/realtime_steering_zh.gif" alt="Realtime Steering" width="49%"/>
  <img src="./assets/images/realtime_steering_en.gif" alt="Realtime Steering" width="49%"/>
</p>

### 🛠️ 细粒度 MCP 控制

开发者能够以**本地可调用函数**的方式获得 MCP 工具，并以任意方式使用（例如直接调用、装备给智能体，或将其包装为更复杂的工具等）

```python
from agentscope.mcp import HttpStatelessClient
from agentscope.tool import Toolkit
import os

async def fine_grained_mcp_control():
    # 以高德MCP为例，初始化MCP客户端
    client = HttpStatelessClient(
        name="gaode_mcp",
        transport="streamable_http",
        url=f"https://mcp.amap.com/mcp?key={os.environ['GAODE_API_KEY']}",
    )

    # 将MCP工具获取为**本地可调用函数**，并在任何地方使用
    func = await client.get_callable_function(func_name="maps_geo")

    # 选项1：直接调用
    await func(address="天安门广场", city="北京")

    # 选项2：作为工具传递给智能体
    toolkit = Toolkit()
    toolkit.register_tool_function(func)
    # ...

    # 选项3：包装为更复杂的工具
    # ...
```

### 🧑‍🤝‍🧑 多智能体对话

AgentScope 提供 ``MsgHub`` 和多种 pipeline 来简化多智能体对话的构建，提供高效的消息路由和无缝信息共享

```python
from agentscope.pipeline import MsgHub, sequential_pipeline
from agentscope.message import Msg
import asyncio

async def multi_agent_conversation():
    # 创建智能体
    agent1 = ...
    agent2 = ...
    agent3 = ...
    agent4 = ...

    # 创建消息中心来管理多智能体对话
    async with MsgHub(
        participants=[agent1, agent2, agent3],
        announcement=Msg("Host", "请介绍一下自己。", "assistant")
    ) as hub:
        # 按顺序发言
        await sequential_pipeline([agent1, agent2, agent3])
        # 动态管理参与者
        hub.add(agent4)
        hub.delete(agent3)
        await hub.broadcast(Msg("Host", "再见！", "assistant"))

asyncio.run(multi_agent_conversation())
```


### 💻 AgentScope Studio

使用以下命令安装并启动 AgentScope Studio，以追踪和可视化基于 AgentScope 构建的智能体应用。

```bash
npm install -g @agentscope/studio

as_studio
```

<p align="center">
    <img
        src="./assets/images/home.gif"
        width="49%"
        alt="home"
    />
    <img
        src="./assets/images/projects.gif"
        width="49%"
        alt="projects"
    />
    <img
        src="./assets/images/runtime.gif"
        width="49%"
        alt="runtime"
    />
    <img
        src="./assets/images/friday.gif"
        width="49%"
        alt="friday"
    />
</p>


## 📖 文档

- 教程
  - [安装](https://doc.agentscope.io/zh_CN/tutorial/quickstart_installation.html)
  - [核心概念](https://doc.agentscope.io/zh_CN/tutorial/quickstart_key_concept.html)
  - [创建消息](https://doc.agentscope.io/zh_CN/tutorial/quickstart_message.html)
  - [ReAct Agent](https://doc.agentscope.io/zh_CN/tutorial/quickstart_agent.html)
- 工作流
  - [对话（Conversation）](https://doc.agentscope.io/zh_CN/tutorial/workflow_conversation.html)
  - [多智能体辩论（Multi-Agent Debate）](https://doc.agentscope.io/zh_CN/tutorial/workflow_multiagent_debate.html)
  - [智能体并发（Concurrent Agents）](https://doc.agentscope.io/zh_CN/tutorial/workflow_concurrent_agents.html)
  - [路由（Routing）](https://doc.agentscope.io/zh_CN/tutorial/workflow_routing.html)
  - [交接（Handoffs）](https://doc.agentscope.io/zh_CN/tutorial/workflow_handoffs.html)
- 常见问题
  - [FAQ](https://doc.agentscope.io/zh_CN/tutorial/faq.html)
- 任务指南
  - [模型](https://doc.agentscope.io/zh_CN/tutorial/task_model.html)
  - [提示格式化器](https://doc.agentscope.io/zh_CN/tutorial/task_prompt.html)
  - [工具](https://doc.agentscope.io/zh_CN/tutorial/task_tool.html)
  - [记忆](https://doc.agentscope.io/zh_CN/tutorial/task_memory.html)
  - [长期记忆](https://doc.agentscope.io/zh_CN/tutorial/task_long_term_memory.html)
  - [智能体](https://doc.agentscope.io/zh_CN/tutorial/task_agent.html)
  - [管道（Pipeline）](https://doc.agentscope.io/zh_CN/tutorial/task_pipeline.html)
  - [计划](https://doc.agentscope.io/zh_CN/tutorial/task_plan.html)
  - [状态/会话管理](https://doc.agentscope.io/zh_CN/tutorial/task_state.html)
  - [智能体钩子函数](https://doc.agentscope.io/zh_CN/tutorial/task_hook.html)
  - [MCP](https://doc.agentscope.io/zh_CN/tutorial/task_mcp.html)
  - [AgentScope Studio](https://doc.agentscope.io/zh_CN/tutorial/task_studio.html)
  - [追踪](https://doc.agentscope.io/zh_CN/tutorial/task_tracing.html)
  - [智能体评测](https://doc.agentscope.io/zh_CN/tutorial/task_eval.html)
  - [嵌入（Embedding）](https://doc.agentscope.io/zh_CN/tutorial/task_embedding.html)
  - [Token计数](https://doc.agentscope.io/zh_CN/tutorial/task_token.html)
- API
  - [API文档](https://doc.agentscope.io/zh_CN/api/agentscope.html)
- [示例](https://github.com/agentscope-ai/agentscope/tree/main/examples)
  - 功能演示
    - [MCP](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/mcp)
    - [计划](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/plan)
    - [结构化输出](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/structured_output)
    - [RAG](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/rag)
    - [长期记忆](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/long_term_memory)
    - [基于DB的会话管理](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/session_with_sqlite)
    - [流式获取打印消息](https://github.com/agentscope-ai/agentscope/tree/main/examples/functionality/stream_printing_messages)
  - 智能体
    - [ReAct 智能体](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/react_agent)
    - [语音智能体](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/voice_agent)
    - [Deep Research 智能体](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/deep_research_agent)
    - [Browser-use 智能体](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/browser_agent)
    - [Meta Planner 智能体](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent/meta_planner_agent)
  - 游戏
    - [九人制狼人杀](https://github.com/agentscope-ai/agentscope/tree/main/examples/game/werewolves)
  - 工作流
    - [多智能体辩论](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_debate)
    - [多智能体对话](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_conversation)
    - [多智能体并发](https://github.com/agentscope-ai/agentscope/tree/main/examples/workflows/multiagent_concurrent)
  - 评测
    - [ACEBench](https://github.com/agentscope-ai/agentscope/tree/main/examples/evaluation/ace_bench)
  - 训练
    - [使用 Trinity-RFT 进行强化学习训练](https://github.com/agentscope-ai/agentscope/tree/main/examples/training/react_agent)


## 🤝 贡献

我们欢迎并鼓励社区成员为 AgentScope 做出贡献！请参阅我们的 [贡献指南](./CONTRIBUTING.md) 了解更多详情。

## ⚖️ 许可

AgentScope 基于 Apache License 2.0 发布。

## 📚 论文

如果我们的工作对您的研究或应用有帮助，请引用我们的论文。

- [AgentScope 1.0: A Developer-Centric Framework for Building Agentic Applications](https://arxiv.org/abs/2508.16279)

- [AgentScope: A Flexible yet Robust Multi-Agent Platform](https://arxiv.org/abs/2402.14034)

```
@article{agentscope_v1,
    author  = {
        Dawei Gao,
        Zitao Li,
        Yuexiang Xie,
        Weirui Kuang,
        Liuyi Yao,
        Bingchen Qian,
        Zhijian Ma,
        Yue Cui,
        Haohao Luo,
        Shen Li,
        Lu Yi,
        Yi Yu,
        Shiqi He,
        Zhiling Luo,
        Wenmeng Zhou,
        Zhicheng Zhang,
        Xuguang He,
        Ziqian Chen,
        Weikai Liao,
        Farruh Isakulovich Kushnazarov,
        Yaliang Li,
        Bolin Ding,
        Jingren Zhou}
    title   = {AgentScope 1.0: A Developer-Centric Framework for Building Agentic Applications},
    journal = {CoRR},
    volume  = {abs/2508.16279},
    year    = {2025},
}

@article{agentscope,
    author  = {
        Dawei Gao,
        Zitao Li,
        Xuchen Pan,
        Weirui Kuang,
        Zhijian Ma,
        Bingchen Qian,
        Fei Wei,
        Wenhao Zhang,
        Yuexiang Xie,
        Daoyuan Chen,
        Liuyi Yao,
        Hongyi Peng,
        Zeyu Zhang,
        Lin Zhu,
        Chen Cheng,
        Hongzhu Shi,
        Yaliang Li,
        Bolin Ding,
        Jingren Zhou}
    title   = {AgentScope: A Flexible yet Robust Multi-Agent Platform},
    journal = {CoRR},
    volume  = {abs/2402.14034},
    year    = {2024},
}
```

## ✨ 贡献者

感谢所有贡献者：

<a href="https://github.com/agentscope-ai/agentscope/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=agentscope-ai/agentscope&max=999&columns=12&anon=1" />
</a>
