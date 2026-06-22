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
    # Run-trigger queue — the discriminator carried by each entry on the
    # shared trigger queue, telling the dispatcher how to spawn the run.
    # Centralised here (rather than on ``MessageBus``) so the bus stays
    # free of business vocabulary.
    # ------------------------------------------------------------------

    WAKEUP_KIND_WAKE = "wake"
    """Trigger kind: wake an *idle* session to drain pending inbox
    content. The dispatcher spawns the run with ``input_msg=None`` and
    skips the session entirely while it is already running."""

    WAKEUP_KIND_RESUME = "resume"
    """Trigger kind: resume a session parked on an awaiting tool call by
    feeding it a human-in-the-loop result. The dispatcher spawns the run
    with the carried ``input`` event and — unlike ``wake`` — must *not*
    drop the entry while the session is running; it re-queues until the
    parked run releases its lock."""

    # ------------------------------------------------------------------
    # Cross-session UI projection — a generic per-session Redis-hash
    # store onto which one session can project UI cards owned by another
    # (e.g. a team member's pending HITL request projected onto its
    # leader). The ``kind`` prefix on each field lets a single target
    # session carry several independent projection feeds without key
    # collisions, so new projection features reuse this store rather
    # than minting their own.
    # ------------------------------------------------------------------

    _PROJECTION_NS = "agentscope:session:projection:{sid}"
    """Redis-hash namespace key template (per *target* session id)."""

    @classmethod
    def projection_namespace(cls, target_session_id: str) -> str:
        """Return the registry namespace for a session's projections.

        Args:
            target_session_id (`str`):
                The session the entries are projected onto (the session
                whose UI renders them).

        Returns:
            `str`:
                The Redis-hash namespace key.
        """
        return cls._PROJECTION_NS.format(sid=target_session_id)

    @staticmethod
    def projection_field(kind: str, entry_id: str) -> str:
        """Return the hash field key for a single projected entry.

        The ``kind`` prefix namespaces the field so different projection
        feeds sharing one target session never collide, and so a feed
        can be listed/purged by scanning for its prefix.

        Args:
            kind (`str`):
                The projection feed this entry belongs to (e.g.
                ``"subagent_hitl"``).
            entry_id (`str`):
                The entry's identity within the feed, unique per
                ``kind`` (e.g. ``"{worker_session_id}:{reply_id}"``).

        Returns:
            `str`:
                The hash field key, ``"{kind}:{entry_id}"``.
        """
        return f"{kind}:{entry_id}"

    @staticmethod
    def projection_field_prefix(kind: str) -> str:
        """Return the field-key prefix that identifies one feed.

        Used to filter :meth:`MessageBus.registry_getall` down to a
        single ``kind`` when listing or purging.

        Args:
            kind (`str`):
                The projection feed.

        Returns:
            `str`:
                The field-key prefix, ``"{kind}:"``.
        """
        return f"{kind}:"
