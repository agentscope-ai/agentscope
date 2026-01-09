# A2UI in AgentScope

[A2UI (Agent-to-Agent UI)](https://github.com/google/A2UI) is a protocol for agents to send
streaming, interactive user interfaces to clients. It enables LLMs to generate platform-agnostic,
declarative UI definitions that clients can render progressively using native widget sets.

In this example, we demonstrate how to integrate A2UI into a ReAct agent in AgentScope. This
implementation is based on the official A2UI agent samples, adapted to use AgentScope's agent
framework.

Specifically, we have:

1. **Reimplemented the agent with AgentScope**: The agent part of the official A2UI samples has
   been reimplemented using AgentScope's `ReActAgent`, providing a more familiar and integrated
   development experience for AgentScope users.

2. **Progressive schema and template exposure via skills**: To help the agent learn and generate
   A2UI-compliant interfaces, we use AgentScope's skill system to progressively expose the A2UI
   schema and UI templates. The agent can dynamically load these resources through the
   `A2UI_response_generator` skill, enabling it to understand component definitions and learn from
   example UI structures.

## Note on External Dependencies

The following directories in this example contain content sourced from the [Google A2UI repository](https://github.com/google/A2UI):

- **`specification/`**: A2UI protocol specification files
- **`renderers/`**: A2UI renderer implementations (Lit and Angular)
- **`samples/client/`**: A2UI client sample applications

**NPM Package Status**: As of now, the A2UI client libraries (`@a2ui/lit` and `@a2ui/angular`) are **not yet published to NPM**. According to the [official A2UI client setup guide](https://a2ui.org/guides/client-setup/#renderers): "The Lit client library is not yet published to NPM. Check back in the coming days."

Therefore, these dependencies are currently included in this example repository using local file paths (e.g., `"@a2ui/lit": "file:../../../../renderers/lit"` in `package.json` files). This mirrors the approach used in the [official A2UI repository](https://github.com/google/A2UI), where the renderers and samples also use local file paths to reference each other. Additionally, the `copy-spec` task in `renderers/lit/package.json` copies files from the local `specification/` directory during the build process.

**Future Plans**: Once those libraries are published to NPM, we plan to gradually migrate to using the official NPM packages and remove these locally included directories.

## Quick Start

Install the latest version of AgentScope to run this example:

```bash
pip install agentscope --upgrade
```

Then, navigate to the client directory and run the restaurant finder demo:

```bash
cd examples/agent/a2ui_agent/samples/client/lit
npm run demo:restaurant
```

This command will:
- Install dependencies and build the A2UI renderer
- Start the A2A server (AgentScope agent) for the restaurant finder
- Launch the client application in your browser

> Note:
> - The example is built with DashScope chat model. Make sure to set your `DASHSCOPE_API_KEY`
>   environment variable before running the demo.
> - If you are using Qwen series models, we recommend using `qwen-max` for better performance in
>   generating A2UI-compliant JSON responses.
> - Generating UI JSON responses may take some time, typically 1-2 minutes, as the agent needs to
>   process the schema, examples, and generate complex UI structures.
> - The demo uses the standard A2UI catalog. Custom catalog and inline catalog support are under
>   development.

## Roadmap

AgentScope's main focus going forward will be on improving **How Agents Work** with A2UI. The
workflow we're working towards is:

```
User Input → Agent Logic → LLM → A2UI JSON
```

Our optimization efforts will focus on:

- **Agent Logic**: Improving how agents process inputs and orchestrate the generation of A2UI JSON
  messages


- **Handle user interactions from the client**: Enabling agents to properly process and respond to
  user interactions from the client (such as button clicks, form submissions), treating them as new
  user input to create a continuous interactive loop

**Current approach**: The skill-based method we've implemented in this example is our first step
towards this goal. By using AgentScope's skill system to progressively expose the A2UI schema and
templates, agents can learn to generate compliant UI structures. Future improvements will focus on
streamlining this process and making it more intuitive for developers to build A2UI-capable agents.

**Next steps for Agent Logic improvement**

- **Agent skills improvements**:
  - Support flexible schema addition: Enable developers to easily add and customize schemas without
    modifying core skill code
  - Separate schema and examples into dedicated folders: Organize schema definitions and example
    templates into distinct directories for better maintainability and clearer structure

- **Context management in Memory for A2UI long context**:
  - Currently, A2UI messages are extremely long, which makes multi-turn interactions inefficient
    and degrades the quality of agent responses. We plan to implement better context management
    strategies to handle these long-form messages and improve the quality of multi-turn conversations.

- **Keep up with A2UI protocol updates**:
  - We will follow A2UI protocol updates and make corresponding adjustments. For example, we plan to
    support streaming UI JSON introduced in A2UI v0.9.

