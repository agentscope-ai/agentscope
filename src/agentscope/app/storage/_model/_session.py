# -*- coding: utf-8 -*-
"""The session data class for storage."""
import warnings
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field, model_validator
from typing_extensions import deprecated

from ._base import _RecordBase
from ....state import AgentState


class SessionSource(str, Enum):
    """The kinds a session's source used to come in.

    Superseded by :data:`SessionOrigin`, whose tag carries the same
    values. Kept importable, and comparable to
    :attr:`SessionRecord.source`, so integrations pinned to the old name
    keep working while they move.
    """

    USER = "user"
    SCHEDULE = "schedule"
    CHANNEL = "channel"


class UserOrigin(BaseModel):
    """A session a person opened themselves."""

    type: Literal["user"] = "user"


class ScheduleOrigin(BaseModel):
    """A session a schedule opened on its due date."""

    type: Literal["schedule"] = "schedule"

    schedule_id: str
    """The schedule that created it."""


class ChannelOrigin(BaseModel):
    """A session an inbound platform message opened."""

    type: Literal["channel"] = "channel"

    channel_id: str
    """The owning channel. Lets the output forwarder locate the channel
    adapter and its presentation settings on a background or scheduled
    wake, where no inbound message is available to supply it."""

    chat_id: str
    """The platform chat this session maps to, so agent output can be
    delivered back to the right place."""

    chat_name: str | None = None
    """That chat's title when the platform supplied one. Recorded
    because the name arrives with the inbound message: a node that never
    holds the connection cannot look it up."""


class TeamOrigin(BaseModel):
    """A session a team minted for one of its members.

    Carries no team id on purpose. Which team a session belongs to is
    :attr:`SessionRecord.team_id`, which every reader already uses and
    which a session can also lose — this only records that a team is why
    the session exists at all, which nothing could tell before.
    """

    type: Literal["team"] = "team"


# How a session came to exist. Fixed when the session is created and
# never rewritten, which is what separates it from
# ``SessionRecord.team_id``: team membership is granted by a tool call
# inside an existing session and can be revoked, so it is a field of its
# own rather than a member of this union.
SessionOrigin = Annotated[
    Union[UserOrigin, ScheduleOrigin, ChannelOrigin, TeamOrigin],
    Field(discriminator="type"),
]


class ChatModelConfig(BaseModel):
    """The model configuration class."""

    type: str
    """The provider type."""

    credential_id: str
    """The credential id."""

    model: str
    """The model name."""

    parameters: dict
    """The model parameters."""


class TTSModelConfig(BaseModel):
    """The TTS model configuration class."""

    type: str
    """The provider type."""

    credential_id: str
    """The credential id."""

    model: str
    """The TTS model name."""

    parameters: dict
    """TTS parameters (voice, language, etc.)."""


class EmbeddingModelConfig(BaseModel):
    """Configuration for constructing an embedding model from a credential.

    Mirrors :class:`ChatModelConfig` but targets
    :class:`~agentscope.embedding.EmbeddingModelBase` subclasses.
    Used by :class:`KnowledgeBaseRecord` to persist the user's
    embedding model selection.
    """

    type: str
    """The provider type (e.g. ``"openai_credential"``)."""

    credential_id: str
    """The credential id to use for authentication."""

    model: str
    """The embedding model name (e.g. ``"text-embedding-3-small"``)."""

    dimensions: int = Field(..., gt=0)
    """The output embedding vector dimensions.

    Required and first-class — chosen at config-creation time and
    pinned to the resulting :class:`KnowledgeBaseRecord` so subsequent
    indexing / retrieval calls are dim-deterministic without any
    fallback lookup.
    """

    parameters: dict = Field(default_factory=dict)
    """The provider-specific non-dimensional parameters.

    Does **not** carry ``dimensions`` — that field is promoted to a
    top-level attribute above.
    """


class SessionKnowledgeConfig(BaseModel):
    """Session-level knowledge base attachment.

    Persists which knowledge bases the agent should retrieve from for
    this session and how the
    :class:`~agentscope.middleware.RAGMiddleware` should be
    configured.  ``parameters`` carries the user-tunable middleware
    fields verbatim (mirrors :attr:`ChatModelConfig.parameters`); the
    accepted keys and value types are described by
    :meth:`RAGMiddleware.Config.model_json_schema`.
    """

    knowledge_base_ids: list[str] = Field(default_factory=list)
    """Ids of the knowledge bases attached to this session.

    Empty list means no knowledge base is wired and the middleware is
    not installed.
    """

    parameters: dict = Field(default_factory=dict)
    """Middleware parameters keyed by ``RAGMiddleware``'s
    :class:`Config` model fields (``mode``, ``top_k``,
    ``score_threshold``, ``emit_hint_event``, ``persist_hint``,
    ``hint_template``).
    """


