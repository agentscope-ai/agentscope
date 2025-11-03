# -*- coding: utf-8 -*-
"""
.. _faq:

FAQ
========================================

About AgentScope
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*What is AgentScope?*
    AgentScope is a multi-agent framework, aiming to provide a simple yet efficient way to build LLM-empowered agent applications.

*What is the difference between AgentScope v1.0 and v0.x?*
    AgentScope v1.0 is a complete refactoring of the framework, equipped with new features and improvements. Refer to for detailed changes.


About Model
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*How to integrate my own model with AgentScope?*
    Create your own model by inheriting ``agentscope.model.ChatModelBase`` and implement the ``__call__`` method.

*What models are supported by AgentScope?*
    Currently, AgentScope has built-in support for DashScope, Gemini, OpenAI, Anthropic, and Ollama APIs, and the ``OpenAIChatModel`` compatible with DeepSeek and vLLMs models.

*How to monitor the token usage in AgentScope?*
    In AgentScope Studio, we provide visualization of token usage and tracing. Refer :ref:`studio` section for more details.


About Agent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*How to create my own agent?*
    You can choose to use the ``ReActAgent`` class directly, or create your own agent by inheriting from ``AgentBase`` or ``ReActAgentBase`` classes. Refer to the :ref:`agent` section for more details.


*How to forward the (streaming) output of agents to my own frontend or application?*
    Use the pre hook of the ``print`` function to forward printing messages. Refer to the :ref:`hook` section.


About Tools
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*How many tools are provided by AgentScope?*
    AgentScope provides a set of built-in tools, including ``execute_python_code``, ``execute_shell_command``, ``write_text_file`` , etc. You can find them under ``agentscope.tool`` module.


About Reporting Bugs
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
*How can I report a bug in AgentScope?*
    If you encounter a bug while using AgentScope, please report it by opening an issue on our GitHub repository.

*How can I report a security bug in AgentScope?*
    If you discover a security issue in AgentScope, please report it to us through the `Alibaba Security Response Center (ASRC) <https://security.alibaba.com/>`_.

"""
