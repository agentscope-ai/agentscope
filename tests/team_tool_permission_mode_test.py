# -*- coding: utf-8 -*-
"""Permission-mode decisions for the team tools.

Every team tool allows itself unconditionally, so no mode ever asks for an
approval. That must not make the mutating ones look read-only: ``EXPLORE``
denies each invocation whose ``check_read_only`` is ``False``, and
``TeamCreate`` / ``TeamDelete`` / ``AgentCreate`` / ``AgentInvite`` all
write storage. ``TeamSay`` stays read-only so a read-only worker can still
report to its leader.
"""
import unittest
from unittest.async_case import IsolatedAsyncioTestCase

from agentscope.app._tool._agent_create import AgentCreate
from agentscope.app._tool._agent_invite import AgentInvite
from agentscope.app._tool._team_create import TeamCreate
from agentscope.app._tool._team_delete import TeamDelete
from agentscope.app._tool._team_say import TeamSay
from agentscope.permission import (
    PermissionBehavior,
    PermissionContext,
    PermissionEngine,
    PermissionMode,
)
from agentscope.tool import ToolBase

_MUTATING_TOOLS = {
    "TeamCreate": TeamCreate,
    "TeamDelete": TeamDelete,
    "AgentCreate": AgentCreate,
    "AgentInvite": AgentInvite,
}


class _TeamToolPermissionTestBase(IsolatedAsyncioTestCase):
    """Build every team tool with no storage: only decisions are tested."""

    def _make_tool(self, cls: type[ToolBase]) -> ToolBase:
        kwargs = {
            "storage": None,
            "message_bus": None,
            "workspace_manager": None,
            "user_id": "user",
            "session_id": "session",
            "agent_id": "agent",
        }
        if cls is AgentInvite:
            return cls(**kwargs, invitable_pool=[])
        return cls(**kwargs)

    async def _decide(
        self,
        mode: PermissionMode,
        tool: ToolBase,
    ) -> PermissionBehavior:
        """Resolve one invocation under ``mode``."""
        engine = PermissionEngine(context=PermissionContext(mode=mode))
        decision = await engine.check_permission(tool, {})
        return decision.behavior


class TeamToolExploreModeTest(_TeamToolPermissionTestBase):
    """EXPLORE is read-only, so the mutating team tools must be denied."""

    async def test_mutating_team_tools_are_not_read_only(self) -> None:
        """Storage-writing team tools must not claim to be read-only."""
        for name, cls in _MUTATING_TOOLS.items():
            with self.subTest(tool=name):
                self.assertFalse(self._make_tool(cls).is_read_only)

    async def test_mutating_team_tools_are_denied(self) -> None:
        """EXPLORE denies every mutating team tool."""
        for name, cls in _MUTATING_TOOLS.items():
            with self.subTest(tool=name):
                behavior = await self._decide(
                    PermissionMode.EXPLORE,
                    self._make_tool(cls),
                )
                self.assertEqual(behavior, PermissionBehavior.DENY)

    async def test_team_say_is_allowed(self) -> None:
        """A read-only worker must still be able to report to its leader."""
        tool = TeamSay(
            storage=None,
            message_bus=None,
            workspace_manager=None,
            user_id="user",
            session_id="session",
            agent_id="agent",
            role="worker",
        )
        self.assertTrue(tool.is_read_only)
        self.assertEqual(
            await self._decide(PermissionMode.EXPLORE, tool),
            PermissionBehavior.ALLOW,
        )


class TeamToolDefaultModeTest(_TeamToolPermissionTestBase):
    """Outside EXPLORE the team tools keep allowing themselves."""

    async def test_mutating_team_tools_stay_allowed(self) -> None:
        """Their own ``check_permissions`` allows them outside EXPLORE."""
        for name, cls in _MUTATING_TOOLS.items():
            with self.subTest(tool=name):
                behavior = await self._decide(
                    PermissionMode.DEFAULT,
                    self._make_tool(cls),
                )
                self.assertEqual(behavior, PermissionBehavior.ALLOW)


if __name__ == "__main__":
    unittest.main()
