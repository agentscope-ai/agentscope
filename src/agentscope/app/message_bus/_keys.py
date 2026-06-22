# -*- coding: utf-8 -*-
"""Centralised registry of message-bus key/namespace conventions used
by application-layer services.

:class:`~agentscope.app.message_bus.MessageBus` itself stays
domain-agnostic — it exposes only generic primitives
(``publish`` / ``subscribe`` / ``queue_*`` / ``log_*`` / ``registry_*``
/ ``acquire_lock``). All business-specific key formats live here so
they can be audited, migrated, and (eventually) ported off from the
current scattered ``_BASE_…_KEY`` constants on ``MessageBus``.

Add new business keys here as needed. As legacy keys are migrated off
``MessageBus``, they should move into this class as well.
"""


class MessageBusKeys:
    """Application-layer key conventions for the message bus."""

    # ------------------------------------------------------------------
    # Subagent HITL inbox — pending confirm-requests originating from
    # team-member sessions, projected onto their leader session so the
    # leader's UI can render and resolve them.
    # ------------------------------------------------------------------

    _SUBAGENT_HITL_NS = "agentscope:session:subagent_hitl:{sid}"
    """Redis-hash namespace key template (per *leader* session id)."""

    @classmethod
    def subagent_hitl_namespace(cls, leader_session_id: str) -> str:
        """Return the registry namespace for a leader session's pending
        subagent HITL requests.

        Args:
            leader_session_id (`str`):
                The leader session the pending requests are projected
                onto.

        Returns:
            `str`:
                The Redis-hash namespace key.
        """
        return cls._SUBAGENT_HITL_NS.format(sid=leader_session_id)

    @staticmethod
    def subagent_hitl_field(worker_session_id: str, reply_id: str) -> str:
        """Return the hash field key for a single pending HITL request.

        Args:
            worker_session_id (`str`):
                The worker session that emitted the HITL request.
            reply_id (`str`):
                The worker-side reply id the request belongs to.

        Returns:
            `str`:
                The hash field key, ``"{worker_session_id}:{reply_id}"``.
        """
        return f"{worker_session_id}:{reply_id}"
