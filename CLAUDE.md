# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AgentScope is a multi-agent AI framework focused on LLM-powered agents with reasoning, tool use, and memory capabilities.

**Python**: 3.10+
**Package location**: `src/agentscope/`

## Common Commands

```bash
# Install in development mode with all dependencies
pip install -e "agentscope[full]"

# Run tests
pytest tests/                                    # all tests
pytest tests/model_openai_test.py               # single file
pytest tests/ -k "memory"                       # filter by keyword
pytest tests/ --forked                           # isolate per-test process

# Pre-commit hooks (run before committing)
pre-commit run --all-files

# Type checking
mypy src/agentscope/

# Linting
pylint src/agentscope/
flake8 src/agentscope/
black --check src/agentscope/
```

## Architecture

```
src/agentscope/
├── agent/           # Agent implementations (ReActAgent, UserAgent, A2AAgent, RealtimeAgent)
├── model/           # LLM model adapters (OpenAI, Anthropic, DashScope, Gemini, Ollama)
├── formatter/       # Message format converters for different model APIs
├── tool/            # Tool system (Toolkit, tool functions)
├── memory/          # Memory implementations (InMemory, Redis, SQLAlchemy, Mem0, ReMe)
├── pipeline/        # Multi-agent orchestration (MsgHub)
├── rag/            # RAG components (readers, vector stores)
├── session/         # Session management
├── embedding/       # Embedding models
├── token/          # Token counting
├── a2a/            # Agent-to-Agent protocol
├── realtime/       # Real-time voice interaction
├── mcp/            # Model Context Protocol clients
├── tracing/        # OpenTelemetry tracing
├── evaluate/       # Evaluation and benchmarking
├── tuner/          # Model tuning and finetuning
└── message/        # Message types (Msg, TextBlock, ToolUseBlock, etc.)
```

### Core Abstraction Hierarchy

```
AgentBase (abstract)
├── ReActAgentBase
│   └── ReActAgent          # Main agent with ReAct reasoning loop
├── UserAgent              # Human input handling
├── A2AAgent               # A2A protocol agent
└── RealtimeAgent          # Voice interaction agent

ChatModelBase (abstract)
├── OpenAIChatModel
├── AnthropicChatModel
├── DashScopeChatModel
├── GeminiChatModel
└── OllamaChatModel
```

### Key Patterns

1. **Tool Registration**: `Toolkit.register_tool_function()` registers functions as tools (no decorator needed)
2. **Message Flow**: `Msg` → `Formatter` → Model API → `ChatResponse` → `Formatter` → `Msg`
3. **Memory**: `MemoryBase` → `InMemoryMemory` / `RedisMemory` / `AsyncSQLAlchemyMemory`
4. **Async**: All agent operations are async; use `await agent(msg)`

## Code Standards

From `.github/copilot-instructions.md`:

### File Naming
- Internal modules in `src/agentscope/` use `_` prefix (e.g., `_toolkit.py`)
- Public API exposed through `__init__.py`

### Lazy Loading
- Third-party imports at point of use, not at file top
- Use factory pattern for conditional base class imports

### Docstrings (required)
```python
def func(a: str, b: int | None = None) -> str:
    """{description}

    Args:
        a (`str`):
            The argument a
        b (`int | None`, optional):
            The argument b

    Returns:
        `str`:
            The return str
    """
```

### Pre-commit
- All code must pass pre-commit checks before committing
- Skip checks only for agent system prompt parameters (to avoid `\n` formatting issues)

## Examples

```bash
# ReAct agent
python examples/agent/react_agent/main.py

# Real-time voice (requires FastAPI/WebSocket)
python examples/agent/realtime_voice_agent/run_server.py

# A2A server
python examples/agent/a2a_agent/setup_a2a_server.py
```
