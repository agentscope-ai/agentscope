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
            "团队显示名称。用于用户识别团队，并显示在团队界面中。"
        ),
    )
    description: str = Field(
        description=(
            "团队的用途——其总体目标或共享上下文。"
            "这会成为团队的章程，并注入每位成员的系统提示词中，"
            "使所有成员对团队存在的原因有一致的高层理解。"
        ),
    )


class TeamCreate(_TeamToolBase):
    """Create a new team and become its leader."""

    name: str = "TeamCreate"

    description: str = """以你当前的会话为领导创建一个新团队，并返回其团队 ID。

## 何时使用该工具
当你收到的任务最适合拆分为多个由专业化智能体（成员）在你协调下 \
并行执行的子任务时，使用该工具。创建团队后，使用 ``AgentCreate`` \
为每位成员配置各自的角色、提示词和权限模式来创建成员。注意：\
你传给 ``AgentCreate`` 的 ``prompt`` 会自动送达该成员，因此**不要**\
在 ``AgentCreate`` 之后紧接着调用 ``TeamSay``——只需等待成员汇报即可。

## 何时不要使用该工具
- 任务足够简单，你可以自行处理。
- 你在本次会话中已经领导一个团队——一个会话一次只能领导一个团队。
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
                            f"团队 {team.id}（{team.data.name}）已创建。"
                            f"你是团队负责人。使用AgentCreate添加成员，"
                            f"然后用TeamSay协调他们。"
                        ),
                    ),
                ],
            )
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[TextBlock(text=f"TeamCreate failed: {e}")],
                state=ToolResultState.ERROR,
            )
