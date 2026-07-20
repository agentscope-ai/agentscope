# -*- coding: utf-8 -*-
"""The TeamCreate tool — establishes a new team led by the current session."""
from pydantic import Field

from ._team_tool_base import _TeamToolBase
from ..storage import TeamData, TeamRecord
from ...message import TextBlock, ToolResultState
from ...tool import ToolChunk, ParamsBase


class _TeamCreateParams(ParamsBase):
    """Parameters for :class:`TeamCreate`."""

    name: str = Field(
        description=(
            "Display name of the team. Used by the user to identify the "
            "team and shown in the team UI."
        ),
    )
    description: str = Field(
        description=(
            "What the team is for — its overall goal or shared context. "
            "This becomes the team's charter and is wired into every "
            "member's system prompt so all members share the same "
            "high-level understanding of why the team exists."
        ),
    )


class TeamCreate(_TeamToolBase):
    """Create a new team and become its leader."""

    name: str = "TeamCreate"

    description: str = """Create a new team led by your current session and \
return its team id.

## When to Use This Tool
Use this tool when the task you've been given is best decomposed into \
parallel sub-tasks executed by multiple specialised agents (members) \
under your coordination. After creating the team, use ``AgentCreate`` to \
spawn each member with its own role, prompt, and permission mode. NOTE: \
the ``prompt`` you pass to ``AgentCreate`` is delivered to that member \
automatically, so do **NOT** call ``TeamSay`` right after ``AgentCreate`` — \
just wait for the members to report back.

## When NOT to Use This Tool
- The task is small enough to handle yourself.
- You already lead a team in this session — a session can only lead \
one team at a time.
"""

    input_schema: dict = _TeamCreateParams.model_json_schema()

    async def __call__(
        self,
        name: str,
        description: str,
    ) -> ToolChunk:
        """Create the team directly via storage.

        Reads the current session record from storage to enforce the
        precondition: a session can only lead one team at a time.
        This makes the tool safe to attach unconditionally to
        ``source='user'`` agents — calling it when the session
        already leads a team returns a clear error rather than
        silently corrupting state.

        Args:
            name (`str`):
                Display name of the team.
            description (`str`):
                Description / charter of the team.

        Returns:
            `ToolChunk`:
                A success message containing the team id, or an error
                chunk if a precondition fails or creation failed.
        """
        try:
            session = await self._storage.get_session(
                self._user_id,
                self._agent_id,
                self._session_id,
            )
            if session is None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                "TeamCreate: session "
                                f"{self._session_id} not found."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            if session.team_id is not None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                "TeamCreate: this session is already "
                                f"part of team {session.team_id}. A "
                                "session can only lead one team at a "
                                "time — dissolve the current one with "
                                "TeamDelete first."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

            team = TeamRecord(
                user_id=self._user_id,
                session_id=self._session_id,
                data=TeamData(
                    name=name,
                    description=description,
                    member_ids=[],
                ),
            )
            await self._storage.upsert_team(self._user_id, team)
            await self._storage.set_session_team_id(
                self._user_id,
                self._session_id,
                team.id,
            )

            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Team {team.id} ({team.data.name}) created. "
                            f"You are the leader. Use AgentCreate to add "
                            f"members, then TeamSay to coordinate them."
                        ),
                    ),
                ],
            )
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[TextBlock(text=f"TeamCreate failed: {e}")],
                state=ToolResultState.ERROR,
            )
