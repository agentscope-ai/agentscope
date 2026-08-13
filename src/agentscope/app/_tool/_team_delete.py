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

    description: str = """解散你当前领导的团队。

## 何时使用该工具
- 团队已完成其工作，你想进行清理。
- 团队陷入无法恢复的僵局，你想重新开始。
- 你已经从每位成员处收集到了所需的交付物。

## 何时不要使用该工具
- 成员仍在产生有用的输出，你可能还需要它们的后续成果；\
解散会删除它们，且无法恢复。
- 你只想移除某一个特定成员——v1 中没有"移除单个成员"的工具，\
只能整体解散团队。

## 影响
- 每一位成员智能体及其会话都会被删除。
- 团队记录会被删除。
- 你自己的会话仍然存在，但不再关联任何团队——相关团队工具在\
后续推理步骤中将不可用。

该操作不可逆。
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
            session = await self._storage.get_session(
                self._user_id,
                self._agent_id,
                self._session_id,
            )
            if session is None or session.team_id is None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                "TeamDelete: this session is not in "
                                "any team."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            team = await self._storage.get_team(
                self._user_id,
                session.team_id,
            )
            if team is None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                "TeamDelete: team "
                                f"{session.team_id} no longer exists."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            if team.session_id != self._session_id:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                "TeamDelete: only the team leader "
                                "can dissolve the team; this session "
                                "is a worker."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

            # Local import to avoid a circular dependency between
            # ``_tools`` and ``_service`` at module load.
            from .._service import SessionService  # noqa: PLC0415

            session_service = SessionService(
                storage=self._storage,
                message_bus=self._message_bus,
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
