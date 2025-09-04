# -*- coding: utf-8 -*-
"""
.. key-concepts:

Key Concepts
====================================

This chapter establishes key concepts from an engineering
perspective to introduce AgentScope's design.

.. note:: The goal of introducing the key concepts in AgentScope is to claim what practical problems AgentScope addresses and how it supports developers, rather than to offer formal definitions.

State
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

In AgentScope, state management is a fundamental building block that maintains snapshots of objects' runtime data.

AgentScope separates object initialization from state management, allowing
object to be restored to different states after initialization through
``load_state_dict`` and ``state_dict`` methods.

In AgentScope, agent, memory, long-term memory and toolkit are all stateful
objects. AgentScope links the state management of these objects together by supporting nested state management.

Message
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
In AgentScope, message is the fundamental data structure,
used to

- exchange information between agents,
- display information in the user interface,
- store information in memory,
- act as a unified medium between AgentScope and different LLM APIs.

Tool
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
A tool in AgentScope refers to callable object, no matter it's a

- function,
- partial function,
- instance method,
- class method,
- static method, or
- callable instance with ``__call__`` method.

Besides, the callable object can be either

- async or sync,
- streaming or non-streaming.

So feel free to use any callable object as a tool in AgentScope.

Agent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
In AgentScope, the agent behaviors are abstracted into three core functions in
``AgentBase`` class:

- ``reply``: Handle incoming message(s) and generate a response message.
- ``observe``: Receive message(s) from the environment or other agents without returning a response.
- ``print``: Display message(s) to the target terminal, web interface, etc.

To support realtime steering, an additional ``handle_interrupt`` function is
provided to handle user interrupts during the agent's reply process.

Additionally, ReAct agent is the most important agent in AgentScope, where
the agent's reply process is divided into two stages:

- reasoning: thinking and generating tool calls by calling the LLM
- acting: execute the tool functions.

Thus, we provide two additional core functions in ``ReActAgentBase`` class,
``_reasoning`` and ``_acting``.

Formatter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Formatter is the core component for LLM compatibility in AgentScope,
responsible for converting message objects into the required format for
LLM APIs.

Besides, additional functionality such as prompt engineering, truncation,
and message validation can also be implemented in the formatter.

Within the formatter, the "multi-agent" (or "multi-identity") concept differs
from the common multi-agent orchestration concept.
It focuses on the scenario where multiple identities are involved in the
given messages, so that the common used ``role`` field (usually "role",
"assistant" or "system") in LLM APIs cannot distinguish them.

Therefore, AgentScope provides multi-agent formatter to handle
this scenario, usually used in games, multi-person chats, and social
simulations.

.. note:: Multi-agent workflow **!=** multi-agent in formatter.
 For example, even if the following code snippet may involve multiple
 agents (the ``tool_agent`` and the ``tool_function`` caller), the input query
 is wrapped into a **user** message, so the ``role`` field can still distinguish
 between them.

 .. code-block:: python

    async def tool_function(query: str) -> str:
        \"\"\"Tool function calling another agent\"\"\"
        msg = Msg("user", query, role="user")
        tool_agent = Agent(name="Programmer")
        return await tool_agent(msg)

 Understanding this distinction helps developers better grasp AgentScope's formatter design.


Long-Term Memory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Although providing different base classes for short- and
long-term memory, there are no strict distinctions between them in AgentScope.

In our view, everything should be **requirement-driven**. As long as your
needs are excellently met, developers can completely use just one powerful
memory system.

For ensuring the flexibility of AgentScope, we provide a two mode long-term
memory system, allowing the agent to manage (record and retrieve) the
long-term memory by its own.
"""
