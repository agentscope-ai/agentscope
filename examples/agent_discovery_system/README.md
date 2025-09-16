# Agent Discovery System Example

This example demonstrates the Agent Discovery System, a cognitive framework for eureka moments and serendipitous insights built on AgentScope, now with an **integrated web interface** and **Gemini API** support.

## Overview

The Agent Discovery System enables active discovery of novel insights from personal knowledge bases through:

- **Cognitive exploration** using multiple specialized agents
- **Bayesian surprise calculation** to identify paradigm-shifting discoveries  
- **Budget-controlled exploration** with configurable limits
- **Multi-agent coordination** for comprehensive analysis
- **Graph-based knowledge management** for relationship discovery
- **🆕 Web-based user interface** for easy interaction
- **🆕 Gemini API integration** for powerful language model capabilities
- **🆕 Markdown file support** for knowledge base creation

## Features Demonstrated

### Web Interface Features
1. **Drag & Drop Upload**: Easy markdown file upload with progress tracking
2. **Real-time Progress**: Live updates via WebSocket connection
3. **Interactive Configuration**: Adjust exploration depth, token budget, etc.
4. **Visual Discovery Steps**: Step-by-step visualization of the discovery process
5. **Rich Results Display**: Formatted insights, hypotheses, and research questions
6. **Session Management**: Track discovery sessions with unique IDs

### Core Workflow
1. **Knowledge Base Processing**: Extract concepts and build knowledge graphs from MD files
2. **Active Exploration**: Use curiosity-driven algorithms to explore knowledge boundaries
3. **Surprise Assessment**: Calculate Bayesian surprise to identify significant discoveries
4. **Insight Generation**: Generate novel hypotheses and connections
5. **Meta-Analysis**: Assess confidence and identify future research directions

### Agent Coordination
- **UserProxyAgent**: Interface and budget management
- **OrchestratorAgent**: Central coordination and workflow control
- **KnowledgeGraphAgent**: Local knowledge graph management
- **Mock Agents**: Simplified implementations for demonstration

## Quick Start (Web Interface)

### 1. Install Dependencies

```bash
# Install all required dependencies
pip install -r requirements.txt

# Or install individually
pip install fastapi uvicorn websockets google-generativeai
```

### 2. Set Up Gemini API Key

```bash
# Get your API key from: https://makersuite.google.com/app/apikey
export GEMINI_API_KEY="your-gemini-api-key"
```

**For detailed setup instructions, see [GEMINI_SETUP.md](GEMINI_SETUP.md)**

### 3. Run the System

```bash
# Easy startup (recommended)
python run_discovery_system.py

# Or manually start the server
python discovery_server.py
```

### 4. Use the Web Interface

1. **Upload Knowledge Base**: Drag and drop your `.md` files
2. **Configure Exploration**: Set your initial idea and parameters
3. **Start Discovery**: Watch real-time progress and results
4. **View Insights**: Explore discoveries, hypotheses, and research questions

![Web Interface Demo](screenshot.png) <!-- Add screenshot later -->

## Command Line Usage (Advanced)

For programmatic usage or integration:

### Prerequisites

```bash
# Install AgentScope (if not already installed)
pip install agentscope

# Install Gemini dependencies
pip install google-generativeai

# Install optional dependencies for enhanced functionality
pip install sentence-transformers  # For better embeddings
pip install faiss-cpu             # For fast similarity search
pip install networkx              # For graph analysis
```

### Environment Setup

Set your Gemini API key (optional - example will use mock responses if not provided):

```bash
export GEMINI_API_KEY="your-gemini-api-key"
```

You can get your Gemini API key from: https://makersuite.google.com/app/apikey

**For detailed setup instructions, see [GEMINI_SETUP.md](GEMINI_SETUP.md)**

### Quick Setup Test

Run the setup test to verify your configuration:

```bash
python test_gemini.py
```

### Run the Example

```bash
python main.py
```

## Example Output

The system will:

1. Create a sample knowledge base with documents about AI and cognitive psychology
2. Initialize the discovery system with budget constraints
3. Run exploration loops to find novel connections
4. Generate insights, hypotheses, and research questions
5. Provide a comprehensive discovery report

