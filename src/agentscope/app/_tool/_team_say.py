# -*- coding: utf-8 -*-
"""The TeamSay tool — sends a message to one or all team members."""
import json
from typing import Any

from pydantic import Field

from ._constants import HANDLE_LEN
from ._team_tool_base import _TeamToolBase
from ..message_bus import MessageBusKeys
from .._bus_ops import enqueue_run_trigger
from ..storage._utils import _ensure_team_members
from ...message import HintBlock, TextBlock, ToolResultState
from ...tool import ToolChunk, ParamsBase


class _TeamSayParams(ParamsBase):
    """Parameters for :class:`TeamSay`."""

    content: str = Field(
        description=(
            "消息文本。纯自然语言；接收方会将其作为一条用户消息"
            "出现在其上下文中。"
        ),
    )
    to: str | None = Field(
        default=None,
        description=(
            "接收方成员名称。传入 ``null``（默认值）向团队中其他所有成员"
            "广播。若要指定某位成员，请使用该成员的名称。"
        ),
    )


_LEADER_DESCRIPTION = """向特定团队成员发送消息，或向所有成员广播。

## 何时使用该工具
- 将用户提出的**新**需求或新上下文传递给特定成员。
- 向所有成员广播更新或协调消息。
- 在需要澄清时向某位成员追问后续问题。

## 何时不要使用该工具
- 不要反复调用该工具去检查某位成员的进度——成员完成任务时会通过 \
``TeamSay`` 自动通知你。请等待它们的消息，不要轮询。
- 不要在通过 ``AgentCreate`` 创建成员后立即调用该工具，成员会从 \
``AgentCreate`` 调用的 ``prompt`` 中收到初始任务，并在完成后汇报—— \
只需等待它们的消息。
- 当前会话尚不属于任何团队（请先调用 ``TeamCreate``）。
- 你想与自己对话——请使用你自己的推理。

## 重要
- 每位成员一经通过 AgentCreate 创建便会立即开始工作。成员完成任务后，\
会调用 ``TeamSay`` 向你汇报结果。你无需再提示它们——只需等待它们的回复。
- 除非你还有进一步的问题或需求，否则**不要**回复成员的工作汇报消息。\
``TeamSay`` 用于协调，而不是闲聊——你的首要任务是完成整体任务。
"""

_WORKER_DESCRIPTION = """向团队领导者发送消息，或向所有团队成员广播。

## 何时使用该工具
- **重要**：当你完成分配的任务时，**必须**调用该工具将结果汇报给领导者。\
领导者正在等待你的汇报——发送汇报前不要结束你的回合。
- 分享阶段性发现，或向领导者寻求澄清。
- 广播其他成员可能需要的信息。

## 何时不要使用该工具
- 你想与自己对话——请使用你自己的推理。
- 消息只是转瞬即逝的内心想法，对他人没有价值。
"""


