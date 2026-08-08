# -*- coding: utf-8 -*-
"""The AgentKick tool — removes one member from a led team."""

from pydantic import Field

from ._team_routing import _build_member_directory
from ._team_tool_base import _TeamToolBase
from ...message import TextBlock, ToolResultState
from ...tool import ParamsBase, ToolChunk


class _AgentKickParams(ParamsBase):
    """Parameters for :class:`AgentKick`."""

    target: str = Field(
        description=(
            "The member to remove. Use the same name/display string "
            "that TeamSay would accept — the plain name for created "
            'members or "<name>@<handle>" for invited members.'
        ),
    )


class AgentKick(_TeamToolBase):
    """Remove one member from the team led by the current session."""

    name: str = "AgentKick"
    description: str = """Remove a single member from the team you lead.

## When to Use This Tool
- A member is producing incorrect or irrelevant output.
- A member keeps failing or is stuck in an error loop.
- You no longer need a particular member's contribution.

## When NOT to Use This Tool
- You want to dissolve the entire team — use ``TeamDelete`` instead.
- The member is still producing useful output.

## Effects
- Created members (from ``AgentCreate``) are fully deleted.
- Invited members (from ``AgentInvite``) lose only their team session;
  their standalone agent remains usable by the user.
- The member is removed from the team roster immediately and can no
  longer be addressed via ``TeamSay``.

## Important
- The member's in-progress work is lost. Be sure before calling.
- You cannot remove yourself — use ``TeamDelete`` to dissolve the team.
"""

    input_schema: dict = _AgentKickParams.model_json_schema()
    is_read_only: bool = False

    async def __call__(self, target: str) -> ToolChunk:
        """Resolve and remove one member from the current team.

        Args:
            target (`str`):
                The same display name accepted by :class:`TeamSay`.

        Returns:
            `ToolChunk`:
                A role-aware confirmation, or an error chunk when a
                precondition or target lookup fails.
        """
        try:
            session = await self._storage.get_session(
                self._user_id,
                self._agent_id,
                self._session_id,
            )
            if session is None or session.team_id is None:
                return _error(
                    "AgentKick: this session is not in any team.",
                )

            team = await self._storage.get_team(
                self._user_id,
                session.team_id,
            )
            if team is None:
                return _error(
                    f"AgentKick: team {session.team_id} no longer exists.",
                )
            if team.session_id != self._session_id:
                return _error(
                    "AgentKick: only the team leader can remove members; "
                    "this session is a worker.",
                )

            leader_agent = await self._storage.get_agent(
                self._user_id,
                self._agent_id,
            )
            leader_name = (
                leader_agent.data.name
                if leader_agent is not None
                else self._agent_id
            )
            if target == leader_name:
                return _error(
                    "AgentKick: you cannot remove yourself — use "
                    "TeamDelete to dissolve the team.",
                )

            directory = await _build_member_directory(
                self._storage,
                self._user_id,
                team,
            )
            member = directory.get(target)
            if member is None:
                return _error(
                    f"AgentKick: no team member is named {target!r}. "
                    f"Known members: {sorted(directory)}.",
                )

            # Local import avoids a module-load cycle between tools and
            # services, matching TeamDelete's dependency pattern.
            from .._service import SessionService  # noqa: PLC0415

            service = SessionService(
                storage=self._storage,
                message_bus=self._message_bus,
            )
            removed = await service.delete_team_member(
                self._user_id,
                team.id,
                member,
            )
            if not removed:
                return _error(
                    f"AgentKick: member {target!r} is no longer part "
                    f"of team {team.id}.",
                )

            if member.role == "created":
                effect = "its agent and session were fully deleted"
            else:
                effect = (
                    "only its team session was deleted; its standalone "
                    "agent remains available"
                )
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Removed member {target!r} from team "
                            f"{team.id}; {effect}."
                        ),
                    ),
                ],
            )
        except Exception as e:  # pylint: disable=broad-except
            return _error(f"AgentKick failed: {e}")


def _error(text: str) -> ToolChunk:
    """Build an error-state tool result.

    Args:
        text (`str`): The error message.

    Returns:
        `ToolChunk`: A final error chunk containing ``text``.
    """
    return ToolChunk(
        content=[TextBlock(text=text)],
        state=ToolResultState.ERROR,
    )
