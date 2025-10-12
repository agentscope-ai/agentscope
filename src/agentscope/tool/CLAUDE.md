# Tool Module

**Location:** `src/agentscope/tool/`
**Parent:** [AgentScope Root](../CLAUDE.md)

## 🧩 Overview

The tool module provides a comprehensive framework for tool management, execution, and integration with agents.

## 📚 Key Files

### Core Components
- **Toolkit class** - Central registry for tool functions
- **Execution engine** - Safe and controlled tool invocation
- **MCP integration** - Model Context Protocol support

### Advanced Capabilities
- **Agentic tools management** - Tools that can manage other tools
- **Meta tool support** - Tools that provide capabilities for tool management

## 🎯 Features

### Tool Control
- **Sync/async tool functions** - Flexible execution modes
- **Streaming support** - Real-time tool execution handling
- **Post-processing** - Results manipulation and formatting
- **User interruption** - Configurable tool execution interruption handling

### Tool Types
- **Basic tools** - Simple function wrappers
- **Complex tools** - Multi-step tool chains
- **MCP tools** - External tool integration via MCP protocol

## 🚀 Usage Examples

```python
from agentscope.tool import Toolkit, execute_python_code, execute_shell_command

# Create toolkit and register tools
toolkit = Toolkit()
toolkit.register_tool_function(execute_python_code)
toolkit.register_tool_function(execute_shell_command)
```

## 🔧 Dependencies

- **MCP client** (`src/agentscope/mcp/`)

### Fine-Grained Control
"Developers can obtain the MCP tool as a **local callable function**, and use it anywhere (e.g. call directly, pass to agent, wrap into more complex tools)

## ⚙️ Configuration

Tool execution parameters and security settings for safe operation in multi-agent environments.

## 🔗 Related Modules

- **[Agent Framework](../agent/CLAUDE.md)**
- **[MCP Integration](../mcp/CLAUDE.md)**

---

🏠 [Back to AgentScope Root](../CLAUDE.md)