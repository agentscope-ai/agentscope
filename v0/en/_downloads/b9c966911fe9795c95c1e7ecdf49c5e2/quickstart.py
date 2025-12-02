# -*- coding: utf-8 -*-
"""
.. _quickstart:

Quick Start
============================

AgentScope requires Python 3.9 or higher. You can install from source or pypi.

From PyPI
----------------
.. code-block:: bash

    pip install agentscope

From Source
----------------
To install AgentScope from source, you need to clone the repository from
GitHub and install by the following commands

.. code-block:: bash

    git clone https://github.com/modelscope/agentscope
    cd agentscope
    pip install -e .

To ensure AgentScope is installed normally. You can execute the following code:
"""

import agentscope

print(agentscope.__version__)

# %%
# Extra Dependencies
# ----------------------------
#
# AgentScope provides extra dependencies for different demands. You can
# install them according to your demands.
#
# - ollama: Ollama API
# - litellm: Litellm API
# - zhipuai: Zhipuai API
# - gemini: Gemini API
# - anthropic: Anthropic API
# - service: The dependencies for different tool functions
# - distribute: The dependencies for distribution mode
# - full: All the dependencies
#
# Taking distribution mode as an example, the installation command differs
# according to your operation OS.
#
# For Windows users:
#
# .. code-block:: bash
#
#       pip install agentscope[gemini]
#       # or
#       pip install agentscope[ollama,distribute]
#
# For Mac and Linux users:
#
# .. code-block:: bash
#
#       pip install agentscope\[gemini\]
#       # or
#       pip install agentscope\[ollama,distribute\]
