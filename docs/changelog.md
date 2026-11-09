# CHANGELOG of v1.0.0

> ➡️ change; ✅ new feature; ❌ deprecate

The overall changes from v0.x.x to v1.0.0 are summarized below.

## Overview
- ✅ Support asynchronous execution throughout the library
- ✅ Support tools API thoroughly


## ✨Session
- ✅ Support automatic state management
- ✅ Support session/application-level state management


## ✨Tracing
- ✅ Support OpenTelemetry-based tracing
- ✅ Support third-party tracing platforms, e.g. Arize-Phoenix, Langfuse, etc.


## ✨MCP
- ✅ Support both client- and function-level control over MCP by a new MCP module
- ✅ Support both "pay-as-you-go" and persistent session management
- ✅ Support streamable HTTP, SSE and StdIO transport protocols


## ✨Memory
- ✅ Support long-term memory by providing a `LongTermMemoryBase` class
- ✅ Provide a Mem0-based long-term memory implementation
- ✅ Support both static- and agent-controlled long-term memory modes


## Formatter
- ✅ Support prompt construction/formatting with token count estimation
- ✅ Support tools API in multi-agent prompt formatting


## Model
- ❌ Deprecate model configuration, use explicit object instantiation instead
- ✅ Provide a new `ModelResponse` class for structured model responses
- ✅ Support asynchronous model invocation
- ✅ Support reasoning models
- ✅ Support any combination of streaming/non-streaming, reasoning/non-reasoning and tools API


## Agent
- ❌ Deprecate `DialogAgent`, `DictDialogAgent` and prompt-based ReAct agent class
- ➡️ Expose memory, formatter interfaces to the agent's constructor in ReActAgent
- ➡️ Unify the signature of pre- and post- agent hooks
- ✅ Support pre-/post-reasoning and pre-/post-acting hooks in ReActAgent class
- ✅ Support asynchronous agent execution
- ✅ Support interrupting agent's replying and customized interruption handling
- ✅ Support automatic state management
- ✅ Support parallel tool calls
- ✅ Support two-modes long-term memory in ReActAgent class


## Tool
- ✅ Provide a more powerful `Toolkit` class for tools management
- ✅ Provide a new `ToolResponse` class for structured and multimodal tool responses
- ✅ Support group-wise tool management
- ✅ Support agent to manage tools by itself
- ✅ Support post-processing of tool responses
- Tool function
  - ✅ Support both async and sync functions
  - ✅ Support both streaming and non-streaming return


## Evaluation
- ✅ Support ReAct agent-oriented evaluation
- ✅ Support Ray-based distributed and concurrent evaluation
- ✅ Support statistical analysis over evaluation results


## AgentScope Studio
- ✅ Support runtime tracing
- ✅ Provide a built-in copilot agent named Friday


## Logging
- ❌ Deprecate `loguru` and use Python native `logging` module instead


## Distribution
- ❌ Deprecate distribution functionality momentarily, a new distribution module is coming soon


## RAG
- ❌ Deprecate RAG functionality momentarily, a new RAG module is coming soon


## Parsers
- ❌ Deprecate parsers module


## WebBrowser
- ❌ Deprecate the `WebBrowser` class and shift to MCP-based web browsing
