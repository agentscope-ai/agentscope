# -*- coding: utf-8 -*-
"""Per-agent tool whitelist applied to workspace MCPs.

The framework injects MCPs straight from ``workspace.list_mcps()``
(see ``agentscope.app._service._toolkit.get_toolkit``), bypassing
``extra_agent_tools`` entirely — so the whitelist maintained by
``agent_tools_router`` (PUT/DELETE ``/api/agents/{id}/tools/{name}``)
cannot filter MCPs at the ``extra_factory`` layer the way it filters
project tools.

Fix without touching framework code: wrap the workspace manager.
``WorkspaceManagerBase.get_workspace`` already receives ``agent_id``,
so the wrapper intercepts it and returns a delegating proxy whose
``list_mcps`` applies the per-agent whitelist.  ``get_toolkit`` calls
``list_mcps`` on every chat run, so whitelist changes take effect
immediately.
"""

from __future__ import annotations

from typing import Any


class _WhitelistWorkspaceProxy:
    """Delegating workspace proxy filtering ``list_mcps`` per agent."""

    def __init__(self, workspace: Any, agent_id: str) -> None:
        # ``object.__setattr__`` keeps ``__setattr__`` default so the
        # proxy stays inert; ``__getattr__`` below only fires on miss.
        object.__setattr__(self, "_workspace", workspace)
        object.__setattr__(self, "_agent_id", agent_id)

    async def list_mcps(self) -> list:
        """Return MCPs allowed by the per-agent tool whitelist.

        Empty whitelist means all available (same semantics as the
        tool config APIs); non-empty keeps only listed names.
        """
        from bocomadp.routers.agent_tools import _tool_whitelists

        mcps = await self._workspace.list_mcps()
        whitelist = _tool_whitelists.get(self._agent_id, [])
        if not whitelist:
            return mcps
        allowed = set(whitelist)
        return [m for m in mcps if getattr(m, "name", "") in allowed]

    def __getattr__(self, item: str) -> Any:
        # Everything else (list_tools / list_skills / get_backend /
        # add_mcp / add_skill_archive / workdir / ...) delegates.
        return getattr(self._workspace, item)


class WhitelistWorkspaceManager:
    """Wrap a ``WorkspaceManagerBase``, filtering MCPs per agent.

    All methods and attributes except ``get_workspace`` and the
    lifecycle hooks delegate to the inner manager, so local and K8s
    managers both work unchanged.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    async def get_workspace(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        workspace_id: str | None = None,
    ) -> Any:
        ws = await self._inner.get_workspace(
            user_id,
            agent_id,
            session_id,
            workspace_id,
        )
        return _WhitelistWorkspaceProxy(ws, agent_id)

    async def __aenter__(self) -> "WhitelistWorkspaceManager":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> Any:
        return await self._inner.__aexit__(*exc)

    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)


__all__ = ["WhitelistWorkspaceManager"]
