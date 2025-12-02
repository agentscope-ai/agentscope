# -*- coding: utf-8 -*-
"""
.. _low_code:

低代码开发
===========================

.. important:: 新的 AgentScope 低代码开发平台即将上线，从 v0.1.5 版本开始，现有的 workstation 代码会从主库中剥离，以下功能将处于不可用状态。

本教程介绍如何在AgentScope Workstation中通过拖拽界面构建多智能体应用程序。

Workstation
------------------

Workstation现已集成在 :ref:`agentscope-studio` 中。
它为零代码用户提供了一种更简单的方式来构建多智能体应用程序。

.. note:: Workstation 正处于快速迭代开发中，将会频繁更新。

启动 Workstation
---------------------

首先确保您已安装最新版本的 AgentScope。

执行以下Python代码来启动 AgentScope Studio：

.. code-block:: python

    import agentscope
    agentscope.studio.init()

或在终端中运行以下 bash 命令：

.. code-block:: bash

    as_studio

然后访问 `https://127.0.0.1:5000` 进入 AgentScope Studio，并点击侧边栏中的 Workstation 图标进入。


* **中央工作区**：您可以在这个主要区域拖拽组件来构建应用程序。

* **顶部工具箱**：用于导入、导出、检查和运行您的应用程序。

.. image:: https://img.alicdn.com/imgextra/i1/O1CN01RXAVVn1zUtjXVvuqS_!!6000000006718-1-tps-3116-1852.gif

内置示例
^^^^^^^^^^^^^^^^^^^^^^^^^^^

对于初学者，我们强烈建议从预构建的示例开始。您可以直接单击示例将其导入到中央工作区。或者，为了获得更加结构化的学习体验，您也可以选择跟随每个示例链接的教程。这些教程将一步步指导如何在 AgentScope Workstation 上构建多智能体应用。

构建应用
^^^^^^^^^^^^^^^^^^^^^^^^^^^

要构建应用程序，请执行以下步骤：

* 选择并拖拽组件：从侧边栏中单击并拖拽所选组件到中央工作区。

* 连接节点：大多数节点都有输入和输出点。单击一个组件的输出点并拖拽到另一个组件的输入点，以创建消息流管道。这样不同的节点就可以传递消息。

* 配置节点：将节点放入工作区后，单击任意一个节点来填写其配置设置。您可以自定义提示、参数和其他属性。

运行应用
^^^^^^^^^^^^^^^^^^^^^^^^^^^

构建完应用程序后，单击"运行"按钮。在运行之前，Workstation会检查应用程序中是否有任何错误。如果有错误，系统会提示您在继续之前进行修正。之后，您的应用程序将在与AgentScope Studio相同的Python环境中执行，您可以在Dashboard中找到它。

导入/导出应用
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Workstation支持导入和导出您的应用程序。单击"导出HTML"或"导出Python"按钮可生成代码，您可以将其分发给社区或本地保存。如果要将导出的代码转换为Python代码，可以使用以下命令将JSON配置编译为Python代码：

.. code-block:: bash

    # 编译
    as_workflow config.json --compile ${YOUR_PYTHON_SCRIPT_NAME}.py

如果您想直接运行本地配置，可以使用以下命令：

.. code-block:: bash

    # 运行
    as_gradio config.json


想要进一步编辑您的应用程序吗？只需单击"导入HTML"按钮，将以前导出的HTML代码重新上传到AgentScope Workstation即可。

检查应用
^^^^^^^^^^^^^^^^^^^^^^^^^

构建应用程序后，您可以单击"检查"按钮来验证应用程序结构的正确性。将执行以下检查规则：

* 模型和智能体存在检查：每个应用程序必须至少包含一个模型节点和一个智能体节点。

* 单连接策略：每个组件的每个输入不应该有多于一个连接。

* 必填字段验证：所有必填输入字段都必须填写，以确保每个节点都有正确运行所需的参数。

* 配置命名一致性：智能体节点使用的"模型配置名称"必须与模型节点中定义的"配置名称"相对应。

* 适当的节点嵌套：像ReActAgent这样的节点应该只包含工具节点。同样，像IfElsePipeline这样的管道节点应该包含正确数量的元素（不超过2个），而ForLoopPipeline、WhileLoopPipeline和MsgHub应该遵守只有一个元素的规则（必须是SequentialPipeline作为子节点）。

"""
