# -*- coding: utf-8 -*-
"""Per-agent tool whitelist applied at the ``Toolkit`` level.

The framework's ``get_toolkit`` attaches many tool sources besides the
caller-supplied ``extra_factory``:

- workspace builtins (Bash / Read / Write / Edit / Glob / Grep)
- planning tools (TaskCreate / TaskList / TaskGet / TaskUpdate)
- background-task control (ToolStop)
- schedule control (ScheduleCreate / ...)
- team tools (TeamCreate / AgentCreate / TeamSay / TeamDelete /
  AgentInvite)
- middleware-provided tools

The per-agent whitelist maintained by ``agent_tools_router``
(PUT/DELETE ``/agents/{id}/tools/{name}``) previously only filtered
the ``extra_factory`` source and ``list_mcps`` — so an agent created
with ``enabled_tools=["get_current_time"]`` still saw every other tool
at runtime (and could call them), which defeats least privilege.

Fix without touching framework code: patch the ``get_toolkit`` binding
inside ``agentscope.app._service._chat`` — the only call site, looked
up at call time through the module global, so there is no import-order
race. The wrapper filters the assembled ``Toolkit`` by the per-agent
whitelist:

- empty whitelist -> everything stays available (same semantics as the
  tool config APIs);
- non-empty whitelist -> only listed tool names survive, across every
  tool source above.

MCPs are already filtered by ``WhitelistWorkspaceManager``; skills are
installed explicitly into the agent's workspace so they are left
untouched.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("bocomadp.toolkit_whitelist")

_original_get_toolkit: Any = None


def _keep(tool: Any, allowed: set[str]) -> bool:
    """Return whether *tool*'s name is in the allowed set."""
    return getattr(tool, "name", "") in allowed


async def _whitelisted_get_toolkit(*args: Any, **kwargs: Any):
    """Assemble the toolkit, then filter by the per-agent whitelist."""
    toolkit = await _original_get_toolkit(*args, **kwargs)

    agent_record = kwargs.get("agent_record")
    agent_id = getattr(agent_record, "id", "") or ""

    # Lazy import: this module may be imported early during startup.
    from bocomadp.routers.agent_tools import _tool_whitelists

    whitelist = _tool_whitelists.get(agent_id, [])
    if not whitelist:
        return toolkit
    allowed = set(whitelist)

    # The framework ``Toolkit`` has no top-level ``tools`` attribute:
    # tools live inside each ``ToolGroup`` (the "basic" group plus the
    # extra groups from get_toolkit). Filter every group's tools, then
    # drop non-basic groups that become empty: the builtin meta tool
    # (``reset_tools``) is auto-exposed whenever any non-basic group
    # exists, so leaving an empty group would still hand the agent a
    # tool that advertises (and toggles) tool groups it should not see.
    groups = getattr(toolkit, "tool_groups", None) or []
    for group in groups:
        group.tools = [t for t in group.tools if _keep(t, allowed)]
    toolkit.tool_groups = [
        g
        for g in groups
        if g.name == "basic" or g.tools or getattr(g, "mcps", None)
    ]
    return toolkit


def patch_get_toolkit() -> None:
    """Replace the chat service's ``get_toolkit`` binding (idempotent).

    Must run before the first chat run; the wrapper is looked up at
    call time via the module global, so there is no import-order race.
    """
    global _original_get_toolkit
    if _original_get_toolkit is not None:
        return

    from agentscope.app._service import _chat as _chat_module

    _original_get_toolkit = _chat_module.get_toolkit
    _chat_module.get_toolkit = _whitelisted_get_toolkit
    logger.info(
        "patched %s.get_toolkit with per-agent whitelist filter",
        _chat_module.__name__,
    )


__all__ = ["patch_get_toolkit"]
