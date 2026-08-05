"""Sub-agent templates — reusable blueprints passed to ``create_app``.

Adapted from agentscope's ``examples/agent_service/main.py``. Each template
defines a sub-agent *type* (e.g. ``"researcher"``, ``"coder"``) that the
leader agent can route to via the ``AgentCreate`` tool's ``subagent_type``
parameter.

Add your own templates here. They are wired into the app in ``main.py``
via ``create_app(custom_subagent_templates=load_subagent_templates())``.

## How to add a new template

1. Pick a unique ``type`` string (must be unique across all templates).
2. Write a ``system_prompt_template`` using the available placeholders:
   ``{member_name}``, ``{team_name}``, ``{leader_name}``,
   ``{team_description}``, ``{member_description}``.
3. Pick a :class:`PermissionContext` matching the trust level the sub-agent
   needs (EXPLORE = read-only, WRITE = read+write filesystem, ...).
4. Register it in :func:`load_subagent_templates`.
"""

from __future__ import annotations

from agentscope.app import SubAgentTemplate
from agentscope.permission import PermissionContext, PermissionMode


def _researcher_template() -> SubAgentTemplate:
    """Read-only explorer sub-agent."""
    return SubAgentTemplate(
        type="researcher",
        description=(
            "Read-only agents specialized in research and investigation "
            "tasks. They can read files and gather information but cannot "
            "modify, create, or delete them. Use this agent type when you "
            "need to investigate the codebase, understand its structure, "
            "or gather information from files to support planning — without "
            "making any changes."
        ),
        system_prompt_template=(
            "You are {member_name}, a researcher agent in team "
            "'{team_name}' led by {leader_name}.\n\n"
            "Team purpose: {team_description}\n\n"
            "Your role: {member_description}\n\n"
            "## Responsibilities\n"
            "- Complete the research tasks assigned by the team leader.\n"
            "- You are read-only: you may inspect files and the codebase, "
            "but you must never modify, create, or delete anything.\n\n"
            "## Reporting\n"
            "- Always report the task result back to {leader_name} using "
            "the TeamSay tool, whether the task succeeds or fails.\n"
            "- Keep your private reasoning private; only share conclusions "
            "and findings that the leader needs.\n\n"
            "Note: `TeamSay` is your ONLY channel to communicate with "
            "{leader_name} and the other team members. Any other output you "
            "produce is invisible to them, so anything you want them to see "
            "MUST be sent through `TeamSay`."
        ),
        permission_context=PermissionContext(mode=PermissionMode.EXPLORE),
    )


def _coder_template() -> SubAgentTemplate:
    """Read-write coder sub-agent (example — adjust permissions to taste)."""
    return SubAgentTemplate(
        type="coder",
        description=(
            "Agents that can read and write files, run shell commands, and "
            "make code changes. Use this agent type when the task requires "
            "modifying the codebase, running tests, or executing scripts."
        ),
        system_prompt_template=(
            "You are {member_name}, a coder agent in team '{team_name}' "
            "led by {leader_name}.\n\n"
            "Team purpose: {team_description}\n\n"
            "Your role: {member_description}\n\n"
            "## Responsibilities\n"
            "- Complete the coding tasks assigned by the team leader.\n"
            "- You may read, write, and delete files, and run shell "
            "commands within your permission scope.\n\n"
            "## Reporting\n"
            "- Always report the task result back to {leader_name} using "
            "the TeamSay tool, whether the task succeeds or fails.\n"
            "- If a command fails, include the error output in your "
            "report so the leader can decide how to proceed.\n\n"
            "Note: `TeamSay` is your ONLY channel to communicate with "
            "{leader_name} and the other team members."
        ),
        # ACCEPT_EDITS allows file read/write + shell within the workspace sandbox.
        permission_context=PermissionContext(mode=PermissionMode.ACCEPT_EDITS),
    )


def load_subagent_templates() -> list[SubAgentTemplate]:
    """Return all sub-agent templates to register with ``create_app``.

    Add new templates to this list as you build them. Duplicate ``type``
    values will be rejected by ``create_app`` at startup.
    """
    return [
        _researcher_template(),
        _coder_template(),
    ]
