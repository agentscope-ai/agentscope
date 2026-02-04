# -*- coding: utf-8 -*-
"""
.. _installation:

Installation
============================

AgentScope requires Python 3.10 or higher. You can install from source or pypi.

From PyPI
----------------
.. code-block:: bash

    pip install agentscope

From Source
----------------
To install AgentScope from source, you need to clone the repository from
GitHub and install by the following commands

.. code-block:: bash

    git clone -b main https://github.com/agentscope-ai/agentscope
    cd agentscope
    pip install -e .

To ensure AgentScope is installed successfully, check via executing the following code:
"""

import agentscope

print(agentscope.__version__)

# %%
# Extra Dependencies
# ----------------------------
#
# To satisfy the requirements of different functionalities, AgentScope provides
# extra dependencies that can be installed based on your needs.
#
# - full: Including extra dependencies for model APIs and tool functions
# - dev: Development dependencies, including testing and documentation tools
#
# For example, when installing the full dependencies, the installation command varies depending on your operating system.
#
# For Windows users:
#
# .. code-block:: bash
#
#       pip install agentscope[full]
#
# For Mac and Linux users:
#
# .. code-block:: bash
#
#       pip install agentscope\[full\]
