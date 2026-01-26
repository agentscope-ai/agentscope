# -*- coding: utf-8 -*-
"""The agent card definition for the A2A agent."""
from a2a.types import AgentCard, AgentCapabilities, AgentSkill
from a2ui.extension.a2ui_extension import get_a2ui_agent_extension

agent_card = AgentCard(
    name="Friday",
    description="A simple ReAct agent that handles input queries",
    url="http://localhost:10002",
    version="1.0.0",
    capabilities=AgentCapabilities(
        push_notifications=False,
        state_transition_history=True,
        streaming=True,
        extensions=[get_a2ui_agent_extension()],
    ),
    default_input_modes=["text/plain"],
    default_output_modes=["text/plain"],
    skills=[
        AgentSkill(
            name="execute_python_code",
            id="execute_python_code",
            description="Execute Python code snippets.",
            tags=["code_execution"],
        ),
        AgentSkill(
            name="execute_shell_command",
            id="execute_shell_command",
            description="Execute shell commands on the server.",
            tags=["code_execution"],
        ),
        AgentSkill(
            name="view_text_file",
            id="view_text_file",
            description="View the content of a text file on the server.",
            tags=["file_viewing"],
        ),
    ],
)
