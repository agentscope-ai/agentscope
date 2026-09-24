# -*- coding: utf-8 -*-
"""Shared constants for the framework-builtin team tools.

Centralised here (rather than duplicated per-module) so contracts that
must agree across tools have exactly one source of truth. Adding a new
tool that touches the same invariant should import from here, not
redeclare the value.
"""

HANDLE_LEN: int = 8
# Length of the ``agent_id`` prefix used to disambiguate invited
# members in the ``"<name>@<handle>"`` display string.
#
# Must be the same value at every producer (``AgentInvite``, which
# builds the enum and the display string) and every consumer
# (``TeamSay``, which parses the display string back into a routing
# key). Widening this in the future is a coordinated change — leaders
# that already routed via the old length would target the wrong member
# otherwise.
