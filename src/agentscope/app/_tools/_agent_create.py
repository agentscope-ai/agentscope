# -*- coding: utf-8 -*-
"""The AgentCreate tool — spawns a worker into the current team."""
import json
from typing import Literal

from pydantic import Field

from ._team_tool_base import _TeamToolBase
from ..storage import AgentData, AgentRecord, SessionConfig
from ...agent import ContextConfig, ReActConfig
from ...message import HintBlock, TextBlock, ToolResultState
from ...permission import PermissionContext, PermissionMode
from ...state import AgentState
from ...tool import ToolChunk, ParamsBase


_PERMISSION_MODE_BY_VALUE: dict[str, PermissionMode] = {
    mode.value: mode for mode in PermissionMode
}


def _build_worker_system_prompt(
    team_name: str,
    team_description: str,
    member_name: str,
    member_description: str,
) -> str:
    """Compose the system prompt for a freshly spawned worker."""
    sections = [f"You are {member_name}, a member of team {team_name!r}."]
    if team_description:
        sections.append(f"Team purpose: {team_description}")
    if member_description:
        sections.append(f"Your role: {member_description}")
    sections.append(
        "You communicate with the team leader and other members "
        "through the TeamSay tool. Other tool calls execute in your "
        "own session. Speak on the team only when you have something "
        "external to share — your private reasoning stays private.",
    )
    return "\n\n".join(sections)


class _AgentCreateParams(ParamsBase):
    """Parameters for :class:`AgentCreate`."""

    name: str = Field(
        description=(
            'Short identifier for the new member, e.g. ``"researcher"`` '
            'or ``"coder-1"``. Other members address it via '
            "``TeamSay(to=<this name>)``, so it MUST be unique within "
            "the team."
        ),
    )
    description: str = Field(
        description=(
            "One-sentence summary of the member's role — e.g. "
            '``"Researches background information on the target topic"``. '
            "Becomes part of the member's system prompt so it understands "
            "its place in the team."
        ),
    )
    prompt: str = Field(
        description=(
            "The first task delivered to the member as a user message. "
            "The member begins executing immediately upon creation, so "
            "make this concrete and self-contained — do not just say "
            '``"wait for instructions"`` (use TeamSay later instead). '
            "Include any context, constraints, deliverables, and "
            "deadlines the member needs."
        ),
    )
    permission_mode: Literal[
        "default",
        "accept_edits",
        "explore",
        "bypass",
        "dont_ask",
    ] = Field(
        default="default",
        description=(
            "Permission mode controlling how the member handles tool "
            "calls that would otherwise require user confirmation.\n\n"
            "Choose based on the member's responsibilities:\n\n"
            '- ``"default"`` — Each tool call that touches the system '
            "(file writes, shell commands, etc.) requires confirmation. "
            "Pick this when the member's work has real-world side effects "
            "you want the user to review.\n"
            '- ``"accept_edits"`` — Auto-approve file edits and '
            "filesystem-shaping commands inside the working directory; "
            "still confirm other risky calls. Pick this for a member "
            "doing rapid iteration on code under your supervision.\n"
            '- ``"explore"`` — Read-only mode: allow Read/Grep/Glob, '
            "deny anything that mutates state. Pick this for research / "
            "audit / planning members that should never modify anything.\n"
            '- ``"bypass"`` — Skip every permission check. Pick this '
            "ONLY for fully sandboxed members where any operation is "
            "guaranteed safe (e.g. a containerised worker on disposable "
            "data).\n"
            '- ``"dont_ask"`` — Convert every ASK decision to DENY. '
            "Pick this for unattended/background members where the user "
            'isn\'t around to answer prompts; safer than ``"bypass"`` '
            "because risky calls fail-closed instead of executing.\n\n"
            'When unsure, start with ``"default"``.'
        ),
    )


