---
name: Analyzing AgentScope Library
description: This skill provides a way to retrieve information from the AgentScope library for analysis and decision-making.
---

# Analyzing AgentScope Library

## Overview

This guide covers the essential operations for retrieving and answering questions about the AgentScope library.
If you need to answer questions regarding the AgentScope library, or look up specific information, functions/classes,
examples or guidance, this skill will help you achieve that.

## Quick Start

The skill provides the following key scripts:

- Search for guidance in the AgentScope tutorial.
- Search for official examples and recommended implementations provided by AgentScope.
- A quick interface to view AgentScope's Python library by given a module name (e.g. agentscope), and return the module's submodules, classes, and functions.

When being asked an AgentScope-related question, you can follow the steps below to find the relevant information:

First decide which of the three scripts to use based on the user's question.
- If user asks for "how to use" types of questions, use the "Search for Guidance" script to find the relevant tutorial
- If user asks for "how to implement/build" types of questions, first search for relevant examples. If not found, then
  consider what functions are needed and search in the guide/tutorial
- If user asks for "how to initialize" types of questions, first search for relevant tutorials. If not found, then
  consider to search for the corresponding modules, classes, or functions in the library.


### Search for Examples

First ask for the user's permission to clone the agentscope GitHub repository if you haven't done so:

```bash
git clone -b main https://github.com/agentscope-ai/agentscope
```

In this repo, the `examples` folder contains various examples demonstrating how to use different features of the
AgentScope library.
They are organized in a tree structure by different functionalities. You should use shell command like `ls` or `cat` to
navigate and view the examples. Avoid using `find` command to search for examples, as the name of the example
files may not directly relate to the functionality being searched for.

### Search for Guidance

Similarly, first ensure you have cloned the agentscope GitHub repository.

The source agentscope tutorial is located in the `docs/tutorials` folder of the agentscope GitHub repository. It's
organized by the different sections. To search for guidance, go to the `docs/tutorials` folder and view the tutorial
files by shell command like `ls` or `cat`.


### Search for Targeted Modules

First, ensure you have installed the agentscope library in your environment:

```bash
pip list | grep agentscope
```

If not installed, ask the user for permission to install it by command:

```bash
pip install agentscope
```

Then, run the following script to search for specific modules, classes, or functions. It's suggested to start with
`agentscope` as the root module name, and then specify the submodule name you want to search for.

```bash
python view_agentscope_module.py --module agentscope
```

About detailed usage, please refer to the `./view_agentscope_module.py` script (located in the same folder as this
SKILL.md file).

