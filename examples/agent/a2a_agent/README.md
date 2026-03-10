# Agent-to-Agent Protocol Example

The `A2AAgent` in AgentScope is an A2A client that connects to an external agent server via the Agent-to-Agent (A2A) protocol.
This example demonstrates how to set up and use the `A2AAgent` to interact with an agent hosted on an A2A server.

Note the A2A feature is experimental and subject to change, and due to the limitations of A2A protocol, the `A2AAgent`
currently

1. only supports chatbot scenarios, where only a user and an agent are involved
2. does not support realtime steering/interruption during the conversation
3. does not support agentic structured outputs
4. stores the observed messages locally and send them together with the input message(s) of the `reply` function

## Files

The example contains the following files:

```
examples/agent/a2a_agent
├── main.py                  # The main script to run the A2A agent example
├── setup_a2a_server.py      # The script to set up a simple A2A server
├── agent_card.py            # The agent card definition for the A2A agent
└── README.md                # This README file
```

## Setup

This example provides a simple setup to demonstrate how to use the `A2AAgent` in AgentScope.
First you need to install the required dependencies:

```bash
uv pip install a2a-sdk[http-server] agentscope[a2a]
#  or
pip install a2a-sdk[http-server] agentscope[a2a]
```

Then we first set up a simple A2A server that hosts a ReAct agent:
```bash
uvicorn setup_a2a_server:app --host 0.0.0.0 --port 8000
```
This will start an A2A server locally on port 8000.

After that, you can run the A2A agent example to run a chatbot conversation with the agent hosted on the A2A server:
```bash
python main.py
```

