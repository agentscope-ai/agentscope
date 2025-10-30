# Router Agent Examples

This repository contains two different approaches to implement intelligent routing in AgentScope. Choose the approach that best fits your needs.

## 📁 Files Overview

- `routing_by_structured_output.py`: Router using structured output for explicit decision making
- `routing_by_tool_calls.py`: Router using integrated tool calls for seamless routing

------

## 🎯 Structured Output Router (`routing_by_structured_output.py`)

### What This Example Demonstrates

This example showcases a **Router Agent** with **explicit routing control** using structured output. The router makes transparent decisions about where to route questions and provides clear reasoning for each routing choice.

#### Key Features:

- **Explicit Routing Decisions**: Uses Pydantic models to structure routing choices with clear reasoning
- **Transparent Decision Making**: See exactly why each question was routed to a specific agent
- **Separation of Concerns**: Routing logic is separated from agent execution
- **Structured Metadata**: All routing decisions are captured in structured format
- **High Customization**: Easy to add complex routing rules and conditions
- **Audit Trail**: Full visibility into routing decisions for debugging and optimization

The router follows a transparent decision-making process:

1. **Analyze** the user's question using structured reasoning
2. **Generate** a structured RouterResponse with agent_name and routing_reason
3. **Execute** routing based on the structured decision
4. **Provide** clear feedback on routing choices

### How to Run This Example

1. **Set Environment Variable**:

   ```
   export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
   ```

2. **Run the script**:

   ```
   python routing_by_structured_output.py
   ```

3. **Interact with the system**:

   ```
   user: hello
   Router: Hello! How can I assist you today?
   user: Calculate 15 * 8 + 23
   Router: I'm going to route this calculation to our math specialist for an accurate response.
   Math: The calculation 15 * 8 + 23 equals 143.
   user: When did World War II end?
   Router: I will route your question to our history specialist who can provide you with the exact date of when World War II ended.
   History: World War II ended on September 2, 1945, ...
   ```

### Customizing the Router System

You can easily extend the system with additional specialized agents:

#### Adding New Specialized Agents

```
#1. Define new agent prompt
SCIENCE_PROMPT = "You are a science assistant specializing in physics, chemistry, and biology."

#2. Create the agent in main()
agent_science = create_agent("Science", SCIENCE_PROMPT)

#3. Update routing logic
elif agent_name == "Science":msg = await agent_science(user_msg)

# 4. Update RouterResponse model (optional - for validation)
class RouterResponse(BaseModel):
		agent_name: Literal["Math", "History", "Science", "router"] | None = Field(...)
```

#### Modifying Routing Rules

Update the `ROUTER_PROMPT`:

```
# Add to DECISION PROCESS section:
    - If it's a science question, route it to the Science agent.

# Add to Available Agents section:
    - Science: Specialized in physics, chemistry, and biology questions.
```

### Notes

⚠️ **Structured Output Requirement**: Ensure your model supports structured output generation. The RouterResponse model requires compatible model capabilities.

🔍 **Debugging Advantage**: This approach provides full visibility into routing decisions through the structured metadata.

🎛️ **High Control**: Perfect for complex routing scenarios where you need explicit control over decision logic.

📊 **Analytics Ready**: Structured outputs make it easy to analyze routing patterns and optimize performance.

------

## 🔧 Tool Call Router (`routing_by_tool_calls.py`)

### What This Example Demonstrates

This example showcases a **Router Agent** with **integrated tool-based routing** where specialized agents are accessible as tools. The router seamlessly decides when to use tools or handle questions directly.

#### Key Features:

- **Integrated Tool System**: Specialized agents are registered as tools within the router
- **Seamless Routing**: Router handles tool selection and execution internally
- **Simplified Architecture**: Single agent with multiple capabilities through tools
- **Automatic Tool Selection**: ReActAgent automatically chooses appropriate tools
- **Reduced Complexity**: No external routing logic needed
- **Natural Conversation**: Tool usage is transparent to the user

The router follows an integrated approach:

1. **Analyze** user questions within the ReActAgent framework
2. **Select** appropriate tools automatically based on question content
3. **Execute** tool calls or respond directly as needed
4. **Deliver** unified responses regardless of internal tool usage

### How to Run This Example

1. **Set Environment Variable**:

   ```
   export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
   ```

2. **Run the script**:

   ```
   python routing_by_tool_calls.py
   ```

3. **Interact with the system**:

   ```
   user: hello
   Router: Hello! How can I assist you today?
   user: Calculate 15 * 8 + 23
   Router: {
       "type": "tool_use",
       "id": "call_680b9fe8839a41cfb942d1",
       "name": "generate_math_response",
       "input": {
           "question": "Calculate 15 * 8 + 23"
       }
   }
   Math: The calculation 15 * 8 + 23 equals 143.
   system: {
       "type": "tool_result",
       "id": "call_680b9fe8839a41cfb942d1",
       "name": "generate_math_response",
       "output": [
           {
               "type": "text",
               "text": "The calculation 15 * 8 + 23 equals 143."
           }
       ]
   }
   Router: The calculation 15 * 8 + 23 equals 143.
   ```

### Customizing the Router System

#### Adding New Specialized Agents

```
#1. Create new tool function
async def generate_science_response(question: str) ->ToolResponse:
		"""Generate a science response.

    Args:
        question (str): The science question to answer.
    """
    agent_science = create_agent("Science", SCIENCE_PROMPT)
    msg_res = await agent_science(Msg(name="user", content=question, role="user"))
    return ToolResponse(content=msg_res.get_content_blocks("text"))

# 2. Register with router
router_agent.toolkit.register_tool_function(generate_science_response)
```

### Notes

🔧 **Tool Function Requirements**: Tool functions must have proper type hints and return ToolResponse objects for correct registration.

🎯 **Automatic Selection**: The ReActAgent automatically determines when to use tools vs. direct responses based on the conversation context.

⚡ **Performance**: May require fewer API calls when the router can handle simple questions directly without tool usage.

🛠️ **Extensibility**: Easy to add new capabilities by registering additional tool functions with descriptivedocstrings.

------

## 🤔 Which Approach Should You Choose?

Both approaches have their strengths and can be suitable for different scenarios. Here's a more balanced comparison to help you decide

### Consider **Structured Output** If:

- ✅ You need explicit control over routing decisions
- ✅ You want to audit and analyze routing patterns
- ✅ You have complex routing rules
- ✅ You need to debug routing logic
- ✅ You want transparency in decision-making

### Consider **Tool Calls** If:

- ✅ You prefer simpler implementation
- ✅ You want integrated routing behavior
- ✅ You have straightforward routing needs
- ✅ You want to leverage ReActAgent capabilities
- ✅ You prefer automatic tool selection

Both approaches demonstrate the flexibility of AgentScope for building intelligent multi-agent systems!