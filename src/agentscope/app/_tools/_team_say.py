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
            "Recipient member name. Pass ``null`` (the default) to "
            "broadcast to every other member of the team. To address "
            "a specific peer use that member's name."
        ),
    )


_LEADER_DESCRIPTION = """Send a message to a specific team member or \
broadcast to all members.

## When to Use This Tool
- Pass **new** requirements or context from the user to a specific member.
- Broadcast an update or coordination message to all members.
- Ask a member a follow-up question when you need clarification.

## When NOT to Use This Tool
- DO NOT repeatedly call this to check on a member's progress — members \
will automatically notify you via ``TeamSay`` when they finish their task. \
Wait for their message instead of polling.
- DO NOT call this right after creating a member by ``AgentCreate``, the \
member will receive its initial task from the ``prompt`` of the \
``AgentCreate`` call and report back when done — just wait for their message. \
- The session is not in a team yet (call ``TeamCreate`` first).
- You want to talk to yourself — use your own reasoning.

## Important
- Each member starts working immediately when created via AgentCreate. \
When a member finishes its task, it will call ``TeamSay`` to report results \
back to you. You do NOT need to prompt them — just wait for their reply.
- **DO NOT** reply to a member's report message unless you have further \
questions or requirements. ``TeamSay`` is for coordination, not chit-chat — \
your top priority is to complete the overall task.
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

            # Build a (name -> (session_id, agent_id)) directory in one
            # pass over the team. Routing is by **name** rather than
            # agent_id so workers can address the leader (they receive
            # the leader's name in the <team-message from="..."> hint
            # but never see the leader's agent_id). Uniqueness of names
            # within the team is enforced at AgentCreate time.
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
            for worker_agent_id in team.data.member_ids:
                worker_agent = await self._storage.get_agent(
                    self._user_id,
                    worker_agent_id,
                )
                if worker_agent is None:
                    continue
                sessions = await self._storage.list_sessions(
                    self._user_id,
                    worker_agent_id,
                )
                if sessions:
                    directory[worker_agent.data.name] = (
                        sessions[0].id,
                        worker_agent_id,
                    )

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
                source=sender_name,
            )
            payload = hint.model_dump(mode="json")

            for sid, aid in recipients:
                await self._message_bus.inbox_push(sid, payload)
                await self._message_bus.enqueue_wakeup(
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
