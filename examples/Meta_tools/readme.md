# Meta Tools System for AgentScope

Meta Tools is an innovative extension for AgentScope designed to manage and invoke large, diverse toolsets efficiently.
By using a layered architecture, it drastically reduces context length, lowers cognitive load, and improves tool selection accuracy.

## Background

In the native AgentScope framework, the `Toolkit` exposes all active tools directly to a `ReActAgent`. While this approach works well for smaller tool sets, it faces significant challenges when dealing with numerous and diverse tools:

1. **Excessive Context Length** – Long prompts reduce LLM reasoning efficiency
2. **Cognitive Overload** – The agent must handle tasks *and* search through a large tool pool, which hurts performance

## Our Solution: Three-Layer Meta Tools Architecture

Meta Tools organizes tools into functional categories and applies intelligent selection within each category.
From the main agent's perspective, only high-level categories are visible — the actual tools are selected internally.

```
┌─────────────────────────┐
│ Level 3: MetaManager    │ ← Exposed to main agent, shows categories only
└──────────┬──────────────┘
           │
┌──────────▼──────────────┐
│ Level 2: CategoryManager│ ← Manages category tools, multi-round reasoning
└──────────┬──────────────┘
           │
┌──────────▼──────────────┐
│ Level 1: Normal Tool    │ ← MCP tools, built-ins, custom functions
└─────────────────────────┘
```

**Benefits**:

- **Reduced Context Length**: Main agent only sees category-level abstractions, not individual tools
- **Improved Selection Accuracy**: Specialized prompts and focused tool sets enhance selection precision
- **Scalable Architecture**: System performance remains stable as tool count increases
- **Agent-Level Decoupling**: Separates detailed tool selection from the agent, enabling focus on task decomposition and state monitoring

![img.png](img.png)



## How to Run This Example

**Environment**

* **LLM API Key**: Configure your environment variable:
```bash
export DASHSCOPE_API_KEY="your_dashscope_api_key"
export GAODE_API_KEY="your_gaode_api_key"
export BING_API_KEY="your_bing_api_key_from_modelscope"
export TRAIN_API_KEY="your_12306_api_key_from_modelscope"
```
**MCP Service Setup**

