# Roadmap

## Long-term Goals

Offering **agent-oriented programming (AOP)** as a new programming paradigm to organize the design and implementation of next-generation LLM-empowered applications.

## Current Focus (January 2026 - )

### 🎙️ Voice Agent

**Voice agents** are a domain we are highly focused on, and AgentScope will continue to invest in this direction.

AgentScope aims to build **production-ready** voice agents rather than demonstration prototypes. This means our voice agents will:

- Support **production-grade** deployment, including seamless frontend integration
- Support **tool invocation**, not just voice conversations
- Support **multi-agent** voice interactions

#### Development Roadmap

Our development strategy for voice agents consists of **three progressive milestones**:

1. **TTS Models** → 2. **Multimodal Models** → 3. **Real-time Multimodal Models**

---

#### Phase 1: TTS (Text-to-Speech) Models

- **Build TTS model base class infrastructure**
  - Design and implement a unified TTS model base class
  - Establish standardized interfaces for TTS model integration

- **Horizontal API expansion**
  - Support mainstream TTS APIs (e.g., OpenAI TTS, Google TTS, Azure TTS, ElevenLabs, etc.)
  - Ensure consistent behavior across different TTS providers

---

#### Phase 2: Multimodal Models (Non-Realtime)

- **Enable ReAct agents with multimodal support**
  - Integrate multimodal models (e.g., qwen3-omni, gpt-audio) into existing ReAct agent framework
  - Support audio input/output in non-realtime mode

- **Advanced multimodal agent capabilities**
  - Enable tool invocation within multimodal conversations
  - Support multi-agent workflows with multimodal communication

---

#### Phase 3: Real-time Multimodal Models


- **Beyond request-response**: Explore streaming, interrupt handling, and concurrent multimodal processing
- **New programming paradigms**: Design agent programming models specifically tailored for real-time interactions
- **Production readiness**: Ensure low-latency performance, stability, and scalability for production deployment

### 🛠️ Agent Skill

Provide **production-ready** agent skill integration solutions.

### 🌐 Ecosystem Expansion

- **A2UI (Agent-to-UI)**: Enable seamless agent-to-user interface interactions
- **A2A (Agent-to-Agent)**: Enhance agent-to-agent communication capabilities

### 🚀 Agentic RL

- Support using [Tinker](https://tinker-docs.thinkingmachines.ai/) backend to tune agent applications on devices without GPU.
- Support tuning agent applications based on their run history.
- Integrate with AgentScope Runtime to provide better environment abstraction.
- Add more tutorials and examples on how to build complex judge functions with the help of evaluation module.
- Add more tutorials and examples on data selection and augmentation.

### 📈 Code Quality

Continuous refinement and improvement of code quality and maintainability.

# Completed Milestones

### AgentScope V1.0.0 Roadmap

We are deeply grateful for the continuous support from the open-source community that has witnessed AgentScope's
growth. Throughout our journey, we have maintained **developer-centric transparency** as our core principle,
which will continue to guide our future development.

As the AI agent ecosystem rapidly evolves, we recognize the need to adapt AgentScope to meet emerging trends and
requirements. We are excited to announce the upcoming release of AgentScope v1.0.0, which marks a significant shift
towards deployment-focused and secondary development direction. This new version will provide comprehensive support for agent developers
with enhanced deployment capabilities and practical features. Specifically, the update will include:

- ✨New Features
  - 🛠️ Tool/MCP
    - Support both sync/async tool functions
    - Support streaming tool function
    - Support parallel execution of tool functions
    - Provide more flexible support for the MCP server

  - 💾 Memory
    - Enhance the existing short-term memory
    - Support long-term memory

  - 🤖 Agent
    - Provide powerful ReAct-based out-of-the-box agents

- 👨‍💻 Development
  - Provide enhanced AgentScope Studio with visual components for developing, tracing and debugging
  - Provide a built-in copilot for developing/drafting AgentScope applications

- 🔍 Evaluation
  - Provide built-in benchmarking and evaluation toolkit for agents
  - Support result visualization

- 🏗️ Deployment
  - Support asynchronous agent execution
  - Support session/state management
  - Provide sandbox for tool execution

Stay tuned for our detailed release notes and beta version, which will be available soon. Follow our GitHub
repository and official channels for the latest updates. We look forward to your valuable feedback and continued
support in shaping the future of AgentScope.