Sample output:
```
🔍 Agent Discovery System - Example Usage
==================================================

📚 Setting up example knowledge base...
📁 Created example knowledge base with 5 documents at: ./example_knowledge_base

🎯 Initial idea: How do machine learning algorithms relate to cognitive psychology?
🔎 Focus areas: artificial intelligence, human cognition, learning theories, neural networks

🚀 Starting discovery process...
Discovery session started: abc123-session-id
Running exploration loop 1...
Running exploration loop 2...
Session terminated: natural_completion

📊 Discovery Results
==============================
Session ID: abc123-session-id
Total time: 45.3 seconds
Loops completed: 2
Surprise level: 0.67
Coverage: substantial

💡 Key Insights:
  1. Neural attention mechanisms mirror human cognitive attention processes
  2. Reinforcement learning parallels psychological conditioning theories
  3. Memory hierarchies in AI systems reflect human memory models

🔍 Discoveries (3):
  1. Cross-domain connection between deep learning and cognitive load theory (confidence: 0.85)
  2. Unexpected similarity between neural network layers and information processing stages (confidence: 0.78)
  3. Novel application of connectivism to explain AI learning patterns (confidence: 0.72)

🧪 Hypotheses Generated (2):
  1. Attention mechanisms in transformers could improve cognitive modeling accuracy
  2. Cognitive psychology principles could enhance explainable AI development

❓ Research Questions (2):
  1. How can cognitive load theory inform neural network architecture design? (priority: high)
  2. What insights from human memory research could improve AI memory systems? (priority: medium)

💰 Budget Utilization:
  Tokens used: 7543/10000
  Time used: 45.3/1800.0 seconds
  Cost used: $2.15/$5.00
  Termination reason: natural_completion
```

## Customization

### Adjusting Budget Constraints

```python
# Use different Gemini models for different capabilities
from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter

# For reasoning-heavy tasks
reasoning_model = GeminiChatModel(
    model_name="gemini-2.5-pro",
    generate_kwargs={"temperature": 0.3}
)

# For creative insight generation  
creative_model = GeminiChatModel(
    model_name="gemini-2.5-flash-lite", 
    generate_kwargs={"temperature": 0.9}
)

formatter = GeminiChatFormatter()

workflow = DiscoveryWorkflow(
    model=reasoning_model,  # or creative_model
    formatter=formatter,
    max_loops=5,           # More exploration loops
    token_budget=20000,    # Higher token budget
    time_budget=3600.0,    # 1 hour time limit
    cost_budget=10.0,      # $10 cost limit
)
```

### Using Different Models

```python
# Use different Gemini models for different capabilities
from agentscope.model import GeminiChatModel

# For reasoning-heavy tasks (uses more advanced Gemini model)
reasoning_model = GeminiChatModel(
    model_name="gemini-2.5-pro",
    generate_kwargs={"temperature": 0.3}
)

# For creative insight generation  
creative_model = GeminiChatModel(
    model_name="gemini-2.5-flash-lite", 
    generate_kwargs={"temperature": 0.9}
)
```

### Custom Knowledge Base

Replace the example knowledge base with your own documents:

```python
results = await workflow.run_full_discovery(
    knowledge_base_path="/path/to/your/documents",
    initial_idea="Your research question or idea",
    focus_areas=["your", "focus", "areas"],
    exploration_depth="deep",  # shallow, normal, or deep
)
```

## Architecture Notes

This example demonstrates a complete integration between:

### Frontend (Web Interface)
- **Modern HTML5/CSS3/JavaScript** with Tailwind CSS styling
- **Real-time WebSocket communication** for live progress updates
- **Drag-and-drop file upload** with MD file validation
- **Responsive design** that works on desktop and mobile
- **Interactive configuration** for exploration parameters

### Backend (Python Server)
- **FastAPI server** with async/await support
- **AgentScope Discovery System** integration
- **Gemini API** for powerful language model capabilities
- **Session management** with unique discovery sessions
- **File processing** for MD knowledge base creation

### Integration Benefits
- **No direct API calls from browser** - secure server-side processing
- **Real-time progress tracking** via WebSocket streaming
- **Structured results display** with rich formatting
- **Error handling and validation** at multiple levels
- **Scalable architecture** supporting multiple concurrent users

In a production implementation, you would have:

- Complete implementations of all specialized agents
- Real external search integration
- Advanced graph algorithms for knowledge discovery
- Sophisticated surprise assessment using information theory
- Integration with research databases and APIs
- Persistent session management across multiple runs
- User authentication and session persistence
- Cloud storage for knowledge bases

## Related Examples

- `react_agent/` - Basic ReAct agent patterns used in the discovery system
- `functionality/long_term_memory/` - Memory systems for knowledge persistence
- `multiagent_conversation/` - Multi-agent coordination patterns

## References

- [Agent Discovery System Design Document](../../src/agentscope/discovery/README.md)
- [AgentScope Documentation](https://agentscope-ai.github.io/agentscope/)
- [Bayesian Surprise in Information Theory](https://en.wikipedia.org/wiki/Bayesian_surprise)