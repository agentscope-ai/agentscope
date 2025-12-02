# 常见问题解答

## 关于 AgentScope
_**Q**：AgentScope 与其他代理平台/框架有什么区别？_
<br/>
**A**：AgentScope 是一个面向开发者的多智能体平台，旨在简化**多智能体应用程序**的开发、部署和监控。

## 关于模型

_**Q**：如何在 AgentScope 中集成/使用新的模型 API？_
<br/>
**A**：请参考 [集成新的 LLM API](integrating_new_api) 。

_**Q**：AgentScope 支持哪些 LLM？_
<br/>
**A**：AgentScope 支持大多数现有的 LLM API，包括 OpenAI、Claude、Gemini、DashScope 等。支持列表请参考 [模型 API](model_api) 。

_**Q**：如何在 AgentScope 中监控 token 使用情况？_
<br/>
**A**：详情请参考 [监控 Token 使用情况](token_usage)。

## 关于工具

_**Q**：AgentScope 提供了哪些工具？_
<br/>
**A**：请参考 [工具](tools)。

_**Q**：如何在 AgentScope 中使用这些工具？_
<br/>
**A**：AgentScope 提供了 `ServiceToolkit` 模块用于工具使用。详细用法请参考 [工具](tools)。

## 关于智能体

_**Q**：如何在 AgentScope 中使用智能体？_
<br/>
**A**：您可以使用 AgentScope 中内置的智能体，或开发自己的智能体。详情请参考 [内置智能体](builtin_agent) 一节。

_**Q** 如何将智能体的（流式）输出转发/到自己的前端或应用程序？_
<br/>
**A** 可以的！可以使用智能体中 `Speak` 函数的钩子来实现，详细请参考 [钩子函数](hook) 一节。

## 关于 GUI

_**Q**：AgentScope 提供了哪些 GUI？_
<br/>
**A**：AgentScope 支持在 Gradio 中运行您的应用程序，并且还提供了一个名为 AgentScope Studio 的 GUI，供您监控和管理应用程序。

## 关于低代码开发

_**Q**：AgentScope 中的低代码开发是什么？_
<br/>
**A**：它意味着您可以通过拖拽组件来开发应用程序。详情请参考 [低代码开发](low_code)。

## 关于报告漏洞

_**Q**：我如何报告 AgentScope 中的漏洞？_
<br/>
**A**：如果您在使用 AgentScope 时遇到漏洞，请通过在我们的 GitHub 仓库中创建问题来报告。

_**Q**：我如何报告 AgentScope 中的安全漏洞？_
<br/>
**A**：如果您发现 AgentScope 中的安全问题，请通过[阿里巴巴安全响应中心 (ASRC)](https://security.alibaba.com/)报告给我们。
