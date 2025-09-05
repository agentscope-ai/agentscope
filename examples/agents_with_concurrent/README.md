# Multi-Perspective Discussion System

## What This Example Demonstrates

This example showcases a **Parallel Multi-Perspective Discussion System** that analyzes any topic from four distinct viewpoints simultaneously. When you present a topic, the system instantly gathers perspectives from different types of thinkers, providing a comprehensive and balanced view of the subject matter.

### Key Features:

- **True Parallel Processing**: All four perspectives are generated simultaneously using AgentScope's async capabilities
- **Maximum Efficiency**: 4x faster than sequential processing - get comprehensive analysis in the time it takes for one response
- **Diverse Viewpoints**: Four distinct thinking styles provide comprehensive coverage:
  - **😊 Optimist**: Focuses on opportunities, benefits, and positive outcomes
  - **🤓 Realist**: Provides balanced, fact-based analysis with practical considerations
  - **🤨 Critic**: Identifies risks, challenges, and potential problems
  - **💡 Innovator**: Explores creative solutions and future possibilities
- **Non-blocking Execution**: While agents generate response in parallel, the system remains responsive
- **Resource Optimization**: Efficient use of API calls and processing time through async/await patterns

## AgentScope Async Advantages

### 🚀 **Performance Benefits**

**Traditional Sequential Approach:**

```
Agent 1: ████████ (8 seconds)
Agent 2: ████████ (8 seconds)
Agent 3: ████████ (8seconds)
Agent 4: ████████ (8seconds)
Total Time: ~32 seconds
```

**AgentScope Async Approach:**

```
Agent 1: ████████ (8 seconds)
Agent 2: ████████ (8 seconds)
Agent 3: ████████ (8 seconds)
Agent 4: ████████ (8seconds)
Total Time: ~8 seconds (75% time savings!)
```

### ⚡ **Concurrency in Action**

```python
# All agents start generating response simultaneously
discussion_tasks = [agent(msg) for agent in agents]
perspectives = await asyncio.gather(*discussion_tasks)
```

This single line demonstrates AgentScope's power:

- **Immediate dispatch**: All 4 agents start processing instantly
- **Parallel execution**: No waiting for one agent to finish before starting the next
- **Coordinated completion**: Results are collected as they become available

### 🎯 **Real-World Impact**

- **Interactive Experiences**: Users get responses 4x faster, improving engagement
- **Scalability**: System can handle multiple users or topics without performance degradation
- **Cost Efficiency**: Optimal use of computational resources
- **Professional Applications**: Suitable for real-time decision support systems

## How to Run This Example

1. **Set Environment Variable**:

   ```bash
    export DASHSCOPE_API_KEY="your_dashscope_api_key_here"
   ```

2. **Run the script**:

   ```bash
   python parallel_discussion.py
   ```

## Customizing the Discussion System

### Adding New Perspectives

You can easily add new types of thinkers to the discussion:

```Python
# Add new perspective prompts
ECONOMIST_PROMPT = """You are an economist who analyzes topics from economic impact and market perspectives.
Focus on costs, benefits, market dynamics, and economic implications.
Keep your responses analytical and economics-focused."""

# Update the agents list
agents = [
    create_agent("Optimist", OPTIMIST_PROMPT),
    create_agent("Realist", REALIST_PROMPT),
    create_agent("Critic", CRITIC_PROMPT),
    create_agent("Innovator", INNOVATOR_PROMPT),
    create_agent("Economist", ECONOMIST_PROMPT),  # New - still processes in parallel!
]
```

**Async Benefit**: Adding more perspectives doesn't increase total processing time significantly. They all run in parallel!

## Notes

⚡ **Async Advantage**: The system demonstrates AgentScope's ability to handle multiple concurrent agent operations efficiently.

🎯 **Scalability**: Adding more perspectives doesn't significantly impact total response time due to parallel processing.

🔧 **Real-world Ready**: This pattern is perfect for production systems requiring fast, comprehensive analysis.

🎭 **Interactive Experience**: Users experience near-instant comprehensive responses instead of waiting for sequential processing.

💡 **Framework Power**: Showcases why AgentScope's async design makes it ideal for multi-agent applications.