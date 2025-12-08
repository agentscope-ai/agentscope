# Meta Planner Agent Example

An advanced AI agent example that demonstrates sophisticated task planning and execution capabilities using AgentScope. The Meta Planner breaks down complex tasks into manageable subtasks and orchestrates specialized worker agents to complete them efficiently.

## Overview

The Meta Planner agent is designed to handle complex, multi-step tasks that would be difficult for a simple agent to manage directly. It uses a planning-execution pattern where:

1. **Complex tasks are decomposed** into smaller, manageable subtasks
2. **Worker agents can be dynamically created** with appropriate tools for each subtask
3. **Progress is tracked and managed** through a roadmap system
4. **Results are coordinated** to achieve the overall goal

This approach enables handling sophisticated workflows like data analysis, research projects, content creation, and multi-step problem solving.

## Key Features

- **Intelligent Task Decomposition**: Automatically breaks down complex requests into executable subtasks
- **Progress Tracking**: Maintains a structured roadmap with status tracking for all subtasks
- **Dynamic Worker Management**: Creates and manages specialized worker agents with relevant toolkits
- **State Persistence**: Saves and restores agent state for long-running tasks
- **Flexible Modes**: Can operate in simple ReAct mode or advanced planning mode based on task complexity

## Architecture

### Core Components

1. **MetaPlanner** (`_meta_planner.py`): The main agent class that extends ReActAgent with planning capabilities
2. **Planning Tools** (`_planning_tools/`):
   - `PlannerNoteBook`: Manages session context and user inputs
   - `RoadmapManager`: Handles task decomposition and progress tracking
   - `WorkerManager`: Creates and manages worker agents
3. **System Prompts** (`_built_in_long_sys_prompt/`): Detailed instructions for (worker) agent behavior
4. **Demo Entry Point** (`main.py`): The main function to start the application with meta planner agent.


## Prerequisites for Running This Example

### Required Environment Variables

```bash
# Anthropic API key for the Claude model
export ANTHROPIC_API_KEY="your_anthropic_api_key"

# Tavily API key for search functionality
export TAVILY_API_KEY="your_tavily_api_key"
```

### Optional Environment Variables

```bash
# Custom working directory for agent operations (default: ./meta_agent_demo_env)
export AGENT_OPERATION_DIR="/path/to/custom/working/directory"
```

## Usage

### Basic Usage

Run the agent interactively:

```bash
cd examples/agent/meta_planner_agent
python main.py
```

The agent will start in chat mode where you can provide complex tasks. For example:

- "Create a comprehensive analysis of Meta's stock performance in Q1 2025"
- "Research and write a 7-day exercise plan with detailed instructions"
- "Analyze the latest AI trends and create a summary report"


### Example Interactions

1. **Data Analysis Task**:
   ```
   User: "Analyze the files in my directory and create a summary report"
   ```

2. **Research Task**:
   ```
   User: "Research Alibaba's latest quarterly results and competitive position"
   ```


## Configuration

### Agent Modes

The Meta Planner supports three operation modes:

- **`dynamic`** (default): Automatically switches between simple ReAct and planning mode based on task complexity
- **`enforced`**: Always uses planning mode for all tasks
- **`disable`**: Only uses simple ReAct mode (no planning capabilities)

### Tool Configuration

The agent uses two main toolkits:

1. **Planner Toolkit**: Planning-specific tools for task decomposition and worker management
2. **Worker Toolkit**: Comprehensive tools including:
   - Shell command execution
   - File operations
   - Web search (via Tavily)
   - Filesystem access (via MCP)

### State Management

Agent states are automatically saved during execution:

- **Location**: `./agent-states/run-YYYYMMDDHHMMSS/`
- **Types**:
  - `state-post_reasoning-*.json`: After reasoning steps
  - `state-post-action-{tool_name}-*.json`: After tool executions


### State Recovery

If an agent gets stuck or fails:

1. Check the latest state file in `./agent-states/`
2. Resume from the last successful state:
   ```bash
   python main.py --load_state path/to/state/file.json
   ```

## Advanced Customization

### Adding New Tools

1. Create tool functions following AgentScope patterns
2. Register tools in the appropriate toolkit:
   ```python
   worker_toolkit.register_tool_function(your_custom_tool)
   ```

### Custom MCP Clients

Add additional MCP clients in `main.py`:

```python
mcp_clients.append(
    StdIOStatefulClient(
        name="custom_mcp",
        command="npx",
        args=["-y", "your-mcp-server"],
        env={"API_KEY": "your_key"},
    )
)
```

### System Prompt Modifications

Modify prompts in `_built_in_long_sys_prompt/` to customize agent behavior.