class SessionNaming(BaseModel):
    """How :attr:`SessionConfig.name` is maintained."""

    auto: bool = Field(
        default=False,
        description=(
            "Whether the server may still replace the session name with "
            "a title derived from the conversation."
        ),
    )
    """Whether the display name is the server's to choose.

    Set at creation for sessions created without an explicit name, and
    cleared as soon as the name is settled — either by the user renaming
    the session or by the generated title landing.

    Defaults to ``False``, which is what makes this safe to add to an
    existing deployment: records written before auto-naming existed carry
    no ``naming`` block at all, so they load with ``auto=False`` and keep
    whatever name they already have.
    """


class SessionConfig(BaseModel):
    """Session configuration — set at creation, updatable via PATCH."""

    workspace_id: str
    """Authoritative workspace binding for the session.

    Populated at session creation — either from an explicit
    ``workspace_id`` on ``CreateSessionRequest`` (used by team
    invite/borrow flows) or from
    :meth:`WorkspaceManagerBase.assign_workspace_id` under the
    manager's isolation policy. Consumed verbatim by chat,
    ``list_mcps``, and team tools; also the cache key for
    :meth:`WorkspaceManagerBase.get_workspace`."""

    name: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="Display name for the session.",
    )
    """The session display name."""

    naming: SessionNaming = Field(default_factory=SessionNaming)
    """Who owns :attr:`name`; see :class:`SessionNaming`."""

    cwd: str | None = Field(
        default=None,
        description=(
            "Directory the session is focused on — absolute, or "
            "relative to the workspace root. ``None`` means the root."
        ),
    )
    """The directory this session is currently focused on.

    Not confined to the workspace root, matching
    ``GET /workspace/directories``: on a sandboxed backend the
    reachable filesystem *is* the sandbox, and on a local one the
    caller is already trusted with the host. Nothing is validated on
    write — the value only names a place to look, so a path that has
    gone missing surfaces when something tries to read it rather than
    blocking the change.

    A relative value stays relative on purpose: the workspace root is
    backend-dependent (a host directory for :class:`LocalWorkspace`, a
    fixed in-sandbox path for the container backends) and only
    resolvable asynchronously, so denormalising it here would go stale
    the moment a session moves between backends. Resolve with
    ``backend.abspath(cwd, cwd=workspace.workdir)`` at the point of use
    — which handles both forms.

    Purely a viewing anchor: it does **not** change where ``Bash``,
    ``Glob`` or ``Grep`` execute.
    """

    chat_model_config: ChatModelConfig | None = None
    """The chat model config. None means no model has been configured yet."""

    fallback_chat_model_config: ChatModelConfig | None = None
    """The fallback chat model config. Used as a backup when the primary
    model fails. None means no fallback configured."""

    tts_model_config: TTSModelConfig | None = None
    """The TTS model config. None means TTS is not enabled."""

    knowledge_config: SessionKnowledgeConfig | None = None
    """Knowledge bases attached to this session and the corresponding
    :class:`~agentscope.middleware.RAGMiddleware` parameters.
    ``None`` means no knowledge base is wired."""


