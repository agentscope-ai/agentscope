# -*- coding: utf-8 -*-
"""The TeamDelete tool — dissolves the team led by the current session."""
from ._team_tool_base import _TeamToolBase
from ...message import TextBlock, ToolResultState
from ...tool import ToolChunk, ParamsBase


class _TeamDeleteParams(ParamsBase):
    """Parameters for :class:`TeamDelete` — none."""


class TeamDelete(_TeamToolBase):
    """Dissolve the team you currently lead and clean up all members."""

    name: str = "TeamDelete"

    description: str = """Dissolve the team you currently lead.

## When to Use This Tool
- The team has finished its work and you want to clean up.
- The team is unrecoverably stuck and you want to start over.
- You have collected the deliverables you need from each member.

## When NOT to Use This Tool
- Members are still producing useful output and you may want their \
follow-up; dissolving deletes them and they cannot be revived.
- You want to remove only one specific member — there is no "remove \
single member" tool in v1, only whole-team dissolution.

## Effects
- Every member agent + its session is deleted.
- The team record is deleted.
- Your own session continues to exist but is no longer associated with \
any team — the team-related tools become unavailable on subsequent \
reasoning steps.

This is irreversible.
"""

    input_schema: dict = _TeamDeleteParams.model_json_schema()

    async def __call__(self) -> ToolChunk:
        """Dissolve the bound session's team via :class:`SessionService`.

        Reads the current session + team records from storage to
        enforce: caller must be in a team AND must be its leader.
        Then delegates the actual cancel + delete + bus-purge cascade
        to :meth:`SessionService.delete_team`, which routes every
        member through the shared session-level primitive — so worker
        chat runs are cancelled cross-process and their bus state is
        cleaned up the same way ``DELETE /agents`` would.

        Returns:
            `ToolChunk`:
                A confirmation message, or an error chunk if a
                precondition fails.
        """
        try:
            team = await self._require_leader_team("dissolve the team")

            # Local import to avoid a circular dependency between
            # ``_tools`` and ``_service`` at module load.
            from .._service import SessionService  # noqa: PLC0415

            session_service = SessionService(
                storage=self._storage,
                message_bus=self._message_bus,
                workspace_manager=self._workspace_manager,
            )
            await session_service.delete_team(self._user_id, team.id)
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Team {team.id} dissolved. All members "
                            f"deleted; your session is no longer "
                            f"leading any team."
                        ),
                    ),
                ],
            )
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[TextBlock(text=f"TeamDelete failed: {e}")],
                state=ToolResultState.ERROR,
            )
