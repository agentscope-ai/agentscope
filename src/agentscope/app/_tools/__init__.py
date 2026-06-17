# -*- coding: utf-8 -*-
"""Framework-builtin tools wired into team-participating agents.

These tools differ from the workspace-provided builtins (Bash, Read,
Task series, …) in two ways:

1. **Construction depends on app-level resources** — they bind a
   :class:`StorageBase` + :class:`MessageBus` reference plus the
   request-scoped ``user_id`` / ``session_id`` / ``agent_id`` at agent
   assembly time, and call storage / bus directly in their
   ``__call__`` (no intermediate service layer).
2. **Visibility depends only on the agent's source field** —
   ``source='user'`` agents always see the full leader-side toolset
   (``TeamCreate / AgentCreate / TeamSay / TeamDelete``) regardless of
   whether they currently lead a team. Each tool checks the storage
   state at ``__call__`` time and fails clearly if its precondition
   is not met. ``source='team'`` workers only see ``TeamSay``.

   The benefit is that a single chat run can ``TeamCreate`` and then
   ``AgentCreate`` immediately afterward — there is no toolkit
   refresh point because the toolkit never changed; only the
   underlying storage state did, which the next tool reads fresh.

Selection of the right subset by ``agent.source`` happens inline in
:func:`get_toolkit`; there is no separate "team tool factory" helper.
"""
from ._agent_create import AgentCreate, DEFAULT_SUB_AGENT_TEMPLATE
from ._team_create import TeamCreate
from ._team_delete import TeamDelete
from ._team_say import TeamSay

__all__ = [
    "AgentCreate",
    "DEFAULT_SUB_AGENT_TEMPLATE",
    "TeamCreate",
    "TeamDelete",
    "TeamSay",
]
