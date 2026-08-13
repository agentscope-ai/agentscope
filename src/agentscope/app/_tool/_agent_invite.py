# -*- coding: utf-8 -*-
"""The AgentInvite tool — borrows an existing agent into the leader's team.

Unlike :class:`AgentCreate`, which spawns a brand-new worker
(``source='team'``) from a :class:`SubAgentTemplate`, this tool
**borrows** a pre-existing user-owned agent by minting a fresh
team-scoped :class:`SessionRecord` on top of the *existing*
:class:`AgentRecord`.  The borrowed agent keeps its system prompt,
context/react configs, workspace, MCP, skills, and model choice; only
a new session is created so it can hold a parallel conversation for
the team.

When the team is dissolved or the leader is deleted, only the borrowed
session is cleaned up — the underlying :class:`AgentRecord` survives
so the user can still use the agent stand-alone.
"""
from __future__ import annotations

import copy
import json
from typing import TYPE_CHECKING

from pydantic import Field

from ._constants import HANDLE_LEN
from ._team_tool_base import _TeamToolBase
from ..message_bus import MessageBusKeys
from .._bus_ops import enqueue_run_trigger
from ..storage import SessionConfig, TeamMember
from ..storage._utils import _ensure_team_members
from ...message import HintBlock, TextBlock, ToolResultState
from ...state import AgentState
from ...tool import ToolChunk, ParamsBase
from ..._utils._common import _generate_id

if TYPE_CHECKING:
    from ..message_bus import MessageBus
    from ..storage import AgentRecord, StorageBase
    from ..workspace_manager import WorkspaceManagerBase


def _display_handle(agent_id: str) -> str:
    """Return the derived routing handle for ``agent_id``.

    Kept as a tiny helper (rather than inlining ``agent_id[:HANDLE_LEN]``
    at every call site) so a future change to how a handle is derived
    (widening the prefix, hashing, etc.) has exactly one place to touch.
    The length itself lives in :mod:`_constants` so ``TeamSay``'s
    parser agrees byte-for-byte with what this producer emits.
    """
    return agent_id[:HANDLE_LEN]


def _display_name(agent_name: str, agent_id: str) -> str:
    """Format the leader-facing display / routing string.

    Example: ``"Monday@9f3c1a20"``. Used by both :class:`AgentInvite`
    (as the ``target`` enum values) and :func:`TeamSay` (as the
    directory keys for invited members).
    """
    return f"{agent_name}@{_display_handle(agent_id)}"


class _AgentInviteParams(ParamsBase):
    """Parameters for :class:`AgentInvite`.

    ``target`` and ``prompt`` are the only inputs — the borrowed
    agent's name, system prompt, and configuration all come from its
    existing :class:`AgentRecord`, so there is nothing else for the
    leader LLM to override.
    """

    target: str = Field(
        description=(
            "要借用的可邀请智能体，格式为"
            '``"<name>@<handle>"``（例如 ``"Monday@9f3c1a20"``）。'
            "从枚举值中选择——每个枚举值都是在组装工具列表时，"
            "根据用户当前可邀请的智能体填充的。"
        ),
    )
    prompt: str = Field(
        description=(
            "作为用户消息传递给被邀请智能体的第一个任务。"
            "它加入后会立即开始执行——不要告诉它等待进一步指示。"
            "请包含智能体自主工作所需的上下文、约束、交付物和截止时间。"
        ),
    )


_DESCRIPTION_HEADER = """将用户已有的智能体借用到你领导的团队中。

<如果你的工具集中没有``AgentCreate``，请忽略下面所有对它的引用。>

## 与``AgentCreate``的区别
- ``AgentCreate``会创建一个**与你共享工作区**的全新智能体，因此你们可以在
同一工作目录内协作处理文件。``AgentInvite``借用的则是**拥有自己工作区**的
已有智能体——根据用户的配置，两个工作区可能共享也可能不共享同一文件系统，
因此你交出的路径无法假定能在被邀请方一侧解析。
- 被邀请的智能体已有名称；你不能重命名它。

## 何时使用此工具
- 已存在一个用户自有智能体，其声明的用途正好符合你需要的角色——
直接复用它，而不是用``AgentCreate``创建新的成员。
- 你想通知或委派给某个特定领域专长的已有智能体。

## 何时不要使用此工具
- 没有合适的可邀请智能体——改用``AgentCreate``。
- 你需要为这个团队定制成员的system prompt或角色——被邀请成员的配置是
冻结的；如果需要按团队定制，请使用``AgentCreate``。
- 你目前没有领导任何团队。请先调用``TeamCreate``。

## 重要提示
- 不要假定被邀请的智能体与你共享文件系统。优先使用自包含的消息；
除非你已先验证双方都能看到，否则不要在消息中嵌入工作目录或文件路径。
- 如果（且仅当）任务确实需要双方共同操作同一个大型文件或复杂的项目目录，
请先使用``TeamSay``确认被邀请的智能体能否看到相同的文件（例如让它列出或
检查某个特定路径）。对于只涉及消息内容的任务，可以跳过这个握手步骤。
- ``TeamSay``是你与被邀请智能体之间的主要沟通渠道。
- ``TeamDelete``不会删除被邀请的智能体——只会清理团队范围的会话。
如果你多次把同一个智能体邀请进不同团队，它可能会保留早期合作中的
长期记忆或文件。

## 可邀请的智能体"""


