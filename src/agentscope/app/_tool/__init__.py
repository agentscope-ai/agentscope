# -*- coding: utf-8 -*-
"""Framework-builtin tools wired into team-participating agents.

These tools differ from the workspace-provided builtins (Bash, Read,
Task series, …) in two ways:

1. **Construction depends on app-level resources** — they bind a
   :class:`StorageBase` + :class:`MessageBus` reference plus the
   request-scoped ``user_id`` / ``session_id`` / ``agent_id`` at agent
   assembly time, and call storage / bus directly in their
   ``__call__`` — except ``TeamDelete``, which delegates to
   :class:`SessionService` for cascade deletion.
2. **Visibility depends on the session's team role, not the agent's
   source field** — a session that is not in any team OR that is its
   team's leader gets the full leader-side toolset (``TeamCreate /
   AgentCreate / TeamSay / TeamDelete``, plus ``AgentInvite`` when the
   user has any invitable agents). A session that is a worker in some
   team gets only ``TeamSay``. This session-level distinction is what
   lets a borrowed ("invited") agent's session see worker-only tools
   even though the underlying ``AgentRecord`` still has
   ``source='user'``. Each tool re-reads the current session + team
   from storage at ``__call__`` time and fails clearly if its
   precondition is not met.

   The benefit is that a single chat run can ``TeamCreate`` and then
   ``AgentCreate`` immediately afterward — there is no toolkit
   refresh point because the toolkit never changed; only the
   underlying storage state did, which the next tool reads fresh.

Selection of the right subset happens inline in :func:`get_toolkit`;
there is no separate "team tool factory" helper.
"""
from ._agent_create import AgentCreate, DEFAULT_SUB_AGENT_TEMPLATE
from ._agent_invite import AgentInvite
from ._team_create import TeamCreate
from ._team_delete import TeamDelete
from ._team_say import TeamSay

__all__ = [
    "AgentCreate",
    "AgentInvite",
    "DEFAULT_SUB_AGENT_TEMPLATE",
    "TeamCreate",
    "TeamDelete",
    "TeamSay",
]
