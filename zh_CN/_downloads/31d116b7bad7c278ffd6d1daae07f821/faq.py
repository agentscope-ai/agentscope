# -*- coding: utf-8 -*-
"""
.. _faq:

常见问题
========================================

关于 AgentScope
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*什么是AgentScope？*
    AgentScope 是一个多智能体框架，旨在提供一种简单高效的方式来构建基于大语言模型的智能体应用程序。

*AgentScope v1.0 与 v0.x 版本有什么区别？*
    AgentScope v1.0是对框架的完全重写，配备了新功能和改进。详细变更请参考相关文档。


关于模型
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*如何在 AgentScope 中集成我自己的模型？*
    通过继承 ``agentscope.model.ChatModelBase`` 并实现 ``__call__`` 方法来创建您自己的模型。

*AgentScope 支持哪些模型？*
    目前，AgentScope 内置支持 DashScope、Gemini、OpenAI、Anthropic 和 Ollama API，以及与 DeepSeek 和 vLLMs 模型兼容的 ``OpenAIChatModel``。

*如何在 AgentScope 中监控token 使用情况？*
    在 AgentScope Studio中，我们提供了 token 使用情况的可视化和追踪功能。详情请参考:ref:`studio` 部分。


关于智能体
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*如何创建我自己的智能体？*
    您可以选择直接使用 ``ReActAgent`` 类，或者通过继承 ``AgentBase`` 或 ``ReActAgentBase`` 类来创建您自己的智能体。详情请参考 :ref:`agent` 部分。


*如何将智能体的（流式）输出转发到我自己的前端或应用程序？*
    使用 ``print`` 函数的前置钩子来转发打印消息。请参考 :ref:`hook` 部分。


关于工具
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*AgentScope 提供了多少工具？*
    AgentScope 提供了一套内置工具，包括 ``execute_python_code``、``execute_shell_command``、``write_text_file`` 等。您可以在 ``agentscope.tool`` 模块下找到它们。


关于错误报告
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*如何报告 AgentScope中的错误？*
    如果您在使用 AgentScope 时遇到错误，请通过在我们的 GitHub仓库中开启一个issue来报告。

*如何报告AgentScope 中的安全漏洞？*
    如果您在AgentScope 中发现安全问题，请通过 `阿里巴巴安全响应中心(ASRC) <https://security.alibaba.com/>`_ 向我们报告。

"""
