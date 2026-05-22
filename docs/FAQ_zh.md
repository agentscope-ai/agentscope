# AgentScope 常见问题 (FAQ)

---

## AgentScope 核心框架

### Q1：AgentScope 2.0 和 AgentScope 1.0 有什么区别？

**A**：AgentScope 1.0 强调"透明开发"，让开发者清晰理解智能体的消息流转与工具调用过程。AgentScope 2.0 在延续透明理念的基础上，聚焦**让智能体稳定把事做完**，主要升级包括：

*   **模型层容错**：统一的重试与备用模型机制，提升长链路任务的稳定性。
*   **消息与事件系统重构**：通过 Content Block 和流式事件，让执行过程可展示、可交互、可干预。
*   **权限系统**：细粒度控制工具调用、文件读写和命令执行的安全边界。
*   **上下文管理**：结构化压缩与工具结果截断，支撑长期任务执行。
*   **Middleware 机制**：在关键执行环节插入自定义逻辑，灵活扩展而不修改框架内部。
*   **Workspace**：将执行环境（本地/容器/云沙箱）与 Agent 逻辑解耦，统一接口可替换。
*   **Agent Service**：将 AgentScope-Runtime 的能力合并进核心框架，智能体可作为可部署服务对外提供流式接口，支持 Session 恢复和后台任务管理。

简言之，1.0 关注"如何构建智能体"，2.0 进一步关注"如何让智能体可靠运行"。

---

### Q2：AgentScope 的 Workstation（工作站）还存在吗？

**A**：不存在了。从 **AgentScope 1.0 起**，项目已全面转向 **代码优先（code-first）的开发模式**，不再维护早期的拖拽式 Workstation 界面。**不建议在新项目中使用 Workstation**。

---

### Q3：为什么不同大模型需要不同的 Formatter？

**A**：不同大模型厂商（如 OpenAI、Anthropic、通义千问等）对输入消息格式（如 role 字段、工具调用结构）有不同要求。AgentScope 通过 **Formatter 解耦模型差异**，确保生成的消息符合各 API 规范。即使某些模型声称兼容 OpenAI，也可能因版本滞后需单独适配。

---

### Q4：ReAct 智能体是否支持动态 Pydantic 模型进行结构化输出？

**A**：支持！AgentScope 已实现 **动态 JSON Schema 生成**，并利用 Pydantic 进行校验。详见文档：[结构化输出](https://doc.agentscope.io/tutorial/task_agent.html#structured-output)。

---

### Q5：AgentScope 是否支持 MCP（Model Control Protocol）协议？

**A**：支持。AgentScope 兼容标准 MCP 协议，可用于对接外部工具或服务。教程参考：[MCP 使用指南](https://doc.agentscope.io/zh_CN/tutorial/task_mcp.html)。

---

### Q6：除了官方示例，是否有社区开发的 AgentScope 应用？

**A**：有！可查看 [agentscope-samples](https://github.com/agentscope-ai/agentscope-samples) 仓库，其中包含社区贡献的狼人杀、辩论、智能客服等多种场景示例。

---

### Q7：模型微调和记忆召回有什么区别？

**A**：

*   **模型微调**：通过训练修改模型参数，提升特定任务能力。

*   **记忆召回**：在推理时通过向量数据库等方式注入相关上下文。 两者**不冲突，甚至可结合使用**——例如微调一个模型使其更擅长利用召回的记忆信息。


---

### Q8：AgentScope-Java 和 Spring AI Alibaba 是什么关系？

**A**：

*   **AgentScope-Java** 是 AgentScope 的 Java 实现，目前仍在建设中，目标是与 Python 版在理念、设计和功能上保持一致。

*   **Spring AI Alibaba** 将**底层切换为 AgentScope-Java**，专注 Spring 生态集成。如果你使用 Spring AI Alibaba 的 Agentic API，后续升级即可自动获得 AgentScope 能力，**无需单独引入 AgentScope-Java**。


---

### Q9：能否配合 Cursor、Claude Code 等 AI 编程助手使用 AgentScope？

**A**：完全可以！AgentScope 代码结构清晰、文档完善，非常适合与 AI 编程助手配合。建议将 AgentScope 源码或教程（位于 `docs/tutorial/en/src/`）作为上下文提供给 AI，效果更佳。

---
> 📚 官方文档：[AgentScope](https://doc.agentscope.io)｜[AgentScope-Runtime](https://runtime.agentscope.io/v1.0.5/en/intro.html)   💬 遇到问题？欢迎加入官方交流群！

| [**Discord**](https://discord.gg/eYMpfnkG8h) | **DingTalk** |
| --- | --- |
| [![image](https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/res/mPdnpEbk9rGLgqw9/img/aae46e38-9875-4867-9de0-01ccbefcd610.png)](https://camo.githubusercontent.com/e9a88e8b54eaf26cda4abe522968eac3ed9bb8a1fe2f97d83fb3ca21ed64cd26/68747470733a2f2f67772e616c6963646e2e636f6d2f696d6765787472612f69312f4f31434e3031686844316d75314464334257565576784e5f2121363030303030303030303233382d322d7470732d3430302d3430302e706e67) | [![image](https://alidocs.oss-cn-zhangjiakou.aliyuncs.com/res/mPdnpEbk9rGLgqw9/img/57e53bf1-c52b-457e-9a21-b295a49136a9.png)](https://github.com/agentscope-ai/agentscope/blob/main/assets/images/dingtalk_qr_code.png) |