class AgentInvite(_TeamToolBase):
    """Borrow one of the user's invitable agents into the current team.

    The tool is only attached to the leader's toolkit when the calling
    user has at least one agent with ``invitable=True`` and a non-empty
    ``invite_description`` — see the toolkit assembly logic in
    :func:`get_toolkit`. The invitable pool is captured as a **snapshot**
    at attachment time so the tool's ``input_schema`` can enumerate
    concrete targets; ``__call__`` re-fetches the target's record and
    re-checks invitability before minting the session, so a race
    between snapshot and call (e.g. the user just turned off the toggle)
    is caught cleanly.
    """

    name: str = "AgentInvite"
    # No leader state needed — the borrowed session starts from a
    # fresh PermissionContext(); nothing carries over from the leader.
    is_state_injected: bool = False

    description: str
    input_schema: dict

    def __init__(
        self,
        storage: "StorageBase",
        message_bus: "MessageBus",
        workspace_manager: "WorkspaceManagerBase",
        user_id: str,
        session_id: str,
        agent_id: str,
        invitable_pool: list["AgentRecord"],
    ) -> None:
        """Bind request-scoped identifiers plus the invitable pool snapshot.

        Args:
            storage (`StorageBase`):
                Application storage backend.
            message_bus (`MessageBus`):
                Application message bus.
            workspace_manager (`WorkspaceManagerBase`):
                Used to mint the workspace id for freshly-invited
                agents (see :meth:`__call__`).
            user_id (`str`):
                The owner user id.
            session_id (`str`):
                The calling session id.
            agent_id (`str`):
                The calling agent id.
            invitable_pool (`list[AgentRecord]`):
                Snapshot of currently-invitable agents. Must be
                non-empty — the caller skips construction otherwise.
        """
        super().__init__(
            storage,
            message_bus,
            workspace_manager,
            user_id,
            session_id,
            agent_id,
        )

        self._pool_by_id: dict[str, "AgentRecord"] = {
            a.id: a for a in invitable_pool
        }

        # Build enum + a human-readable per-target rundown for the LLM.
        enum_values = [
            _display_name(a.data.name, a.id) for a in invitable_pool
        ]
        target_lines = [
            f"- ``{_display_name(a.data.name, a.id)!r}`` — "
            f"{a.data.invite_config.invite_description}"
            for a in invitable_pool
        ]
        self.description = (
            _DESCRIPTION_HEADER + "\n" + "\n".join(target_lines) + "\n"
        )

        schema = copy.deepcopy(_AgentInviteParams.model_json_schema())
        schema["properties"]["target"]["enum"] = enum_values
        self.input_schema = schema

    async def __call__(
        self,
        target: str,
        prompt: str,
    ) -> ToolChunk:
        """Mint a team-scoped session on top of an existing agent record.

        Preconditions (all rechecked at call time against fresh storage
        reads):
        - Caller's session is in a team AND is that team's leader.
        - ``target`` is a well-formed ``"<name>@<handle>"`` string whose
          handle prefix-matches an agent id in the current invitable
          pool.
        - The matched agent is still ``invitable=True`` with a
          non-empty ``invite_description`` (guards against a
          toggle-off race between attachment and invocation).
        - The team does not already have this agent as a member (one
          borrow per agent per team).
        - The invited agent has at least one existing session to
          inherit ``workspace_id`` / ``chat_model_config`` from — a
          brand-new never-opened agent record is not invitable in
          practice because it has no runtime state to reuse.

        Args:
            target (`str`):
                The ``"<name>@<handle>"`` display string chosen from
                the enum.
            prompt (`str`):
                First task delivered as a user message to the invited
                agent.

        Returns:
            `ToolChunk`:
                A success message containing the invited agent's
                display name, or an error chunk on failure.
        """
        try:
            invited, resolve_err = _resolve_target(
                self._pool_by_id,
                target,
            )
            if resolve_err is not None:
                return _error(resolve_err)
            assert invited is not None  # narrows for mypy

            session = await self._storage.get_session(
                self._user_id,
                self._agent_id,
                self._session_id,
            )
            if session is None or session.team_id is None:
                return _error(
                    "AgentInvite: this session is not in any team — "
                    "call TeamCreate first.",
                )
            team = await self._storage.get_team(
                self._user_id,
                session.team_id,
            )
            if team is None:
                return _error(
                    f"AgentInvite: team {session.team_id} no longer "
                    f"exists.",
                )
            if team.session_id != self._session_id:
                return _error(
                    "AgentInvite: only the team leader can invite "
                    "members; this session is a worker.",
                )

            # Re-fetch fresh — the snapshot could be stale if the user
            # just toggled the invite off.
            fresh = await self._storage.get_agent(
                self._user_id,
                invited.id,
            )
            if (
                fresh is None
                or not fresh.data.invite_config.invitable
                or not (
                    fresh.data.invite_config.invite_description or ""
                ).strip()
            ):
                return _error(
                    f"AgentInvite: agent {invited.data.name!r} is no "
                    f"longer invitable.",
                )
            invited = fresh

            # Duplicate-borrow guard — one team, one borrow per agent.
            existing_members = await _ensure_team_members(
                self._storage,
                self._user_id,
                team,
            )
            if any(m.agent_id == invited.id for m in existing_members):
                return _error(
                    f"AgentInvite: agent {invited.data.name!r} is "
                    f"already a member of team "
                    f"{team.data.name!r}.",
                )

            # Leader session — needed for chat-model / workspace fallback
            # when the invited agent has no existing session, and for the
            # sender-name in the initial team-message hint.
            leader_session = await self._storage.get_session(
                self._user_id,
                "",
                team.session_id,
            )
            if leader_session is None:
                return _error(
                    f"AgentInvite: leader session {team.session_id} "
                    f"for team {team.id} is missing — team is in an "
                    f"inconsistent state.",
                )
            leader_agent = await self._storage.get_agent(
                self._user_id,
                leader_session.agent_id,
            )
            leader_name = (
                leader_agent.data.name
                if leader_agent is not None
                else leader_session.agent_id
            )

            # Prefer the invited agent's own primary session for
            # workspace + chat-model reuse: it already has any MCP /
            # skills / cache set up. Fall back to a freshly-generated
            # workspace id + the leader's chat model when the agent has
            # never been opened — the underlying workspace is created
            # lazily by the workspace manager on first chat, so a bare
            # id is enough.
            invited_sessions = await self._storage.list_sessions(
                self._user_id,
                invited.id,
            )
            if invited_sessions:
                primary = invited_sessions[0]
                borrowed_workspace_id = primary.config.workspace_id
                borrowed_chat_model = (
                    primary.config.chat_model_config
                    or leader_session.config.chat_model_config
                )
                borrowed_fallback_model = (
                    primary.config.fallback_chat_model_config
                    or leader_session.config.fallback_chat_model_config
                )
            else:
                borrowed_workspace_id = (
                    self._workspace_manager.assign_workspace_id(
                        user_id=self._user_id,
                        agent_id=invited.id,
                        session_id=_generate_id(),
                    )
                )
                borrowed_chat_model = leader_session.config.chat_model_config
                borrowed_fallback_model = (
                    leader_session.config.fallback_chat_model_config
                )

            # Permission context is NOT inherited from the leader.
            # PermissionContext.working_directories and allow/deny/ask
            # rules are anchored to the leader's workspace, which the
            # invited agent may not share (it has its own workspace_id).
            # Merging leader dirs / rules would advertise paths the
            # invited agent cannot reach and pull in user confirmations
            # granted against a different filesystem. Nor do we inherit
            # from the invited agent's own primary session — the
            # team-scoped conversation is a separate context; prior
            # "user approved X" state should not silently cross over.
            # HITL prompts in this team will re-confirm any sensitive
            # tool call.
            worker_state = AgentState(
                # permission_context defaults to a fresh
                # ``PermissionContext()``; tasks_context defaults to
                # empty. Both are the intended reset for the borrowed
                # session — the invited agent's main-session state
                # stays with its own session.
            )

            invited_display = _display_name(
                invited.data.name,
                invited.id,
            )
            invited_handle = _display_handle(invited.id)
            borrowed = await self._storage.upsert_session(
                user_id=self._user_id,
                agent_id=invited.id,
                config=SessionConfig(
                    workspace_id=borrowed_workspace_id,
                    name=f"team:{team.id}/invited:{invited_handle}",
                    chat_model_config=borrowed_chat_model,
                    fallback_chat_model_config=borrowed_fallback_model,
                ),
                state=worker_state,
            )
            await self._storage.set_session_team_id(
                self._user_id,
                borrowed.id,
                team.id,
            )

            team.data.members = [
                *existing_members,
                TeamMember(
                    owner_id=self._user_id,
                    agent_id=invited.id,
                    session_id=borrowed.id,
                    role="invited",
                ),
            ]
            await self._storage.upsert_team(self._user_id, team)

            hint = HintBlock(
                hint=(
                    "<system-reminder>你已被邀请加入一个名为 "
                    f"'{team.data.name}' 的团队，该团队由本会话中一个名为 "
                    f"'{leader_name}' 的 agent 领导。所有团队成员"
                    f"**只能**通过 `TeamSay` 工具进行沟通。"
                    f"当你完成分配的任务，或想与领导或团队成员沟通时，"
                    f"请使用 `TeamSay`。</system-reminder>\n"
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
            await self._message_bus.queue_push(
                MessageBusKeys.inbox(borrowed.id),
                hint.model_dump(mode="json"),
            )
            await enqueue_run_trigger(
                self._message_bus,
                user_id=self._user_id,
                session_id=borrowed.id,
                agent_id=invited.id,
            )

            return ToolChunk(
                content=[
                    TextBlock(
                        text=(
                            f"Invited {invited_display!r} into team "
                            f"{team.data.name!r}."
                        ),
                    ),
                ],
            )
        except Exception as e:  # pylint: disable=broad-except
            return ToolChunk(
                content=[TextBlock(text=f"AgentInvite failed: {e}")],
                state=ToolResultState.ERROR,
            )


def _resolve_target(
    pool_by_id: dict[str, "AgentRecord"],
    target: str,
) -> tuple["AgentRecord | None", str | None]:
    """Parse a ``"<name>@<handle>"`` string and look up the pool entry.

    Matches on **both** the name part and the handle so that two
    invitable agents sharing an 8-char UUID4 prefix are still
    disambiguated by the LLM-supplied name. Falls back to a
    handle-only lookup when exactly one pool entry matches the handle,
    which keeps the common single-agent case working. If two or more
    entries share the handle AND the name does not narrow the match,
    an ``ambiguous`` error is returned so the caller can retry.

    Returns ``(record, None)`` on success or ``(None, error_message)``
    on any parse or resolution failure.
    """
    if "@" not in target:
        return None, (
            f"AgentInvite: malformed target {target!r} — expected "
            f'"<name>@<handle>", got no ``@`` separator.'
        )
    name_part, handle = target.rsplit("@", 1)
    name_part = name_part.strip()
    handle = handle.strip()
    if not handle:
        return None, (
            f"AgentInvite: malformed target {target!r} — empty handle "
            f"after ``@``."
        )
    handle_matches = [
        record
        for agent_id, record in pool_by_id.items()
        if _display_handle(agent_id) == handle
    ]
    # Preferred: unique (name, handle) match. Guards against the rare
    # 8-char-prefix collision on distinct agents with distinct names.
    named_matches = [r for r in handle_matches if r.data.name == name_part]
    if len(named_matches) == 1:
        return named_matches[0], None
    if len(named_matches) > 1:
        # Same name AND same handle prefix on multiple pool entries —
        # the display strings are indistinguishable, so no client
        # input could disambiguate. Surface the ids so the caller can
        # see what collided.
        ids = sorted(r.id for r in named_matches)
        return None, (
            f"AgentInvite: target {target!r} is ambiguous — multiple "
            f"invitable agents share this display string: {ids}."
        )
    # Fallback: no name match, but exactly one handle match — accept it.
    if len(handle_matches) == 1:
        return handle_matches[0], None
    if len(handle_matches) > 1:
        colliding = sorted(
            _display_name(r.data.name, r.id) for r in handle_matches
        )
        return None, (
            f"AgentInvite: handle {handle!r} matches multiple invitable "
            f"agents: {colliding}. Retry with the exact display string."
        )
    available = sorted(
        _display_name(a.data.name, a.id) for a in pool_by_id.values()
    )
    return None, (
        f"AgentInvite: no invitable agent matches target {target!r}. "
        f"Available: {available}."
    )


def _error(text: str) -> ToolChunk:
    """Build an ``ERROR``-state :class:`ToolChunk` with a text block.

    Not moved to :mod:`_team_tool_base` because that shape is specific
    to :class:`AgentInvite`'s branchy validation path — the other team
    tools currently inline the same pattern.
    """
    return ToolChunk(
        content=[TextBlock(text=text)],
        state=ToolResultState.ERROR,
    )
