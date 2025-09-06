"""
OpenAI ReAct Agent Example using AgentScope.

Features:
- Loads API keys from .env or environment
- Configures GPT model with streaming output
- Registers basic Python + shell tools
- Uses AgentScope's ReActAgent for reasoning and tool use
- Interactive terminal loop

Author: [Your Name]
"""

import os
import asyncio
from pathlib import Path

import agentscope as ascope
from agentscope.agent import ReActAgent, UserAgent
from agentscope.model import OpenAIChatModel
from agentscope.formatter import OpenAIChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit, execute_python_code, execute_shell_command


if __name__ == "__main__":
    asyncio.run(main())
