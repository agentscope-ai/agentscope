Module: `src/agentscope/model/`
Responsibility: Uniform LLM provider abstraction for model-agnostic programming

## Key Files

### Core Interfaces
- Model wrapper base classes for different LLM providers
- Async invocation support with streaming capabilities
- Model-agnostic programming - "Program once, run with all models"

### Supported Providers
- OpenAI (GPT series)
- Anthropic (Claude series)
- DashScope (Alibaba Cloud)
- DeepSeek
- Gemini
- And more...

## Dependencies

- Formatter system (`src/agentscope/formatter/`)

## Features

### Universal Support
- **Async execution** - All models support asynchronous operations
- **Streaming/non-streaming** - Flexible response handling
- **Stream handling** - Real-time and batch processing

### Capabilities
- **Reasoning models** - Support for chain-of-thought and logical reasoning
- **Flexible output modes** - From simple responses to complex structured outputs

## Usage Examples

```python
# OpenAI model
model = OpenAIChatModel(
    model_name="gpt-4",
    api_key=os.environ["OPENAI_API_KEY"],
    stream=True
)

# DashScope model
model = DashScopeChatModel(
    model_name="qwen-max",
    api_key=os.environ["DASHSCOPE_API_KEY"]
)
```

## Model Providers

### Available Integrations
- **OpenAI** - GPT-3.5, GPT-4, and other OpenAI models

## ⚙️ Configuration

Model-specific configuration including:
- API keys and endpoints
- Model parameters (temperature, max_tokens, etc.)
- Provider-specific optimizations

## 🔗 Related Modules

- **[Agent Framework](../agent/CLAUDE.md)**
- **[Prompt Formatters](../formatter/CLAUDE.md)**

---

[Back to Project Root](../CLAUDE.md)