# -*- coding: utf-8 -*-
"""The console module for viewing and trying agents in the terminal.

Two public entries serve two different scenarios:

- :class:`ConsoleRenderer`: a passive event renderer that turns an
  :class:`~agentscope.event.AgentEvent` stream into line-based terminal
  output. Embed it in your own code (scripts, agent pipelines, tests)
  where you own the loop, the inputs and the human-in-the-loop handling.
- :func:`launch_console`: an interactive chat loop bound to a single
  agent — input, tool-call confirmation and Ctrl+C interruption
  included — for trying an agent without writing any UI code.
"""
from ._console import launch_console
from ._renderer import ConsoleRenderer

__all__ = [
    "ConsoleRenderer",
    "launch_console",
]