class AgentCreate(_TeamToolBase):
    """Spawn a new worker member into the team you lead."""

    name: str = "AgentCreate"

    description: str = """Add a new member to the team you lead.

## When to Use This Tool
After ``TeamCreate``, call this for each member you want on the team. \
Each call:
- Creates a worker agent dedicated to this team.
- Delivers ``prompt`` as the worker's first user message — **the worker \
starts executing it immediately**. (So DONT use ``TeamSay`` right after \
creating one agent).

## When NOT to Use This Tool
- You're not currently leading a team. Call ``TeamCreate`` first.
- The new member would duplicate an existing member's role; reuse the \
existing member via ``TeamSay`` instead.

## Effects
- Use the ``name`` you chose as ``to=<name>`` in ``TeamSay`` to direct \
messages to this member specifically. Names must be unique within the \
team (including against the leader's name); duplicates are rejected.
- Members spawned this way live only as long as the team — they are \
deleted when ``TeamDelete`` is called.

## Important
- You are responsible for organising the team, assigning tasks, collecting \
every member's report, and producing the final answer — all members report \
directly to you. Therefore, **DO NOT** encourage members to communicate with \
each other, and **AVOID** creating "integrator"-style members; both make the \
overall communication topology unnecessarily complex.
"""

    input_schema: dict = _AgentCreateParams.model_json_schema()

    async def __call__(
        self,
        name: str,
        description: str,
        prompt: str,
        permission_mode: str = "default",
    ) -> ToolChunk:
        """Spawn the worker agent + session directly via storage.

        Reads the current session + team records from storage to
        enforce two preconditions: the calling session must be in a
        team, and it must be that team's leader.

        Args:
            name (`str`):
                Short identifier for the worker.
            description (`str`):
                One-sentence summary of the worker's role.
            prompt (`str`):
                First task delivered as a user message to the worker.
            permission_mode (`str`, defaults to ``"default"``):
                Permission mode the worker operates under.

        Returns:
            `ToolChunk`:
                A success message containing the new member id, or an
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
                                "AgentCreate: this session is not in "
                                "any team — call TeamCreate first."
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
                                "AgentCreate: team "
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
                                "AgentCreate: only the team leader "
                                "can add members; this session is a "
                                "worker."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

            # Look up leader session for chat-model inheritance + name.
            leader_session = await self._storage.get_session(
                self._user_id,
                "",  # agent_id unused at storage level
                team.session_id,
            )
            if leader_session is None:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                f"AgentCreate: leader session "
                                f"{team.session_id} for team {team.id} is "
                                f"missing — team is in an inconsistent "
                                f"state."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

            if permission_mode not in _PERMISSION_MODE_BY_VALUE:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                f"AgentCreate: unknown permission_mode "
                                f"{permission_mode!r}; expected one of "
                                f"{list(_PERMISSION_MODE_BY_VALUE)}."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )
            mode_enum = _PERMISSION_MODE_BY_VALUE[permission_mode]

            # Enforce team-scoped name uniqueness. TeamSay routes by
            # ``name`` (not agent_id), so duplicates would be ambiguous
            # and unaddressable. The leader's name participates too —
            # workers must not collide with it.
            leader_agent_record = await self._storage.get_agent(
                self._user_id,
                leader_session.agent_id,
            )
            existing_names: set[str] = set()
            if leader_agent_record is not None:
                existing_names.add(leader_agent_record.data.name)
            for member_id in team.data.member_ids:
                member_record = await self._storage.get_agent(
                    self._user_id,
                    member_id,
                )
                if member_record is not None:
                    existing_names.add(member_record.data.name)
            if name in existing_names:
                return ToolChunk(
                    content=[
                        TextBlock(
                            text=(
                                f"AgentCreate: a team member named "
                                f"{name!r} already exists. Member names "
                                f"must be unique within the team "
                                f"(including the leader's name); pick "
                                f"another."
                            ),
                        ),
                    ],
                    state=ToolResultState.ERROR,
                )

            # 1. Build worker AgentRecord (source="team" so it's hidden
            #    from the global agent list).
            system_prompt = _build_worker_system_prompt(
                team_name=team.data.name,
                team_description=team.data.description,
                member_name=name,
                member_description=description,
            )
            worker_agent = AgentRecord(
                user_id=self._user_id,
                source="team",
                data=AgentData(
                    name=name,
                    system_prompt=system_prompt,
                    context_config=ContextConfig(),
                    react_config=ReActConfig(),
                ),
            )
            await self._storage.upsert_agent(self._user_id, worker_agent)

            # 2. Build worker SessionRecord, inheriting leader's model
            #    config.
            worker_state = AgentState(
                permission_context=PermissionContext(mode=mode_enum),
            )
            worker_session = await self._storage.upsert_session(
                user_id=self._user_id,
                agent_id=worker_agent.id,
                config=SessionConfig(
                    workspace_id=leader_session.config.workspace_id,
                    name=f"team:{team.id}/{name}",
                    chat_model_config=(
                        leader_session.config.chat_model_config
                    ),
                    fallback_chat_model_config=(
                        leader_session.config.fallback_chat_model_config
                    ),
                ),
                state=worker_state,
            )
            await self._storage.set_session_team_id(
                self._user_id,
                worker_session.id,
                team.id,
            )

            # 3. Append worker to team.member_ids.
            team.data.member_ids = [
                *team.data.member_ids,
                worker_agent.id,
            ]
            await self._storage.upsert_team(self._user_id, team)

            # 4. Deliver the initial task to the worker's inbox + wakeup.
            leader_name = (
                leader_agent_record.data.name
                if leader_agent_record is not None
                else leader_session.agent_id
            )
            hint = HintBlock(
                hint=(
                    f'<team-message from="{leader_name}">\n'
                    f"{prompt}\n"
                    f"</team-message>"
                ),
                source=json.dumps(
                    {
                        "label": "team_message",
                        "sublabel": leader_name,
                    },
                    ensure_ascii=False,
                ),
            )
            await self._message_bus.inbox_push(
                worker_session.id,
                hint.model_dump(mode="json"),
            )
            await self._message_bus.enqueue_wakeup(
                user_id=self._user_id,
                session_id=worker_session.id,
                agent_id=worker_agent.id,
            )

            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Member {name!r} added to team "
                            f"{team.data.name!r}."
                        ),
                    ),
                ],
            )
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[TextBlock(text=f"AgentCreate failed: {e}")],
                state=ToolResultState.ERROR,
            )