class TeamSay(_TeamToolBase):
    """Send a message to a teammate (or broadcast to all teammates).

    Resolves the team membership at ``__call__`` time from storage,
    so a member added moments earlier in the same chat run is
    addressable immediately.

    The ``description`` shown to the agent differs by role: leaders
    are reminded not to poll members, workers are reminded to report
    results when done. The role is passed at construction time via
    the ``role`` parameter.
    """

    name: str = "TeamSay"
    description: str

    is_concurrency_safe: bool = True
    is_read_only: bool = True

    input_schema: dict = _TeamSayParams.model_json_schema()

    def __init__(
        self,
        *args: Any,
        role: str = "leader",
        **kwargs: Any,
    ) -> None:
        """Initialise with role-specific description.

        Args:
            role (`str`, defaults to ``"leader"``):
                Either ``"leader"`` or ``"worker"``. Determines which
                description the agent sees for this tool.
            *args:
                Forwarded to :class:`_TeamToolBase.__init__`.
            **kwargs:
                Forwarded to :class:`_TeamToolBase.__init__`.
        """
        super().__init__(*args, **kwargs)
        self.description = (
            _LEADER_DESCRIPTION if role == "leader" else _WORKER_DESCRIPTION
        )

    async def __call__(
        self,
        content: str,
        to: str | None = None,
    ) -> ToolChunk:
        """Deliver the message directly via storage + message bus.

        Reads the current session record from storage to resolve the
        team_id (the agent's team membership may have changed since
        agent assembly), builds the team's (agent_id, session_id)
        directory, and pushes a HintBlock + wakeup to each recipient.

        Args:
            content (`str`):
                Message body.
            to (`str | None`, defaults to ``None``):
                Display name of a specific team member to target, or
                ``None`` for broadcast.  Routing is by name (not
                agent id) so workers can address the leader by the
                name they see in ``<team-message from="...">``.
                Name uniqueness within a team is enforced at
                ``AgentCreate`` time.

        Returns:
            `ToolChunk`:
                A confirmation containing the recipient count, or an
                error chunk on failure.
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
                                "TeamSay: this session is not in any "
                                "team — call TeamCreate first if you "
                                "want to start one."
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
                                f"TeamSay: team {session.team_id} no longer "
                                f"exists."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

            leader_session = await self._storage.get_session(
                self._user_id,
                "",
                team.session_id,
            )
            if leader_session is None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                f"TeamSay: leader session "
                                f"{team.session_id} missing for team "
                                f"{team.id}."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

            # Build a (name -> (session_id, agent_id)) directory. The
            # leader is always in the directory under its plain agent
            # name; workers come from the team's members roster via
            # ``ensure_team_members`` (which migrates legacy
            # ``member_ids``-only records on first read). Invited
            # members display as ``"<name>@<agent_id[:8]>"`` so a
            # borrowed agent whose name collides with an already-created
            # member (or the leader) remains addressable.
            #
            # Uniqueness of the resulting display strings within a team
            # is preserved by the AgentCreate name check (which rejects
            # ``@``) and by AgentInvite's one-borrow-per-agent-per-team
            # rule.
            leader_agent = await self._storage.get_agent(
                self._user_id,
                leader_session.agent_id,
            )
            leader_name = (
                leader_agent.data.name
                if leader_agent is not None
                else leader_session.agent_id
            )
            directory: dict[str, tuple[str, str]] = {
                leader_name: (leader_session.id, leader_session.agent_id),
            }
            members = await _ensure_team_members(
                self._storage,
                self._user_id,
                team,
            )
            for member in members:
                member_agent = await self._storage.get_agent(
                    member.owner_id,
                    member.agent_id,
                )
                if member_agent is None:
                    continue
                if member.role == "invited":
                    display = (
                        f"{member_agent.data.name}"
                        f"@{member.agent_id[:HANDLE_LEN]}"
                    )
                else:
                    display = member_agent.data.name
                directory[display] = (member.session_id, member.agent_id)

            own_session_ids = {sid for sid, _aid in directory.values()}
            if self._session_id not in own_session_ids:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                f"TeamSay: this session "
                                f"({self._session_id}) is not part of "
                                f"team {team.id}."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

            if to is None:
                recipients: list[tuple[str, str]] = [
                    (sid, aid)
                    for sid, aid in directory.values()
                    if sid != self._session_id
                ]
            else:
                resolved = directory.get(to)
                if resolved is None:
                    known = sorted(directory.keys())
                    return ToolChunk(
                        content=[
                            TextBlock(
                                text=(
                                    f"TeamSay: no team member is named "
                                    f"{to!r}. Known members: {known}."
                                ),
                            ),
                        ],
                        state=ToolResultState.ERROR,
                    )
                target_session_id, target_agent_id = resolved
                if target_session_id == self._session_id:
                    return ToolChunk(
                        content=[
                            TextBlock(
                                text=(
                                    "TeamSay: cannot send a message to "
                                    "yourself; talk to yourself in your "
                                    "own reasoning instead."
                                ),
                            ),
                        ],
                        state=ToolResultState.ERROR,
                    )
                recipients = [(target_session_id, target_agent_id)]

            # Resolve sender display name once.
            sender_agent = await self._storage.get_agent(
                self._user_id,
                self._agent_id,
            )
            sender_name = (
                sender_agent.data.name
                if sender_agent is not None
                else self._agent_id
            )

            hint = HintBlock(
                hint=(
                    f'<team-message from="{sender_name}">\n'
                    f"{content}\n"
                    f"</team-message>"
                ),
                source=json.dumps(
                    {"label": "team", "sublabel": sender_name},
                    ensure_ascii=False,
                ),
            )
            payload = hint.model_dump(mode="json")

            for sid, aid in recipients:
                await self._message_bus.queue_push(
                    MessageBusKeys.inbox(sid),
                    payload,
                )
                await enqueue_run_trigger(
                    self._message_bus,
                    user_id=self._user_id,
                    session_id=sid,
                    agent_id=aid,
                )

            count = len(recipients)
            target = "broadcast" if to is None else f"member {to!r}"
            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Delivered to {count} recipient(s) "
                            f"({target})."
                        ),
                    ),
                ],
            )
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[TextBlock(text=f"TeamSay failed: {e}")],
                state=ToolResultState.ERROR,
            )