* **Gaode Maps** (Navigation): [Gaode Open Platform](https://lbs.amap.com/)
* **Bing Search** (Information Retrieval): [ModelScope MCP Service](https://www.modelscope.cn/mcp/servers/@yan5236/bing-cn-mcp-server)
* **12306 Train Services** (Information Retrieval): [ModelScope MCP Service](https://www.modelscope.cn/mcp/servers/@Joooook/12306-mcp)

Update your sse API keys in your environment variable.

**Run Example**

   ```bash
   python example.py
   ```

The system automatically constructs the Meta Tool System based on the categories and tool descriptions defined in `Meta_tool_config.json`, and launches an interactive agent named Friday to assist you with various tasks across three main categories: Information Retrieval, Programming & Tech Support, and Location & Navigation.
For example, you can try a prompt like:
`I'm going to drive from West Lake in Hangzhou to Zhoushan. Please give me the driving route and a travel guide for Zhoushan.`


## Key Features


```mermaid
flowchart TD
    A[User request<br/>objective + exact_input] --> B[Initial tool selection<br/>LLM chooses suitable tools]
    B --> C{Does the LLM generate tool calls?}
    
    C -->|Tool calls generated<br/>Normal flow| D[Execute tool set<br/>Log execution results]
    C -->|No tool calls<br/>Insufficient input| E[Insufficient input case<br/>Explain missing elements in detail]
    C -->|No tool calls<br/>No suitable tools| F[No suitable tool case<br/>Explain why this task type cannot be handled]
    
    E --> G[Return<br/>no_tool_calls_generated: true<br/>reasoning: Detailed analysis of missing information<br/>next_steps: Suggestions for additional input]
    F --> H[Return<br/>no_tool_calls_generated: true<br/>reasoning: Explanation of tool capability limits<br/>next_steps: Alternative suggestions]
    
    D --> I[Result evaluation<br/>LLM checks whether the objective is satisfied]
    I --> J{Evaluation decision}
    
    J -->|No new tool calls<br/>Task complete| K[Generate final summary<br/>Return SUCCESS]
    J -->|New tool calls needed<br/>Continue| L[Execute new tools<br/>Prepare for next evaluation round]
    
    L --> M{Check iteration count}
    M -->|&lt; max_iterations (5)| N[iteration++<br/>Store to memory]
    M -->|≥ max_iterations (5)| O[Max iterations reached<br/>Summarize all execution results]
    
    N --> D
    
    K --> P[Final result<br/>task_completed: true<br/>summary: Evaluation summary<br/>all_execution_results]
    O --> Q[Final result<br/>task_completed: false<br/>max_iterations_reached: true<br/>summary: LLM-generated summary]
    
    R[TemporaryMemory<br/>Record execution history] -.-> I
    D -.-> R
    
    style A fill:#e1f5fe
    style C fill:#fff3e0
    style E fill:#ffeb3b
    style F fill:#ff9800
    style G fill:#fff59d
    style H fill:#ffcc02
    style K fill:#c8e6c9
    style O fill:#ffcdd2
    style I fill:#fff3e0
    style J fill:#fce4ec
```

### 1. Unified Interface Design
From the external agent's perspective, each `CategoryManager` appears as a standard tool function with consistent schema:

```json
{
    "type": "function",
    "function": {
        "name": "<category_name>",
        "description": "<category_description> This category automatically selects and operates the most appropriate tool based on your objective and input.",
        "parameters": {
            "type": "object",
            "properties": {
                "objective": {
                    "type": "string",
                    "description": "A clear and well-defined description of the goal you wish to accomplish using tools in this category."
                },
                "exact_input": {
                    "type": "string",
                    "description": "The precise, detailed, and complete input or query to be processed by the selected tool."
                }
            },
            "required": ["objective", "exact_input"]
        }
    }
}
```

From the main agent's perspective, it only needs to call the corresponding high-level category tool and provide the `objective` and `exact_input` parameters to receive a complete and detailed execution flow along with a summary—without having to worry about which specific Level 1 tools were executed behind the scenes.

### 2. Intelligent Multi-Round Execution

* **Select → Execute → Evaluate → Continue/Summarize**: Each category starts by selecting the most suitable tool, executes it, evaluates the result, and decides whether to proceed with further actions or produce a final summary
* **Isolated in-category memory context**: Maintains a dedicated memory space for each category, preserving execution history and reasoning steps without polluting the main agent's context
* **Automatic recovery on failure with fallback tools**: If a selected tool fails, the system automatically retries with alternative tools and adjusted parameters to ensure task completion

### 3. Configuration-Driven Setup
Once tools are added to the global toolkit, they are grouped into meta categories via `Meta_tool_config.json`:
```json
{
    "Information Retrieval": {
        "description": "...",
        "tool_usage_notes": "...",
        "tools": [...]
    }
}
```
In the default configuration, `Meta_tool_config.json` defines a basic categorization for the MCP tools used in `example.py`. When DIY your meta tool system, remember to update `Meta_tool_config.json` along with your agent file.

### 4. Execution Situations
- **Normal Execution**: Complete execution history with results and comprehensive summaries

The Meta Tool system includes robust countermeasures for all potential issues it may encounter:

- **Insufficient Input**: Detailed explanation of missing required elements without tool execution
- **No Suitable Tools**: Clear reasoning about functional limitations with alternative suggestions, without tool execution
- **Execution Failures**: Automatic error recovery with alternative tool selection and parameter adjustment

## Advanced Customization

* **Custom Categories**: Edit `Meta_tool_config.json` to create new functional domains or reorganize existing tools into different categories
* **Custom Prompts**: Modify the selection, evaluation, and summary templates in `meta_tool_prompts/` to adapt reasoning behavior for specific domains or workflows
* **New Tools**: Integrate additional MCP services, built-in AgentScope tools, or custom local functions, and assign them to the most relevant categories for seamless integration

You can freely integrate multiple custom tools into the system, organize them into your own Meta Tool categories, and tailor the workflow to your needs—give it a try and explore the possibilities!