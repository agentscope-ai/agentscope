<p align="center">
  <img
    src="https://img.alicdn.com/imgextra/i1/O1CN01nTg6w21NqT5qFKH1u_!!6000000001621-55-tps-550-550.svg"
    alt="AgentScope Logo"
    width="200"
  />
</p>

<span align="center">

[**English Homepage**](https://github.com/agentscope-ai/agentscope/blob/main/README.md) | [**教程**](https://docs.agentscope.io/) | [**路线图**](https://github.com/orgs/agentscope-ai/projects/2/views/1)

</span>

<p align="center">
    <a href="https://arxiv.org/abs/2402.14034">
        <img
            src="https://img.shields.io/badge/cs.MA-2402.14034-B31C1C?logo=arxiv&logoColor=B31C1C"
            alt="arxiv"
        />
    </a>
    <a href="https://pypi.org/project/agentscope/">
        <img
            src="https://img.shields.io/badge/python-3.11+-blue?logo=python"
            alt="pypi"
        />
    </a>
    <a href="https://pypi.org/project/agentscope/">
        <img
            src="https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fpypi.org%2Fpypi%2Fagentscope%2Fjson&query=%24.info.version&prefix=v&logo=pypi&label=version"
            alt="pypi"
        />
    </a>
    <a href="https://discord.gg/eYMpfnkG8h">
        <img
            src="https://img.shields.io/discord/1194846673529213039?label=Discord&logo=discord"
            alt="discord"
        />
    </a>
    <a href="https://docs.agentscope.io/">
        <img
            src="https://img.shields.io/badge/Docs-English%7C%E4%B8%AD%E6%96%87-blue?logo=markdown"
            alt="docs"
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
<img src="https://trendshift.io/api/badge/repositories/20310" alt="agentscope-ai%2Fagentscope | Trendshift" style="width: 250px; height: 55px;" width="250" height="55"/>
</p>

## 什么是 AgentScope 2.0？

AgentScope 2.0 是一款生产就绪、易于使用的智能体框架，提供与不断进化的模型能力相匹配的核心抽象，并内置对模型微调的支持。

我们为日益自主的大语言模型而设计。
我们的方法是充分发挥模型的推理与工具调用能力，
而不是用严格的提示词和固化的编排方式来束缚它们。

## 为什么选择 AgentScope？

- **简单**：通过内置的 ReAct 智能体、工具、技能、人机协作干预、记忆、计划、实时语音、评估和模型微调，5 分钟即可开始构建你的智能体
- **可扩展**：丰富的生态系统集成，覆盖工具、记忆和可观测性；内置 MCP 和 A2A 支持；通过消息中心（MsgHub）实现灵活的多智能体编排和工作流
- **生产就绪**：支持本地部署、云端 Serverless 部署或 K8s 集群部署，并内置 OTel 支持

<img src="assets/images/agentscope.png" alt="agentscope" width="100%"/>

## 新闻
<!-- BEGIN NEWS -->
- **[2026-06] `功能`:** 支持 Agent Team。[样例](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent_service) | [文档](https://docs.agentscope.io/zh/v2/deploy/agent-team)
- **[2026-05] `发布`:** AgentScope 2.0 已发布！[文档](https://docs.agentscope.io/)
<!-- END NEWS -->

[更多新闻 →](./docs/NEWS_zh.md)

## 社区

欢迎加入我们的社区

| [Discord](https://discord.gg/eYMpfnkG8h)                                                                                         | 钉钉                                                                        |
|----------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| <img src="https://gw.alicdn.com/imgextra/i1/O1CN01hhD1mu1Dd3BWVUvxN_!!6000000000238-2-tps-400-400.png" width="100" height="100"> | <img src="./assets/images/dingtalk_qr_code.png" width="100" height="100"> |

<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
## 📑 Table of Contents

- [快速开始](#%E5%BF%AB%E9%80%9F%E5%BC%80%E5%A7%8B)
  - [安装](#%E5%AE%89%E8%A3%85)
    - [从 PyPI 安装](#%E4%BB%8E-pypi-%E5%AE%89%E8%A3%85)
    - [从源码安装](#%E4%BB%8E%E6%BA%90%E7%A0%81%E5%AE%89%E8%A3%85)
- [Hello AgentScope！](#hello-agentscope)
- [智能体服务](#%E6%99%BA%E8%83%BD%E4%BD%93%E6%9C%8D%E5%8A%A1)
- [贡献](#%E8%B4%A1%E7%8C%AE)
- [许可](#%E8%AE%B8%E5%8F%AF)
- [论文](#%E8%AE%BA%E6%96%87)
- [贡献者](#%E8%B4%A1%E7%8C%AE%E8%80%85)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## 快速开始

### 安装

> AgentScope 需要 **Python 3.11** 或更高版本。

#### 从 PyPI 安装

```bash
uv pip install agentscope
# 或者
# pip install agentscope
```

#### 从源码安装

```bash
# 从 GitHub 拉取源码
git clone -b main https://github.com/agentscope-ai/agentscope.git

# 以可编辑模式安装
cd agentscope

uv pip install -e .
# 或者
# pip install -e .
```

## Hello AgentScope！

使用 AgentScope 2.0，启动你的第一个智能体：

```python
from agentscope.agent import Agent
from agentscope.tool import Toolkit, Bash, Grep, Glob, Read, Write, Edit
from agentscope.credential import DashScopeCredential
from agentscope.model import DashScopeChatModel
from agentscope.message import UserMsg
from agentscope.event import EventType

import os, asyncio


async def main() -> None:
    agent = Agent(
        name="Friday",
        system_prompt="You're a helpful assistant named Friday.",
        model=DashScopeChatModel(
            credential=DashScopeCredential(
              api_key=os.environ["DASHSCOPE_API_KEY"]
            ),
            model="qwen3.6-plus",
        ),
        toolkit=Toolkit(
            tools=[
                Bash(),
                Grep(),
                Glob(),
                Read(),
                Write(),
                Edit(),
            ]
        ),
    )

    async for evt in agent.reply_stream(UserMsg("Tony", "Hi, Friday!")):
        # 处理事件流，例如打印消息、更新 UI 等
        match evt.type:
            case EventType.REPLY_START:
                ...
            case EventType.MODEL_CALL_START:
                ...
            case EventType.TEXT_BLOCK_START:
                ...
            case EventType.TEXT_BLOCK_DELTA:
                ...
            case EventType.TEXT_BLOCK_END:
                ...

            # 处理其他事件类型

asyncio.run(main())
```

## 智能体服务

一个基于 FastAPI 的可扩展**多租户**、**多会话**智能体服务，并在 `examples/web_ui` 中提供预构建的 Web UI

```bash
git clone https://github.com/agentscope-ai/agentscope

cd agentscope/examples/agent_service

# 启动智能体服务后端
python main.py
```

然后打开另一个终端启动 Web UI：

```bash
cd agentscope/examples/web_ui

# 启动 webui
pnpm install
pnpm dev
```

体验聊天式界面，与你的智能体进行交互。

<img src="https://gw.alicdn.com/imgextra/i1/O1CN01vGGiBw20agWwpzmjy_!!6000000006866-2-tps-2934-1732.png" alt="Permission System" width="100%">


## 贡献

我们欢迎社区的贡献！请参阅我们的 [贡献指南](./CONTRIBUTING_zh.md) 了解如何贡献。

## 许可

AgentScope 基于 Apache License 2.0 发布。

## 论文

如果我们的工作对您的研究或应用有帮助，请引用我们的论文。

- [AgentScope 1.0: A Developer-Centric Framework for Building Agentic Applications](https://arxiv.org/abs/2508.16279)

- [AgentScope: A Flexible yet Robust Multi-Agent Platform](https://arxiv.org/abs/2402.14034)

```
@article{agentscope_v1,
    author  = {Dawei Gao, Zitao Li, Yuexiang Xie, Weirui Kuang, Liuyi Yao, Bingchen Qian, Zhijian Ma, Yue Cui, Haohao Luo, Shen Li, Lu Yi, Yi Yu, Shiqi He, Zhiling Luo, Wenmeng Zhou, Zhicheng Zhang, Xuguang He, Ziqian Chen, Weikai Liao, Farruh Isakulovich Kushnazarov, Yaliang Li, Bolin Ding, Jingren Zhou}
    title   = {AgentScope 1.0: A Developer-Centric Framework for Building Agentic Applications},
    journal = {CoRR},
    volume  = {abs/2508.16279},
    year    = {2025},
}

@article{agentscope,
    author  = {Dawei Gao, Zitao Li, Xuchen Pan, Weirui Kuang, Zhijian Ma, Bingchen Qian, Fei Wei, Wenhao Zhang, Yuexiang Xie, Daoyuan Chen, Liuyi Yao, Hongyi Peng, Zeyu Zhang, Lin Zhu, Chen Cheng, Hongzhu Shi, Yaliang Li, Bolin Ding, Jingren Zhou}
    title   = {AgentScope: A Flexible yet Robust Multi-Agent Platform},
    journal = {CoRR},
    volume  = {abs/2402.14034},
    year    = {2024},
}
```

## 贡献者

感谢所有贡献者：

<a href="https://github.com/agentscope-ai/agentscope/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=agentscope-ai/agentscope&max=999&columns=12&anon=1" />
</a>
