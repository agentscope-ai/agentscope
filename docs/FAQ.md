# AgentScope FAQ

---

## AgentScope Core Framework

### Q1: What are the differences between AgentScope 2.0 and AgentScope 1.0?

**A**: AgentScope 1.0 emphasizes "transparent development", enabling developers to clearly understand the agent's message flow and tool invocation process. AgentScope 2.0 extends this philosophy while focusing on **enabling agents to reliably complete tasks end-to-end**. Key upgrades include:

*   **Model-layer fault tolerance**: Unified retry and fallback model mechanisms for improved stability in long-chain tasks.
*   **Message and event system redesign**: Content Blocks and streaming events make the execution process observable, interactive, and interruptible.
*   **Permission system**: Fine-grained control over tool calls, file access, and command execution boundaries.
*   **Context management**: Structured compression and tool result truncation to support long-running tasks.
*   **Middleware mechanism**: Insert custom logic at key execution stages for flexible extension without modifying framework internals.
*   **Workspace**: Decouples execution environments (local/container/cloud sandbox) from agent logic behind a unified, swappable interface.
*   **Agent Service**: Merges AgentScope-Runtime capabilities into the core framework, enabling agents to be deployed as services with streaming interfaces, session recovery, and background task management.

In short, 1.0 focuses on "how to build agents", while 2.0 further addresses "how to make agents run reliably".

---

### Q2: Does AgentScope's Workstation still exist?

**A**: No. Since **AgentScope 1.0**, the project has fully transitioned to a **code-first development approach** and no longer maintains the earlier drag-and-drop Workstation interface. **It is not recommended to use Workstation in new projects**.

---

### Q3: Why do different LLMs require different Formatters?

**A**: Different LLM providers (e.g., OpenAI, Anthropic, DashScope) have different requirements for input message formats (such as role fields and tool call structures). AgentScope uses **Formatters to decouple model differences**, ensuring generated messages conform to each API's specifications. Even models that claim OpenAI compatibility may require separate adaptation due to version lag.

---

### Q4: Does the ReAct agent support dynamic Pydantic models for structured output?

**A**: Yes! AgentScope implements **dynamic JSON Schema generation** with Pydantic validation. See the documentation: [Structured Output](https://doc.agentscope.io/tutorial/task_agent.html#structured-output).

---

### Q5: Does AgentScope support the MCP (Model Control Protocol)?

**A**: Yes. AgentScope is compatible with the standard MCP protocol for integrating external tools and services. Tutorial reference: [MCP User Guide](https://doc.agentscope.io/zh_CN/tutorial/task_mcp.html).

---

### Q6: Are there community-developed AgentScope applications beyond the official examples?

**A**: Yes! Check out the [agentscope-samples](https://github.com/agentscope-ai/agentscope-samples) repository, which contains community-contributed examples including Werewolf, debates, intelligent customer service, and more.

---

### Q7: What is the difference between model fine-tuning and memory retrieval?

**A**:

*   **Model fine-tuning**: Modifies model parameters through training to improve performance on specific tasks.

*   **Memory retrieval**: Injects relevant context during inference via vector databases or similar methods. The two are **not mutually exclusive and can even be combined** — for example, fine-tuning a model to better leverage retrieved memory information.


---

### Q8: What is the relationship between AgentScope-Java and Spring AI Alibaba?

**A**:

*   **AgentScope-Java** is the Java implementation of AgentScope, currently under development, aiming to maintain consistency with the Python version in philosophy, design, and functionality.

*   **Spring AI Alibaba** has **switched its underlying layer to AgentScope-Java**, focusing on Spring ecosystem integration. If you use Spring AI Alibaba's Agentic API, future upgrades will automatically provide AgentScope capabilities — **no need to separately introduce AgentScope-Java**.


---

### Q9: Can AgentScope be used with AI coding assistants like Cursor or Claude Code?

**A**: Absolutely! AgentScope has a clean code structure and comprehensive documentation, making it well-suited for use with AI coding assistants. It is recommended to provide AgentScope source code or tutorials (located in `docs/tutorial/en/src/`) as context to the AI for better results.

---
> 📚 Official Documentation: [AgentScope](https://doc.agentscope.io) | [AgentScope-Runtime](https://runtime.agentscope.io/v1.0.5/en/intro.html)   💬 Questions? Join our community!

| [**Discord**](https://discord.gg/eYMpfnkG8h) | **DingTalk** |
| --- | --- |
| [![image](https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/res/mPdnpEbk9rGLgqw9/img/aae46e38-9875-4867-9de0-01ccbefcd610.png)](https://camo.githubusercontent.com/e9a88e8b54eaf26cda4abe522968eac3ed9bb8a1fe2f97d83fb3ca21ed64cd26/68747470733a2f2f67772e616c6963646e2e636f6d2f696d6765787472612f69312f4f31434e3031686844316d75314464334257565576784e5f2121363030303030303030303233382d322d7470732d3430302d3430302e706e67) | [![image](https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/res/mPdnpEbk9rGLgqw9/img/57e53bf1-c52b-457e-9a21-b295a49136a9.png)](https://github.com/agentscope-ai/agentscope/blob/main/assets/images/dingtalk_qr_code.png) |
