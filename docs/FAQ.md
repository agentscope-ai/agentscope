# AgentScope FAQ

---

## AgentScope Core Framework

### Q1: Does AgentScope's Workstation still exist?

**A**: No. Since **AgentScope 1.0**, the project has fully transitioned to a **code-first development approach** and no longer maintains the earlier drag-and-drop Workstation interface. **It is not recommended to use Workstation in new projects**.

---

### Q2: Why do different LLMs require different Formatters?

**A**: Different LLM providers (e.g., OpenAI, Anthropic, DashScope) have different requirements for input message formats (such as role fields and tool call structures). AgentScope uses **Formatters to decouple model differences**, ensuring generated messages conform to each API's specifications. Even models that claim OpenAI compatibility may require separate adaptation due to version lag.

---

### Q3: Does the ReAct agent support dynamic Pydantic models for structured output?

**A**: Yes! AgentScope implements **dynamic JSON Schema generation** with Pydantic validation. See the documentation: [Structured Output](https://doc.agentscope.io/tutorial/task_agent.html#structured-output).

---

### Q4: Does AgentScope support the MCP (Model Control Protocol)?

**A**: Yes. AgentScope is compatible with the standard MCP protocol for integrating external tools and services. Tutorial reference: [MCP User Guide](https://doc.agentscope.io/zh_CN/tutorial/task_mcp.html).

---

### Q5: Are there community-developed AgentScope applications beyond the official examples?

**A**: Yes! Check out the [agentscope-samples](https://github.com/agentscope-ai/agentscope-samples) repository, which contains community-contributed examples including Werewolf, debates, intelligent customer service, and more.

---

### Q6: What is the difference between model fine-tuning and memory retrieval?

**A**:

*   **Model fine-tuning**: Modifies model parameters through training to improve performance on specific tasks.

*   **Memory retrieval**: Injects relevant context during inference via vector databases or similar methods. The two are **not mutually exclusive and can even be combined** — for example, fine-tuning a model to better leverage retrieved memory information.


---

### Q7: What is the relationship between AgentScope-Java and Spring AI Alibaba?

**A**:

*   **AgentScope-Java** is the Java implementation of AgentScope, currently under development, aiming to maintain consistency with the Python version in philosophy, design, and functionality.

*   **Spring AI Alibaba** has **switched its underlying layer to AgentScope-Java**, focusing on Spring ecosystem integration. If you use Spring AI Alibaba's Agentic API, future upgrades will automatically provide AgentScope capabilities — **no need to separately introduce AgentScope-Java**.


---

### Q8: Can AgentScope be used with AI coding assistants like Cursor or Claude Code?

**A**: Absolutely! AgentScope has a clean code structure and comprehensive documentation, making it well-suited for use with AI coding assistants. It is recommended to provide AgentScope source code or tutorials (located in `docs/tutorial/en/src/`) as context to the AI for better results.