class SessionRecord(_RecordBase):
    """The session record."""

    user_id: str
    """The user id."""

    agent_id: str
    """The agent id."""

    origin: SessionOrigin = Field(default_factory=UserOrigin)
    """How this session came to exist.

    A tagged union rather than a bare kind plus a row of nullable ids:
    once the tag says ``channel`` the channel and chat ids are there,
    and no reader has to ask whether the combination makes sense.

    Named apart from the old ``source`` so that name could stay behind as
    a deprecated property — a rename in place would have turned every
    ``record.source == "user"`` into a silently false comparison.
    """

    team_id: str | None = None
    """The team this session participates in, if any.

    Team membership is session-level: a user agent can lead multiple teams
    across different sessions, and each worker session belongs to exactly
    one team. ``None`` means the session is not part of any team.
    """

    config: SessionConfig
    """Session configuration (workspace, name, model)."""

    @model_validator(mode="before")
    @classmethod
    def _fold_legacy_source(cls, data: Any) -> Any:
        """Read rows written before :data:`SessionOrigin` existed.

        Those carry a bare ``source`` string beside a set of nullable
        ``source_*`` ids. Nothing migrates them in the database; they are
        folded here and written back in the new shape on the next save.
        """
        if not isinstance(data, dict):
            return data
        if data.get("origin") is not None:
            return data

        data = dict(data)
        kind = data.pop("source", None) or "user"
        if not isinstance(kind, str):
            # Built the old way but with a new value — ``source=`` was
            # the field's name for long enough that callers still reach
            # for it, and silently dropping one would leave the session
            # claiming a user opened it.
            data["origin"] = kind
            return data
        schedule_id = data.get("source_schedule_id")
        channel_id = data.get("source_channel_id")
        chat_id = data.get("source_chat_id")
        # A tag carries its ids, so a legacy row missing them cannot be
        # given one — manufacturing a blank id would let it past every
        # guard the old nullable fields made callers write, and it would
        # be indexed under the empty string as well.
        if kind == "schedule" and schedule_id:
            data["origin"] = {
                "type": "schedule",
                "schedule_id": schedule_id,
            }
        elif kind == "channel" and channel_id and chat_id:
            data["origin"] = {
                "type": "channel",
                "channel_id": channel_id,
                "chat_id": chat_id,
                "chat_name": data.get("source_chat_name"),
            }
        elif kind in ("user", "team"):
            data["origin"] = {"type": kind}
        else:
            data["origin"] = {"type": "user"}
        for legacy in (
            "source_schedule_id",
            "source_channel_id",
            "source_chat_id",
            "source_chat_name",
        ):
            data.pop(legacy, None)
        return data

    @property
    @deprecated("Use ``origin.type`` instead.")
    def source(self) -> str:
        """The origin's tag, under the name it used to have."""
        return self.origin.type

    @property
    @deprecated("Use ``origin.schedule_id`` on a ``ScheduleOrigin``.")
    def source_schedule_id(self) -> str | None:
        """The schedule that created this session, if one did."""
        return (
            self.origin.schedule_id
            if isinstance(self.origin, ScheduleOrigin)
            else None
        )

    @property
    @deprecated("Use ``origin.channel_id`` on a ``ChannelOrigin``.")
    def source_channel_id(self) -> str | None:
        """The owning channel, if an inbound message created this."""
        return (
            self.origin.channel_id
            if isinstance(self.origin, ChannelOrigin)
            else None
        )

    @property
    @deprecated("Use ``origin.chat_id`` on a ``ChannelOrigin``.")
    def source_chat_id(self) -> str | None:
        """The platform chat this session serves, if any."""
        return (
            self.origin.chat_id
            if isinstance(self.origin, ChannelOrigin)
            else None
        )

    @property
    @deprecated("Use ``origin.chat_name`` on a ``ChannelOrigin``.")
    def source_chat_name(self) -> str | None:
        """That chat's title, when the platform supplied one."""
        return (
            self.origin.chat_name
            if isinstance(self.origin, ChannelOrigin)
            else None
        )

    state: AgentState = Field(default_factory=AgentState)
    """Mutable runtime state, updated after each chat turn."""


def _origin_kwargs(
    origin: SessionOrigin | None,
    source: str | None,
    schedule_id: str | None,
    channel_id: str | None,
    chat_id: str | None,
    chat_name: str | None,
) -> dict:
    """Build the ``SessionRecord`` keyword that says where a session came
    from, accepting either the union or the flat arguments it replaced.

    The flat ones are folded by the record's own legacy validator rather
    than here, so both entry points agree on what an incomplete set
    means.
    """
    if origin is not None:
        return {"origin": origin}
    if not any((source, schedule_id, channel_id, chat_id, chat_name)):
        return {"origin": UserOrigin()}
    warnings.warn(
        "The flat source arguments are deprecated; pass ``origin`` with "
        "a SessionOrigin instead.",
        DeprecationWarning,
        stacklevel=3,
    )
    return {
        "source": source or "user",
        "source_schedule_id": schedule_id,
        "source_channel_id": channel_id,
        "source_chat_id": chat_id,
        "source_chat_name": chat_name,
    }
