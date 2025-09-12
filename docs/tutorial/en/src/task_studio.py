# -*- coding: utf-8 -*-
"""
.. _studio:

AgentScope Studio
=========================

AgentScope Studio is a local-deployed web application that

- provides **project management** for the development of agent applications
- provides native **visualization** for running applications and tracing
- provides a **built-in agent** named "Friday" that supports secondary development

.. note:: The Studio is under fast development, more features are coming soon!

.. figure:: ../../_static/images/studio_home.webp
    :width: 100%
    :alt: AgentScope Studio Home Page
    :class: bordered-image
    :align: center

    AgentScope Studio Home Page

Quick Start
~~~~~~~~~~~~~~~~~~~~~~~~

AgentScope Studio is installed via ``npm``:

.. code-block:: bash

    npm install -g @agentscope/studio


Start the Studio with the following command:

.. code-block:: bash

    as_studio

To connect your application to the Studio, use the ``agentscope.init`` function with the ``studio_url`` parameter:

.. code-block:: python

    import agentscope

    agentscope.init(studio_url="http://localhost:8000")

    # your application code
    ...

Then, you can see your application in the Studio as follows:

.. figure:: ../../_static/images/studio_project.webp
    :width: 100%
    :alt: Project management
    :class: bordered-image
    :align: center

    Project management in AgentScope Studio

The details about your running application, e.g. token usage, model invocations, and tracing information, can all be viewed in the Studio.

.. figure:: ../../_static/images/studio_run.webp
    :width: 100%
    :alt: AgentScope Studio run Page
    :class: bordered-image
    :align: center

    Application visualization in AgentScope Studio


Friday Agent
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Friday is an experimental local-deployed agent built by AgentScope, aims at

- answering questions about the AgentScope,
- providing a quick secondary development environment for developers,
- integrating all available features in AgentScope to build a more powerful agent, and
- testing and integrating the advanced features in AgentScope.

.. note:: We highly greet contributions from the community to improve Friday! Feel free to open issues or pull requests on our `GitHub repository <https://github.com/agentscope-ai/agentscope>`_.

We are keeping improving Friday, and currently it integrates the following features in AgentScope:

.. list-table::
    :header-rows: 1

    * - Feature
      - Status
      - Further Reading
      - Description
    * - Meta tool
      - ✅
      - :ref:`tool`
      - Group-wise tool management, and allow agent to change equipped tools by itself.
    * - Agent Hook
      - ✅
      - :ref:`hook`
      - Use hook to forward the printing messages to the frontend.
    * - Agent Interruption
      - ✅
      - :ref:`agent`
      - Allow use to interrupt the agent's reply process with post-processing.
    * - Truncated Prompt
      - ✅
      - :ref:`prompt`
      - Support to truncate the prompt with the preset max token limit.
    * - State & Session Management
      - ✅
      - :ref:`state`
      - Auto state management and session management for agents, maintaining the state between different runs.
    * - Long-term Memory
      - 🚧
      - :ref:`memory`
      - Support long-term memory management.


"""
