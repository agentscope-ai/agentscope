# -*- coding: utf-8 -*-
"""
.. _memory:

Memory
========================

In AgentScope, the memory is used to store the context of the agent, and retrieve it when needed.
Specifically, AgentScope provides a memory base class ``MemoryBase`` and an in-memory implementation ``InMemoryMemory`` under ``agentscope.memory`` that can be used directly.

Customize Memory
~~~~~~~~~~~~~~~~~~~~~~~~

To customize your own memory, just inherit from ``MemoryBase`` and implement the following methods:

.. list-table::
    :header-rows: 1

    * - Method
      - Description
    * - ``add``
      - Add ``Msg`` objects to the memory
    * - ``delete``
      - Delete items from the memory
    * - ``size``
      - The size of the memory
    * - ``clear``
      - Clear the memory content
    * - ``get_memory``
      - Get the memory content as a list of ``Msg`` objects
    * - ``state_dict``
      - Get the state dictionary of the memory
    * - ``load_state_dict``
      - Load the state dictionary of the memory

Further Reading
~~~~~~~~~~~~~~~~~~~~~~~~

- :ref:`long-term-memory`
"""
