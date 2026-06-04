# -*- coding: utf-8 -*-
"""The TeamSay tool — sends a message to one or all team members."""
from typing import Any

from pydantic import Field

from ._team_tool_base import _TeamToolBase
from ...message import HintBlock, TextBlock, ToolResultState
from ...tool import ToolChunk, ParamsBase


class _TeamSayParams(ParamsBase):
    """Parameters for :class:`TeamSay`."""

    content: str = Field(
        description=(
            "The message text. Plain natural-language; the recipient "
            "sees it as a user message in its context."
        ),
    )
    to: str | None = Field(
        default=None,
        description=(
            "Recipient agent id. Pass ``null`` (the default) to "
            "broadcast to every other member of the team. To address "
            "a specific peer use the agent id you obtained from "
            "AgentCreate's result (or, for workers replying to the "
            "leader, the leader's agent id)."
        ),
    )


_LEADER_DESCRIPTION = """Send a message to a specific team member or \
broadcast to all members.

## When to Use This Tool
- Pass new requirements or context from the user to a specific member.
- Broadcast an update or coordination message to all members.
- Ask a member a follow-up question when you need clarification.

## When NOT to Use This Tool
- Do NOT repeatedly call this to check on a member's progress — members \
will automatically notify you via TeamSay when they finish their task. \
Wait for their message instead of polling.
- The session is not in a team yet (call TeamCreate first).
- You want to talk to yourself — use your own reasoning.

## Important
Each member starts working immediately when created via AgentCreate. \
When a member finishes its task, it will call TeamSay to report results \
back to you. You do NOT need to prompt them — just wait for their reply.
"""

_WORKER_DESCRIPTION = """Send a message to the team leader or broadcast to \
all team members.

## When to Use This Tool
- **IMPORTANT**: When you finish your assigned task, you MUST call this \
tool to report your results back to the leader. The leader is waiting \
for your report — do not end your turn without sending it.
- Share intermediate findings or ask the leader for clarification.
- Broadcast information that other members might need.

## When NOT to Use This Tool
- You want to talk to yourself — use your own reasoning.
- The message is a transient internal thought with no value to others.
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
                Specific member agent id to target, or ``None`` for
                broadcast.

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

            # Build a single (agent_id -> session_id) directory and its
            # inverse — one pass over team.member_ids.
            directory: dict[str, str] = {
                leader_session.agent_id: leader_session.id,
            }
            for worker_agent_id in team.data.member_ids:
                sessions = await self._storage.list_sessions(
                    self._user_id,
                    worker_agent_id,
                )
                if sessions:
                    directory[worker_agent_id] = sessions[0].id
            agent_id_by_session = {sid: aid for aid, sid in directory.items()}

            if self._session_id not in directory.values():
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
                recipient_session_ids = [
                    sid
                    for sid in directory.values()
                    if sid != self._session_id
                ]
            else:
                target_session_id = directory.get(to)
                if target_session_id is None:
                    return ToolChunk(
                        content=[
                            TextBlock(
                                text=(
                                    f"TeamSay: recipient {to!r} is not a "
                                    f"member of team {team.id}."
                                ),
                            ),
                        ],
                        state=ToolResultState.ERROR,
                    )
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
                recipient_session_ids = [target_session_id]

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
                source=sender_name,
            )
            payload = hint.model_dump(mode="json")

            for sid in recipient_session_ids:
                await self._message_bus.inbox_push(sid, payload)
                await self._message_bus.enqueue_wakeup(
                    user_id=self._user_id,
                    session_id=sid,
                    agent_id=agent_id_by_session.get(sid, ""),
                )

            count = len(recipient_session_ids)
            target = "broadcast" if to is None else f"member {to}"
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
