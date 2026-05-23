# AgentScope 常见问题 (FAQ)

---

## AgentScope 核心框架

### Q1：AgentScope 的 Workstation（工作站）还存在吗？

**A**：不存在了。从 **AgentScope 1.0 起**，项目已全面转向 **代码优先（code-first）的开发模式**，不再维护早期的拖拽式 Workstation 界面。**不建议在新项目中使用 Workstation**。

---

### Q2：为什么不同大模型需要不同的 Formatter？

**A**：不同大模型厂商（如 OpenAI、Anthropic、通义千问等）对输入消息格式（如 role 字段、工具调用结构）有不同要求。AgentScope 通过 **Formatter 解耦模型差异**，确保生成的消息符合各 API 规范。即使某些模型声称兼容 OpenAI，也可能因版本滞后需单独适配。

---

### Q3：AgentScope 是否支持 MCP（Model Control Protocol）协议？

**A**：支持。AgentScope 兼容标准 MCP 协议，可用于对接外部工具或服务。教程参考：[MCP 使用指南](https://docs.agentscope.io)。

---

### Q4：除了官方示例，是否有社区开发的 AgentScope 应用？

**A**：有！可查看 [agentscope-samples](https://github.com/agentscope-ai/agentscope-samples) 仓库，其中包含社区贡献的狼人杀、辩论、智能客服等多种场景示例。

---

### Q5：模型微调和记忆召回有什么区别？

**A**：

*   **模型微调**：通过训练修改模型参数，提升特定任务能力。

*   **记忆召回**：在推理时通过向量数据库等方式注入相关上下文。 两者**不冲突，甚至可结合使用**——例如微调一个模型使其更擅长利用召回的记忆信息。


---

### Q6：AgentScope-Java 和 Spring AI Alibaba 是什么关系？

**A**：

*   **AgentScope-Java** 是 AgentScope 的 Java 实现，目前仍在建设中，目标是与 Python 版在理念、设计和功能上保持一致。

*   **Spring AI Alibaba** 将**底层切换为 AgentScope-Java**，专注 Spring 生态集成。如果你使用 Spring AI Alibaba 的 Agentic API，后续升级即可自动获得 AgentScope 能力，**无需单独引入 AgentScope-Java**。


---

### Q7：能否配合 Cursor、Claude Code 等 AI 编程助手使用 AgentScope？

**A**：完全可以！AgentScope 代码结构清晰、文档完善，非常适合与 AI 编程助手配合。建议将 AgentScope 源码或教程作为上下文提供给 AI，效果更佳。

