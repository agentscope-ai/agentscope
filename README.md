[**中文主页**](https://github.com/modelscope/agentscope/blob/main/README_ZH.md) | [**日本語のホームページ**](https://github.com/modelscope/agentscope/blob/main/README_JA.md) | [**Tutorial**](https://doc.agentscope.io/) | [**Roadmap**](https://github.com/modelscope/agentscope/blob/main/docs/ROADMAP.md)

<p align="center">
    <img align="center" src="https://img.alicdn.com/imgextra/i3/O1CN01ywJShe1PU90G8ZYtM_!!6000000001843-55-tps-743-743.svg" width="110" height="110" style="margin: 30px">
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

<!-- START doctoc -->
<!-- END doctoc -->


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
from agentscope.agents import DialogAgent, UserAgent
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
while True:
    x = dialog_agent(x)
    x = user_agent(x)
    if x.content == "exit":
        break
```

### 🧑‍🤝‍🧑 Multi-agent Conversation

AgentScope is born for **multi-agent** applications.

![](https://img.shields.io/badge/✨_Feature-Transparent-green)
![](https://img.shields.io/badge/✨_Feature-Multi--Agent-purple)

```python
from agentscope.agents import DialogAgent
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

# Create three agents
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

# Create a chatroom by msghub, where agents' messages are broadcast to all participants
with msghub(
    participants=[friday, saturday, sunday],
    announcement=Msg("user", "Hi, let's talk about the weekend!", "user"),  # A greeting message
) as hub:
    # Speak in sequence
    sequential_pipeline([friday, saturday, sunday], x=None)
```

### 💡 Reasoning Agent with Tools

![](https://img.shields.io/badge/✨_Feature-Transparent-green)

Creating a reasoning agent with built-in tools and **MCP servers**!

```python
from agentscope.agents import ReActAgentV2, UserAgent
from agentscope.service import ServiceToolkit, execute_python_code
import agentscope

agentscope.init(
    model_configs={
        "model_config": "my_config",
        "model_type": "dashscope_chat",
        "model_name": "qwen-max",
    }
)

# Add tools
toolkit = ServiceToolkit()
toolkit.add(execute_python_code)

# Connect to MCP server
toolkit.add_mcp_servers(
    {
        "mcpServers": {
            "puppeteer": {
                "url": "http://127.0.0.1:8000/sse",
            },
        },
    }
)

# Create a reasoning-acting agent
agent = ReActAgentV2(
    name="Friday",
    model_config_name="my_config",
    service_toolkit=toolkit,
    max_iters=20
)
user_agent = UserAgent(name="user")

# Build the workflow/conversation explicitly
x = None
while True:
    x = agent(x)
    x = user_agent(x)
    if x.content == "exit":
        break
```

### 🔠 Structured Output

![](https://img.shields.io/badge/✨_Feature-Easy--to--use-yellow)

Specifying structured output easily!

```python
from agentscope.agents import ReActAgentV2
from agentscope.service import ServiceToolkit
from agentscope.message import Msg
from pydantic import BaseModel, Field
from typing import Literal
import agentscope

agentscope.init(
    model_configs={
        "model_config": "my_config",
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
Build your own workflow with AgentScope easily!

```python
from agentscope.agents import ReActAgentV2
from agentscope.service import ServiceToolkit
from agentscope.message import Msg
from pydantic import BaseModel, Field
from typing import Literal, Union
import agentscope

agentscope.init(
    model_configs={
        "model_config": "my_config",
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
    ] = Field(description="Choice the right follow-up task, and choice `None` if the task is too simple or no suitable task")
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

Using a magic function `to_dist` to run the agent in distributed mode!

```python
from agentscope.agents import DialogAgent
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
agent1(Msg("user", "", "user"))
agent2(Msg("user", "", "user"))
```

### 👀 Visualization

![](https://img.shields.io/badge/✨_Feature-Visualization-8A2BE2)
![](https://img.shields.io/badge/✨_Feature-Customization-6495ED)

AgentScope supports **Gradio** and **AgentScope Studio** for visualization. Third-party visualization tools are also supported.

<p align="center">
    <img
        src="https://img.alicdn.com/imgextra/i4/O1CN01eCEYvA1ueuOkien7T_!!6000000006063-1-tps-960-600.gif"
        alt="AgentScope Studio"
        width="45%"
    />
    <img
        src="https://img.alicdn.com/imgextra/i4/O1CN01eCEYvA1ueuOkien7T_!!6000000006063-1-tps-960-600.gif"
        alt="AgentScope Studio"
        width="45%"
    />
</p>

Connect to **Third-party visualization** is also supported!

```python
from agentscope.agents import AgentBase
from agentscope.message import Msg
import requests


def forward_message_hook(self, msg: Msg, stream: bool, last: bool) -> None:
    """Forward the displayed message to third-party visualization tools."""
    # Taking RESTFul API as an example
    requests.post(
        "https://xxx.com",
        json={
            "msg": msg.to_dict(),
            "stream": stream,
            "last": last
        }
    )

# Register as a class-level hook, that all instances will use this hook
AgentBase.register_class_hook(
    hook_type='pre_speak',
    hook_name='forward_to_third_party',
    hook=forward_message_hook
)
```


## License

AgentScope is released under Apache License 2.0.

## Publications

If you find our work helpful for your research or application, please cite our papers.

1. [AgentScope: A Flexible yet Robust Multi-Agent Platform](https://arxiv.org/abs/2402.14034)

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

## Contributors ✨

All thanks to our contributors:

<a href="https://github.com/modelscope/agentscope/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=modelscope/agentscope&max=999&columns=12&anon=1" />
</a>