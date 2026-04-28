# Groq Agent Example

This example demonstrates how to use **Groq** as the LLM backend in AgentScope.
Groq provides low-latency inference for popular open-weight models (Llama, Mixtral,
Gemma, etc.) via an OpenAI-compatible API.

The example covers four capabilities:

| Demo | What it shows |
|------|---------------|
| 1 — Basic chat | Single-turn request/response, token usage |
| 2 — Streaming | Token-by-token output via `stream=True` |
| 3 — ReAct agent | Interactive chatbot with tool use (shell, Python, file viewer) |
| 4 — Structured output | Forcing the model to return a typed JSON object |

## Prerequisites

**Install the Groq SDK extra:**

```bash
pip install "agentscope[groq]"
# or just the SDK directly
pip install groq
```

**Get a free Groq API key** at <https://console.groq.com> and export it:

```bash
export GROQ_API_KEY="your-key-here"
```

## Quick Start

```bash
python main.py
```

The first three demos run automatically. Demo 3 (ReAct agent) is interactive —
type any message and press Enter. Type `exit` to quit.

## Swapping Models

Change `model_name` in `main.py` to any model available on Groq, for example:

```python
GroqChatModel(model_name="mixtral-8x7b-32768", ...)
GroqChatModel(model_name="llama-3.1-8b-instant", ...)
GroqChatModel(model_name="gemma2-9b-it", ...)
```

For the full list, see the [Groq models page](https://console.groq.com/docs/models).

## Using Groq in Your Own Agent

```python
import os
from agentscope.agent import ReActAgent
from agentscope.formatter import GroqChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.model import GroqChatModel
from agentscope.tool import Toolkit

agent = ReActAgent(
    name="MyAgent",
    sys_prompt="You are a helpful assistant.",
    model=GroqChatModel(
        model_name="llama-3.3-70b-versatile",
        api_key=os.environ.get("GROQ_API_KEY"),
        stream=True,
    ),
    formatter=GroqChatFormatter(),
    toolkit=Toolkit(),
    memory=InMemoryMemory(),
)
```

> **Note:** Always pair `GroqChatModel` with `GroqChatFormatter` (or
> `GroqMultiAgentFormatter` for multi-agent scenarios). The formatter converts
> AgentScope's internal `Msg` objects to the Groq/OpenAI-compatible message
> format.
