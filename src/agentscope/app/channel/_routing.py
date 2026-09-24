# -*- coding: utf-8 -*-
"""Pure routing: resolve an inbound event to ``(agent_id, session_id)``.

There is no persisted channel→session mapping table. Given the routing
rules on the channel record, both the target agent and the session id
are computed deterministically from the event — so every node derives
the same result with zero coordination, and session creation is
idempotent (``get_or_create`` on a derived id).
"""
from uuid import NAMESPACE_URL, uuid5

from ..storage import ChannelRecord, ChannelBinding, SessionScope
from ._base import ChannelEvent


# Fixed namespace so derived session ids are stable across processes and
# restarts. Do not change — it would orphan every existing session.
_SESSION_NAMESPACE = uuid5(NAMESPACE_URL, "agentscope.channel.session")


def _binding_matches(event: ChannelEvent, binding: ChannelBinding) -> bool:
    """Whether ``binding`` matches ``event`` (``"*"`` matches anything).

    Args:
        event (`ChannelEvent`): The inbound event.
        binding (`ChannelBinding`): The rule; ``chat_id``/``user_id`` map
            to event fields, other keys to ``event.metadata``.
    """
    if binding.match_value == "*":
        return True
    if binding.match_key == "chat_id":
        value: str | None = event.chat_id
    elif binding.match_key == "user_id":
        value = event.channel_user_id
    else:
        raw = event.metadata.get(binding.match_key)
        value = str(raw) if raw is not None else None
    return value == binding.match_value


def resolve(
    event: ChannelEvent,
    record: ChannelRecord,
) -> tuple[str, str, SessionScope]:
    """Resolve an event to ``(agent_id, session_id, scope)`` via the first
    matching binding and a stable id from ``(channel, agent, scope_key)``.

    Args:
        event (`ChannelEvent`): The inbound event.
        record (`ChannelRecord`): The channel's configuration.

    Returns:
        `tuple[str, str, SessionScope]`: ``(agent_id, session_id, scope)``.
    """
    binding = record.routing.bindings[-1]
    for candidate in record.routing.bindings:
        if _binding_matches(event, candidate):
            binding = candidate
            break

    # Session scope projects the (chat_id, user_id) pair; PER_CHAT is
    # naturally per-user for a DM (its chat has only that user).
    if binding.session_scope is SessionScope.PER_CHAT_USER:
        scope_key = f"{event.chat_id}:{event.channel_user_id}"
    else:
        scope_key = event.chat_id

    session_id = str(
        uuid5(
            _SESSION_NAMESPACE,
            f"{record.id}:{binding.agent_id}:{scope_key}",
        ),
    )
    return binding.agent_id, session_id, binding.session_scope
