<p align="center">
  <img
    src="https://img.alicdn.com/imgextra/i1/O1CN01nTg6w21NqT5qFKH1u_!!6000000001621-55-tps-550-550.svg"
    alt="AgentScope Logo"
    width="200"
  />
</p>

<span align="center">

[**中文主页**](https://github.com/agentscope-ai/agentscope/blob/main/README_zh.md) | [**Documentation**](https://docs.agentscope.io/) | [**Roadmap**](https://github.com/orgs/agentscope-ai/projects/2/views/1)

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
            src="https://img.shields.io/badge/Discord-Join%20Us-5865F2?logo=discord&logoColor=white"
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

## What is AgentScope 2.0?

AgentScope 2.0 is a production-ready, easy-to-use agent framework with essential abstractions that work with rising model capability and built-in support for .

- [**Event System** →](https://docs.agentscope.io/v2/building-blocks/message-and-event) A unified event bus to the frontend and human-in-the-loop support.
- [**Permission System** →](https://docs.agentscope.io/v2/building-blocks/permission-system) Fine-grained, configurable control over tools and resources.
- [**Multi-tenancy & Multi-session Service** →](https://docs.agentscope.io/v2/deploy/agent-service) Production-grade serving with isolation across tenants and sessions.
- [**Workspace / Sandbox Support** →](https://docs.agentscope.io/v2/building-blocks/workspace) Run tools and code in isolated environments, with built-in backends for local, Docker, and E2B.
- [**Extensible Middleware System** →](https://docs.agentscope.io/v2/building-blocks/middleware) Composable hooks to customize and extend the agent's reasoning-acting loop.

We design for increasingly agentic LLMs.
Our approach leverages the models' reasoning and tool use abilities
rather than constraining them with strict prompts and opinionated orchestrations.

<img src="assets/images/agentscope.png" alt="agentscope" width="100%"/>

## News
<!-- BEGIN NEWS -->
- **[2026-06] `FEAT`:** Agent Team supported. [Example](https://github.com/agentscope-ai/agentscope/tree/main/examples/agent_service) | [Docs](https://docs.agentscope.io/v2/deploy/agent-team)
- **[2026-05] `RELS`:** AgentScope 2.0 released! [Docs](https://docs.agentscope.io/)
<!-- END NEWS -->

[More news →](./docs/NEWS.md)

## Community

Welcome to join our community on

| [Discord](https://discord.gg/eYMpfnkG8h)                                                                                         | DingTalk                                                                  |
|----------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------|
| <img src="https://gw.alicdn.com/imgextra/i1/O1CN01hhD1mu1Dd3BWVUvxN_!!6000000000238-2-tps-400-400.png" width="100" height="100"> | <img src="./assets/images/dingtalk_qr_code.png" width="100" height="100"> |

## Quickstart

### Installation

> AgentScope requires **Python 3.11** or higher.

#### From PyPI

```bash
uv pip install agentscope
# or
# pip install agentscope
```

#### From source

```bash
# Pull the source code from GitHub
git clone -b main https://github.com/agentscope-ai/agentscope.git

# Install the package in editable mode
cd agentscope

uv pip install -e .
# or
# pip install -e .
```

## Hello AgentScope!

Start your first agent with AgentScope 2.0:

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
        # Handle the event stream, e.g., print the message, update UI, etc.
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

            # Handle other event types

asyncio.run(main())
```

## Hello Agent Service!

An extensible FastAPI based **multi-tenancy**, **multi-session** agent service with pre-built Web UI in `examples/web_ui`

<table>
  <tr>
    <td align="center">
      <img src="assets/images/team.gif" alt="Agent team" width="100%"/>
      <br/>
      <sub><b>Agent team</b> — a leader agent spawns workers and coordinates them through the built-in team tools.</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <img src="assets/images/task.gif" alt="Task planning" width="100%"/>
      <br/>
      <sub><b>Task planning</b> — the agent breaks complex work into a tracked plan and updates it as it goes.</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <img src="assets/images/permission_bypass.gif" alt="Permission control in bypass mode" width="100%"/>
      <br/>
      <sub><b>Permission control in bypass mode</b> — the agent runs end-to-end without pausing for tool-call confirmations.</sub>
    </td>
  </tr>
  <tr>
    <td align="center">
      <img src="assets/images/bg_tool.gif" alt="Background task offloading" width="100%"/>
      <br/>
      <sub><b>Background task offloading</b> — a long-running tool moves to the background; its result later wakes the agent up and the conversation resumes.</sub>
    </td>
  </tr>
</table>

Run the following commands to start the agent service backend and the web UI:

```bash
git clone -b main https://github.com/agentscope-ai/agentscope.git
cd agentscope/examples/agent_service

# start the agent service backend
python main.py
```

Then open another terminal to start the web UI:

```bash
cd agentscope/examples/web_ui

# start the webui
pnpm install
pnpm dev
```


## Contributing

We welcome contributions from the community! Please refer to our [CONTRIBUTING.md](./CONTRIBUTING.md) for guidelines
on how to contribute.

## License

AgentScope is released under Apache License 2.0.

## Publications

If you find our work helpful for your research or application, please cite our papers.

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

## Contributors

All thanks to our contributors:

<a href="https://github.com/agentscope-ai/agentscope/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=agentscope-ai/agentscope&max=999&columns=12&anon=1" />
</a>
