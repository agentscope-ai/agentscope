# -*- coding: utf-8 -*-
"""
.. _quickstart:

快速入门
============================

AgentScope 需要 Python 3.9 或更高版本。你可以从源码或 pypi 安装。

从 PyPI 安装
----------------
.. code-block:: bash

    pip install agentscope

从源码安装
----------------
要从源码安装 AgentScope，你需要从 GitHub 克隆仓库，然后通过以下命令安装

.. code-block:: bash

    git clone https://github.com/modelscope/agentscope
    cd agentscope
    pip install -e .

要确保 AgentScope 安装正常。可以执行以下代码:
"""

import agentscope

print(agentscope.__version__)

# %%
# 额外依赖
# ----------------------------
#
# AgentScope 提供了针对不同需求的额外依赖。你可以根据需求安装它们。
#
# - ollama: Ollama API
# - litellm: Litellm API
# - zhipuai: Zhipuai API
# - gemini: Gemini API
# - anthropic: Anthropic API
# - service: 用于不同工具函数的依赖
# - distribute: 用于分布式模式的依赖
# - full: 一次性安装所有依赖
#
# 以分布式模式为例，安装命令因操作系统而异。
#
# 对于 Windows 用户:
#
# .. code-block:: bash
#
#       pip install agentscope[gemini]
#       # 或
#       pip install agentscope[ollama,distribute]
#
# 对于 Mac 和 Linux 用户:
#
# .. code-block:: bash
#
#       pip install agentscope\[gemini\]
#       # 或
#       pip install agentscope\[ollama,distribute\]
#
