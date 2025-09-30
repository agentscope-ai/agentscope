[**中文主页**](https://github.com/modelscope/agentscope/blob/main/README_ZH.md) | [**日本語のホームページ**](https://github.com/modelscope/agentscope/blob/main/README_JA.md) | [**Tutorial**](https://doc.agentscope.io/) | [**Roadmap**](https://github.com/modelscope/agentscope/blob/main/docs/ROADMAP.md) | [**FAQ**](https://doc.agentscope.io/tutorial/faq.html)

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
            src="https://img.shields.io/badge/python-3.9+-blue?logo=python"
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
            src="https://img.shields.io/badge/Drag_and_drop_UI-WorkStation-blue?logo=html5&logoColor=green&color=dark-green"
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

## ✨ Why AgentScope?

Easy for beginners, powerful for experts.

- **Transparent to Developers**: Transparent is our **FIRST principle**. Prompt engineering, API invocation, agent building, workflow orchestration, all are visible and controllable for developers. No deep encapsulation or implicit magic.
- **Model Agnostic**: Programming once, run with all models. More than **17+** LLM API providers are supported.
- **LEGO-style Agent Building**: All components are **modular** and **independent**. Use them or not, your choice.
- **Multi-Agent Oriented**: Designed for **multi-agent**, **explicit** message passing and workflow orchestration, NO deep encapsulation.
- **Native Distribution/Parallelization**: Centralized programming for distributed application, and **automatic parallelization**.
- **Highly Customizable**: Tools, prompt, agent, workflow, third-party libs & visualization, customization is encouraged everywhere.
- **Developer-friendly**: Low-code development, visual tracing & monitoring. From developing to deployment, all in one place.

## 📢 News
- **[2025-07-01]** A new version AgentScope is under development. In this new version, AgentScope will be more powerful and flexible, with a new architecture and more features. Refer to our [Roadmap](https://github.com/modelscope/agentscope/blob/main/docs/ROADMAP.md) for more details!
- **[2025-04-27]** A new 💻 AgentScope Studio is online now. Refer [here](https://doc.agentscope.io/build_tutorial/visual.html) for more details.
- **[2025-03-21]** AgentScope supports hooks functions now. Refer to our [tutorial](https://doc.agentscope.io/build_tutorial/hook.html) for more details.
- **[2025-03-19]** AgentScope supports 🔧 tools API now. Refer to our [tutorial](https://doc.agentscope.io/build_tutorial/tool.html).
- **[2025-03-20]** Agentscope now supports [MCP Server](https://github.com/modelcontextprotocol/servers)! You can learn how to use it by following this [tutorial](https://doc.agentscope.io/build_tutorial/MCP.html).
- **[2025-03-05]** Our [🎓 AgentScope Copilot](applications/multisource_rag_app/README.md), a multi-source RAG application is open-source now!
- **[2025-02-24]** [🇨🇳 Chinese version tutorial](https://doc.agentscope.io/zh_CN) is online now!
- **[2025-02-13]** We have released the [📁 technical report](https://doc.agentscope.io/tutorial/swe.html) of our solution in [SWE-Bench(Verified)](https://www.swebench.com/)!
- **[2025-02-07]** 🎉🎉 AgentScope has achieved a **63.4% resolve rate** in [SWE-Bench(Verified)](https://www.swebench.com/).
- **[2025-01-04]** AgentScope supports Anthropic API now.

👉👉 [**Older News**](https://github.com/modelscope/agentscope/blob/main/docs/news_en.md)

## 💬 Contact

Welcome to join our community on

| [Discord](https://discord.gg/eYMpfnkG8h)                                                                                         | DingTalk                                                                                                                          |
|----------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------|
| <img src="https://gw.alicdn.com/imgextra/i1/O1CN01hhD1mu1Dd3BWVUvxN_!!6000000000238-2-tps-400-400.png" width="100" height="100"> | <img src="https://img.alicdn.com/imgextra/i1/O1CN01LxzZha1thpIN2cc2E_!!6000000005934-2-tps-497-477.png" width="100" height="100"> |


<!-- START doctoc generated TOC please keep comment here to allow auto update -->
<!-- DON'T EDIT THIS SECTION, INSTEAD RE-RUN doctoc TO UPDATE -->
## 📑 Table of Contents

- [🚀 Quickstart](#-quickstart)
  - [💻 Installation](#-installation)
    - [🛠️ From source](#-from-source)
    - [📦 From PyPi](#-from-pypi)
- [📝 Example](#-example)
  - [👋 Hello AgentScope](#-hello-agentscope)
  - [🧑‍🤝‍🧑 Multi-Agent Conversation](#-multi-agent-conversation)
  - [💡 Reasoning with Tools & MCP](#-reasoning-with-tools--mcp)
  - [🔠 Structured Output](#-structured-output)
  - [✏️ Workflow Orchestration](#-workflow-orchestration)
  - [⚡️ Distribution and Parallelization](#%EF%B8%8F-distribution-and-parallelization)
  - [👀 Tracing & Monitoring](#-tracing--monitoring)
- [⚖️ License](#-license)
- [📚 Publications](#-publications)
- [✨ Contributors](#-contributors)

<!-- END doctoc generated TOC please keep comment here to allow auto update -->

## 🚀 Quickstart

### 💻 Installation

> AgentScope requires **Python 3.9** or higher.

#### 🛠️ From source

```bash
# Pull the source code from GitHub
git clone https://github.com/modelscope/agentscope.git

# Install the package in editable mode
cd agentscope
pip install -e .
```

#### 📦 From PyPi

```bash
pip install agentscope
```

## 📝 Example

### 👋 Hello AgentScope

![](https://img.shields.io/badge/✨_Feature-Transparent-green)
![](https://img.shields.io/badge/✨_Feature-Model--Agnostic-b)

Creating a basic conversation **explicitly** between **a user** and **an assistant** with AgentScope:

```python
from agentscope.agent import DialogAgent, UserAgent
import agentscope

# Load model configs
agentscope.init(
  model_configs=[
    {
      "config_name": "my_config",
      "model_type": "dashscope_chat",
      "model_name": "qwen-max",
    }
  ]
)

# Create a dialog agent and a user agent
dialog_agent = DialogAgent(
  name="Friday",
  model_config_name="my_config",
  sys_prompt="You're a helpful assistant named Friday"
)
user_agent = UserAgent(name="user")

# Build the workflow/conversation explicitly
x = None
while x is None or x.content != "exit":
  x = dialog_agent(x)
  x = user_agent(x)
```

### 🧑‍🤝‍🧑 Multi-Agent Conversation

AgentScope is designed for **multi-agent** applications, offering flexible control over information flow and communication between agents.

![](https://img.shields.io/badge/✨_Feature-Transparent-green)
![](https://img.shields.io/badge/✨_Feature-Multi--Agent-purple)

```python
from agentscope.agent import DialogAgent
from agentscope.message import Msg
from agentscope.pipelines import sequential_pipeline
from agentscope import msghub
import agentscope

# Load model configs
agentscope.init(
  model_configs=[
    {
      "config_name": "my_config",
      "model_type": "dashscope_chat",
      "model_name": "qwen-max",
    }
  ]
)

# Create three agent
friday = DialogAgent(
  name="Friday",
  model_config_name="my_config",
  sys_prompt="You're a helpful assistant named Friday"
)

saturday = DialogAgent(
  name="Saturday",
  model_config_name="my_config",
  sys_prompt="You're a helpful assistant named Saturday"
)

sunday = DialogAgent(
  name="Sunday",
  model_config_name="my_config",
  sys_prompt="You're a helpful assistant named Sunday"
)

# Create a chatroom by msghub, where agent' messages are broadcast to all participants
with msghub(
        participants=[friday, saturday, sunday],
        announcement=Msg("user", "Counting from 1 and report one number each time without other things", "user"),
        # A greeting message
) as hub:
  # Speak in sequence
  sequential_pipeline([friday, saturday, sunday], x=None)
```

### 💡 Reasoning with Tools & MCP

![](https://img.shields.io/badge/✨_Feature-Transparent-green)

Creating a reasoning agent with built-in tools and **MCP servers**!

```python
from agentscope.agent import ReActAgentV2, UserAgent
from agentscope.service import ServiceToolkit, execute_python_code
import agentscope

agentscope.init(
  model_configs={
    "config_name": "my_config",
    "model_type": "dashscope_chat",
    "model_name": "qwen-max",
  }
)

# Add tools
toolkit = ServiceToolkit()
toolkit.add(execute_python_code)

# Connect to Gaode MCP server
toolkit.add_mcp_servers(
  {
    "mcpServers": {
      "amap-amap-sse": {
        "url": "https://mcp.amap.com/sse?key={YOUR_GAODE_API_KEY}"
      }
    }
  }
)

# Create a reasoning-acting agent
agent = ReActAgentV2(
  name="Friday",
  model_config_name="my_config",
  service_toolkit=toolkit,
  sys_prompt="You're a helpful assistant named Friday."
)
user_agent = UserAgent(name="user")

# Build the workflow/conversation explicitly
x = None
while x is None or x.content != "exit":
  x = agent(x)
  x = user_agent(x)
```

### 🔠 Structured Output

![](https://img.shields.io/badge/✨_Feature-Easy--to--use-yellow)

Specifying structured output with a Pydantic base model.

```python
from agentscope.agent import ReActAgentV2
from agentscope.service import ServiceToolkit
from agentscope.message import Msg
from pydantic import BaseModel, Field
from typing import Literal
import agentscope

agentscope.init(
  model_configs={
    "config_name": "my_config",
    "model_type": "dashscope_chat",
    "model_name": "qwen-max",
  }
)

# Create a reasoning-acting agent
agent = ReActAgentV2(
  name="Friday",
  model_config_name="my_config",
  service_toolkit=ServiceToolkit(),
  max_iters=20
)


class CvModel(BaseModel):
  name: str = Field(max_length=50, description="The name")
  description: str = Field(max_length=200, description="The brief description")
  aget: int = Field(gt=0, le=120, description="The age of the person")


class ChoiceModel(BaseModel):
  choice: Literal["apple", "banana"]


# Specify structured output using `structured_model`
res_msg = agent(
  Msg("user", "Introduce Einstein", "user"),
  structured_model=CvModel
)
print(res_msg.metadata)

# Switch to different structured model
res_msg = agent(
  Msg("user", "Choice a fruit", "user"),
  structured_model=ChoiceModel
)
print(res_msg.metadata)
```

### ✏️ Workflow Orchestration

![](https://img.shields.io/badge/✨_Feature-Transparent-green)

[Routing](https://www.anthropic.com/engineering/building-effective-agents), [parallelization](https://www.anthropic.com/engineering/building-effective-agents), [orchestrator-workers](https://www.anthropic.com/engineering/building-effective-agents), or [evaluator-optimizer](https://www.anthropic.com/engineering/building-effective-agents).
Build your own workflow with AgentScope easily! Taking routing as an example:

```python
from agentscope.agent import ReActAgentV2
from agentscope.service import ServiceToolkit
from agentscope.message import Msg
from pydantic import BaseModel, Field
from typing import Literal, Union
import agentscope

agentscope.init(
  model_configs={
    "config_name": "my_config",
    "model_type": "dashscope_chat",
    "model_name": "qwen-max",
  }
)

# Workflow: Routing
routing_agent = ReActAgentV2(
  name="Routing",
  model_config_name="my_config",
  sys_prompt="You're a routing agent. Your target is to route the user query to the right follow-up task",
  service_toolkit=ServiceToolkit()
)


# Use structured output to specify the routing task
class RoutingChoice(BaseModel):
  your_choice: Literal[
    'Content Generation',
    'Programming',
    'Information Retrieval',
    None
  ] = Field(
    description="Choice the right follow-up task, and choice `None` if the task is too simple or no suitable task")
  task_description: Union[str, None] = Field(description="The task description", default=None)


res_msg = routing_agent(
  Msg("user", "Help me to write a poem", "user"),
  structured_model=RoutingChoice
)

# Execute the follow-up task
if res_msg.metadata["your_choice"] == "Content Generation":
  ...
elif res_msg.metadata["your_choice"] == "Programming":
  ...
elif res_msg.metadata["your_choice"] == "Information Retrieval":
  ...
else:
  ...
```

### ⚡️ Distribution and Parallelization

![](https://img.shields.io/badge/✨_Feature-Transparent-green)
![](https://img.shields.io/badge/✨_Feature-Distribution-darkblue)
![](https://img.shields.io/badge/✨_Feature-Efficiency-green)

Using `to_dist` function to run the agent in distributed mode!

```python
from agentscope.agent import DialogAgent
from agentscope.message import Msg
import agentscope

# Load model configs
agentscope.init(
  model_configs=[
    {
      "config_name": "my_config",
      "model_type": "dashscope_chat",
      "model_name": "qwen-max",
    }
  ]
)

# Using `to_dist()` to run the agent in distributed mode
agent1 = DialogAgent(
  name="Saturday",
  model_config_name="my_config"
).to_dist()

agent2 = DialogAgent(
  name="Sunday",
  model_config_name="my_config"
).to_dist()

# The two agent will run in parallel
agent1(Msg("user", "Execute task1 ...", "user"))
agent2(Msg("user", "Execute task2 ...", "user"))
```

### 👀 Tracing & Monitoring

![](https://img.shields.io/badge/✨_Feature-Visualization-8A2BE2)
![](https://img.shields.io/badge/✨_Feature-Customization-6495ED)

AgentScope provides a local visualization and monitoring tool, **AgentScope Studio**.

```bash
# Install AgentScope Studio
npm install -g @agentscope/studio
# Run AgentScope Studio
as_studio
```

```python
import agentscope

# Connect application to AgentScope Studio
agentscope.init(
  model_configs = {
    "config_name": "my_config",
    "model_type": "dashscope_chat",
    "model_name": "qwen_max",
  },
  studio_url="http://localhost:3000", # The URL of AgentScope Studio
)

# ...
```

<div align="center">
       <img
        src="https://img.alicdn.com/imgextra/i4/O1CN01eCEYvA1ueuOkien7T_!!6000000006063-1-tps-960-600.gif"
        alt="AgentScope Studio"
        width="100%"
    />
   <div align="center">AgentScope Studio, a local visualization tool</div>
</div>


## ⚖️ License

AgentScope is released under Apache License 2.0.

## 📚 Publications

If you find our work helpful for your research or application, please cite our papers.

[AgentScope: A Flexible yet Robust Multi-Agent Platform](https://arxiv.org/abs/2402.14034)

```
@article{agentscope,
    author  = {Dawei Gao and
               Zitao Li and
               Xuchen Pan and
               Weirui Kuang and
               Zhijian Ma and
               Bingchen Qian and
               Fei Wei and
               Wenhao Zhang and
               Yuexiang Xie and
               Daoyuan Chen and
               Liuyi Yao and
               Hongyi Peng and
               Ze Yu Zhang and
               Lin Zhu and
               Chen Cheng and
               Hongzhu Shi and
               Yaliang Li and
               Bolin Ding and
               Jingren Zhou}
    title   = {AgentScope: A Flexible yet Robust Multi-Agent Platform},
    journal = {CoRR},
    volume  = {abs/2402.14034},
    year    = {2024},
}
```

## ✨ Contributors

All thanks to our contributors:

<a href="https://github.com/modelscope/agentscope/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=modelscope/agentscope&max=999&columns=12&anon=1" />
</a>