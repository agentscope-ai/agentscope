# -*- coding: utf-8 -*-
"""
.. _installation:

安装
============================

AgentScope 需要 Python 3.10 或更高版本。您可以从源代码或 PyPI 安装。

从 PyPI 安装
----------------
.. code-block:: bash

    pip install agentscope

从源代码安装
----------------
从源代码安装 AgentScope，需要从 GitHub 克隆仓库，并通过以下命令安装

.. code-block:: bash

    git clone -b main https://github.com/agentscope-ai/agentscope
    cd agentscope
    pip install -e .

执行以下代码确保 AgentScope 正常安装：
"""

import agentscope

print(agentscope.__version__)

# %%
# 额外依赖
# ----------------------------
#
# 为了满足不同功能的需求，AgentScope 提供了额外依赖项。
#
# - full: 包含模型 API 和工具函数的额外依赖项
# - dev: 开发依赖项，包括测试和文档工具
#
# 以 full 模式为例，安装命令根据您的操作系统而有所不同。
#
# 对于 Windows 用户：
#
# .. code-block:: bash
#
#       pip install agentscope[full]
#
# 对于 Mac 和 Linux 用户：
#
# .. code-block:: bash
#
#       pip install agentscope\[full\]
