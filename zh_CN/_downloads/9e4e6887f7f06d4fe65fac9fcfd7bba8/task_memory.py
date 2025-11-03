# -*- coding: utf-8 -*-
"""
.. _memory:

记忆
========================

在 AgentScope 中，记忆（memory）用于存储智能体的上下文，并在需要时检索它。
具体而言，AgentScope 在 ``agentscope.memory`` 模块下提供了记忆基类 ``MemoryBase`` 和一个可直接使用的基于内存实现 ``InMemoryMemory``。

自定义记忆
~~~~~~~~~~~~~~~~~~~~~~~~

要自定义您自己的记忆，只需继承 ``MemoryBase`` 并实现以下方法：

.. list-table::
    :header-rows: 1

    * - 方法
      - 描述
    * - ``add``
      - 向记忆中添加 ``Msg`` 对象
    * - ``delete``
      - 从记忆中删除项目
    * - ``size``
      - 记忆的大小
    * - ``clear``
      - 清空记忆内容
    * - ``get_memory``
      - 以 ``Msg`` 对象列表的形式获取记忆内容
    * - ``state_dict``
      - 获取记忆的状态字典
    * - ``load_state_dict``
      - 加载记忆的状态字典

进一步阅读
~~~~~~~~~~~~~~~~~~~~~~~~

- :ref:`long-term-memory`
"""
