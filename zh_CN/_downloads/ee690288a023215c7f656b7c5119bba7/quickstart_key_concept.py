# -*- coding: utf-8 -*-
"""
.. key-concepts:

核心概念
====================================

本章从工程实践的角度介绍 AgentScope 中的核心概念，从而阐明 AgentScope 的设计理念。

.. note:: 介绍核心概念的目标是为了更好的阐明 AgentScope 在工程实践中解决的问题，以及为开发者提供的帮助，而非给出严谨的定义。

状态
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

状态（State）管理是 AgentScope 框架构建的基础。其中，状态表示对象运行时某一时刻数据的快照。

AgentScope 将对象的“初始化”与“状态管理”分离，对象在初始化后通过 ``load_state_dict`` 和 ``state_dict`` 方法恢复到不同的状态，或导出当前的状态。

在 AgentScope 中，智能体（Agent）、记忆（memory）、长期记忆（Long-term memory）和工具模块（toolkit）都是有状态的对象。
AgentScope 通过支持嵌套式的状态管理，将这些对象的状态管理联系起来。

消息
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
消息（message）是 AgentScope 最核心的数据结构，用于

- 在智能体之间交换信息，
- 在用户交互界面显示信息，
- 在记忆中存储信息，
- 作为 AgentScope 与不同 LLM API 之间的统一媒介。

工具
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
AgentScope 中的“工具”指的是可调用的 Python 对象，包括

- 函数，
- 偏函数（Partial function），
- 实例方法，
- 类方法，
- 静态方法，以及
- 带有 ``__call__`` 方法的可调用实例。

此外，可调用对象可以是

- 异步或同步调用的，
- 流式或非流式返回结果的。

因此，请放心在 AgentScope 中使用任何调用对象作为智能体的工具。

智能体
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
在 AgentScope 中，智能体（Agent）行为被抽象为 ``AgentBase`` 类中的三个核心函数：

- ``reply``：处理传入的消息并生成响应消息。
- ``observe``：接收来自环境或其它智能体的消息，但不返回响应。
- ``print``：将消息显示到目标输出（例如终端、Web 界面）。

为了支持用户实时介入（Realtime Steering），AgentScope 提供了额外
的 ``handle_interrupt`` 函数来处理智能体回复过程中的用户中断。

此外，ReAct 智能体是 AgentScope 中最重要的智能体，该智能体的回复过程分为两个阶段：

- 推理（Reasoning）：通过调用 LLM 进行推理并生成工具调用
- 行动（Acting）：执行工具函数。

因此，我们在 ``ReActAgentBase`` 类中提供了两个额外的核心函数，``_reasoning`` 和 ``_acting``。

提示词格式化
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
提示词格式化器（Prompt Formatter）是 AgentScope 中保证 LLM 兼容性的核心组件，负责将消息对象转换为 LLM API 所需的格式。

此外，诸如提示工程、截断和消息验证等附加功能也可以在格式化器中实现。

在格式化器中，"多智能体"（或"多实体"）概念与常见的多智能体编排概念不同。
它专注于给定消息中包含多个身份实体的场景，因此 LLM API 中常用的 ``role`` 字段（通常取值为 "user"、"assistant" 或 "system"）无法区分它们。

因此，AgentScope 提供 MultiAgentFormatter 来处理这种场景，通常用于游戏、多人聊天和社交仿真。

.. note:: 多智能体工作流 **!=** 格式化器中的多智能体。例如，即使以下代码片段可能涉及多个
 智能体（``tool_agent`` 和 ``tool_function`` 的调用者），但是输入的 ``query`` 参数
 被包装成了 ``role`` 为 **“user”** 消息，因此 ``role`` 字段仍然可以区分它们。

 .. code-block:: python

    async def tool_function(query: str) -> str:
        \"\"\"调用另一个智能体的工具函数\"\"\"
        msg = Msg("user", query, role="user")
        tool_agent = Agent(name="Programmer")
        return await tool_agent(msg)

 理解这种区别有助于开发者了解格式化器部分中单智能体和多智能体的区别。


长期记忆
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
虽然 AgentScope 为短期记忆和长期记忆提供了不同的基类，但是 AgentScope 中并没有严格区分它们的作用。

在我们看来，一切都应该是 **需求驱动的**。只要开发者的需求得到了很好的满足，完全可以只使用一个强大的记忆系统。

这里为了确保 AgentScope 的灵活性，我们为长期记忆提供了两种运行和管理方式，开发者可以根据自己的需要进行选择。
其中“agent_control”模式允许智能体自己主动管理长期记忆，而“static_control”则是传统的由开发者管理的长期记忆
模式。
"""
