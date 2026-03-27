# Clicks Protocol × AgentScope: DeFi Yield Agent

An AgentScope example demonstrating how AI agents can autonomously manage treasury and earn yield on Base L2 using Clicks Protocol's MCP tools.

## 🌳 Sample Structure

```
.
├── README.md                 # Documentation
├── main.py                   # Entry point: DeFi Yield Agent
├── treasury_agent.py         # Treasury management agent with yield tools
├── requirements.txt          # Dependencies
└── example_output.md         # Expected output
```

## 📖 Overview

This sample shows how to build an AI agent that manages its own DeFi treasury using Clicks Protocol via MCP. The agent can:

1. **Check yield rates** across DeFi protocols (Morpho Blue, Aave V3) on Base
2. **Simulate payment splits** to preview how deposits will be allocated (80% liquid, 20% yield)
3. **Query agent status** including registration, balance, and yield earned
4. **Monitor referral networks** for additional earnings

### The Problem This Solves

AI agents increasingly hold funds (tips, payments, revenue). But those funds sit idle. There's no simple way for an agent to:
- Earn yield without manual DeFi interaction
- Keep most funds liquid for operations
- Auto-route to the best APY
- Do it all through tool calls (MCP)

Clicks Protocol solves this by providing 4 read-only MCP tools that any agent framework can consume. No wallet management needed for querying. Just point your agent at the MCP server.

### Why AgentScope + Clicks Protocol

AgentScope's MCP integration is the cleanest in the ecosystem. Three lines to connect:

```python
client = HttpStatelessClient(
    name="clicks",
    transport="streamable_http",
    url="https://clicks-mcp.rechnung-613.workers.dev/mcp",
)
await toolkit.register_mcp_client(client)
```

The agent then discovers all Clicks tools automatically and can reason about yield strategies using ReAct.

## 🚀 Getting Started

### Prerequisites

- Python 3.10+
- An API key for your LLM provider (OpenAI, Anthropic, DashScope, etc.)

### Installation

```bash
pip install -r requirements.txt
```

### Usage

```bash
# With OpenAI
OPENAI_API_KEY=sk-xxx python main.py --model openai

# With DashScope (Qwen)
DASHSCOPE_API_KEY=xxx python main.py --model dashscope

# With Anthropic (Claude)
ANTHROPIC_API_KEY=xxx python main.py --model anthropic
```

### Example Queries

The agent can answer questions like:

- "What's the current yield rate on Morpho Blue?"
- "If I deposit 1000 USDC, how would it be split?"
- "What's the status of agent 0x1234...?"
- "How much yield has been earned across the protocol?"
- "Compare Morpho and Aave yields right now"

## 🛠️ Features

- **Zero configuration**: No wallet or API keys needed (read-only MCP tools)
- **Multi-model support**: Works with OpenAI, Anthropic, DashScope, Gemini, Ollama
- **ReAct reasoning**: Agent reasons about DeFi data step by step
- **Structured output**: Get typed results from yield queries
- **Real on-chain data**: Queries live Base mainnet contracts (not mocks)

## Architecture

```
┌─────────────────┐     MCP (Streamable HTTP)     ┌─────────────────────┐
│  AgentScope      │ ──────────────────────────── │  Clicks Protocol     │
│  ReAct Agent     │     Tool Discovery + Calls    │  MCP Server          │
│                  │                               │  (Cloudflare Worker) │
│  • Plan          │     clicks_get_yield_info     │                      │
│  • Reason        │ ◄──────────────────────────── │  Base Mainnet        │
│  • Act           │     clicks_simulate_split     │  • Morpho Blue       │
│  • Observe       │ ◄──────────────────────────── │  • Aave V3           │
└─────────────────┘                               └─────────────────────┘
```

## Available MCP Tools

| Tool | Description | Use Case |
|------|-------------|----------|
| `clicks_get_yield_info` | Current APY rates (Morpho/Aave) | Compare protocols, decide where to deposit |
| `clicks_simulate_split` | Preview payment split for amount | Calculate expected yield before depositing |
| `clicks_get_agent_info` | Agent registration and balance | Monitor treasury status |
| `clicks_get_referral_stats` | Referral network earnings | Track referral revenue |

## Links

- [Clicks Protocol](https://clicksprotocol.xyz)
- [GitHub](https://github.com/clicks-protocol/clicks-protocol)
- [npm SDK](https://www.npmjs.com/package/@clicks-protocol/sdk)
- [AgentScope Docs](https://doc.agentscope.io/)
