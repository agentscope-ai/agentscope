# OpenAI ReAct Agent (Minimal)

This example runs a minimal ReAct-style agent using OpenAI in AgentScope.

## Prerequisites

- Python 3.10+
- OpenAI API key (`OPENAI_API_KEY`)

## Setup

```bash
# From the repo root
pip install -e .

# Export your OpenAI key
export OPENAI_API_KEY=sk-yourkey
```

## Run

```bash
python examples/openai_react_agent/main.py
```

Type `exit` to end the session.

## Notes

- Default model: `gpt-4o-mini`. Change `model_name` in `main.py` if needed.
- Tools enabled: execute Python code and shell commands. Remove them if you prefer a pure chat agent.
