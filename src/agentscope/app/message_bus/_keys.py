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

from typing import Final


class MessageBusKeys:  # pylint: disable=too-many-public-methods
    """Application-layer key conventions for the message bus.

    A flat registry of key/namespace builders — it grows one method per
    business key, so the public-method count is expected to be high.
    """

    # ------------------------------------------------------------------
    # Run-trigger queue — the discriminator carried by each entry on the
    # shared trigger queue, telling the dispatcher how to spawn the run.
    # Centralised here (rather than on ``MessageBus``) so the bus stays
    # free of business vocabulary.
    # ------------------------------------------------------------------

    WAKEUP_KIND_WAKE: Final = "wake"
    """Trigger kind: wake an *idle* session to drain pending inbox
    content. The dispatcher spawns the run with ``input_msg=None`` and
    skips the session entirely while it is already running."""

    WAKEUP_KIND_RESUME: Final = "resume"
    """Trigger kind: resume a session parked on an awaiting tool call by
    feeding it a human-in-the-loop result. The dispatcher spawns the run
    with the carried ``input`` event and — unlike ``wake`` — must *not*
    drop the entry while the session is running; it re-queues until the
    parked run releases its lock."""

    WAKEUP_KIND_MESSAGE: Final = "message"
    """Trigger kind: start a new turn from a genuine user ``Msg`` (e.g. an
    inbound channel message). The dispatcher spawns the run with the
    carried message as ``input_msg`` so it is persisted and reasoned over
    as a real user turn. Like ``resume`` (and unlike ``wake``) it carries
    input, so it is re-queued rather than dropped while the session is
    running."""

    WAKEUP_KIND_QUEUED_MESSAGE: Final = "queued_message"
    """Trigger kind: drain the next ordinary browser input from the
    session's FIFO turn queue. Busy or HITL-parked sessions defer this
    trigger until the current reply has fully finished."""

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

    # ------------------------------------------------------------------
    # Session event stream (replay log + live pub/sub)
    # ------------------------------------------------------------------

    _SESSION_EVENTS = "agentscope:session:events:{sid}"

    SESSION_REPLAY_MAX_LEN = 1000
    """Replay log length cap; older events are trimmed on append."""

    @classmethod
    def session_events(cls, session_id: str) -> str:
        """Replay log + live pub/sub channel key for a session."""
        return cls._SESSION_EVENTS.format(sid=session_id)

    # ------------------------------------------------------------------
    # Session run lock
    # ------------------------------------------------------------------

    _SESSION_LOCK = "agentscope:session:lock:{sid}"

    SESSION_RUN_TTL_SECS = 600
    """Default lock lease for a chat run (10 minutes)."""

    @classmethod
    def session_lock(cls, session_id: str) -> str:
        """Per-session distributed-lock key."""
        return cls._SESSION_LOCK.format(sid=session_id)

    # ------------------------------------------------------------------
    # Session inbox
    # ------------------------------------------------------------------

    _INBOX = "agentscope:inbox:{sid}"
    _INBOX_LOCK = "agentscope:inbox:lock:{sid}"
    _INBOX_CONSUMER = "agentscope:inbox:consumer:{sid}"

    INBOX_LOCK_TTL_SECS = 30
    """Lease for the inbox hand-off lock. The critical sections it
    guards are a single queue op plus a single registry op, so a lease
    this short only ever matters when a process dies inside one."""

    INBOX_CONSUMER_FIELD = "running"
    """Field name inside the per-session inbox-consumer registry."""

    @classmethod
    def inbox(cls, session_id: str) -> str:
        """Per-session inbox drain-queue key."""
        return cls._INBOX.format(sid=session_id)

    @classmethod
    def inbox_lock(cls, session_id: str) -> str:
        """Per-session lock serialising inbox hand-off.

        Held only around two tiny critical sections — the producer's
        "push then read consumer flag" and the consumer's "drain then
        clear consumer flag". Making those two mutually exclusive is
        what stops an entry pushed just as a run finishes from being
        both missed by that run and skipped by the producer's wake-up
        decision.
        """
        return cls._INBOX_LOCK.format(sid=session_id)

    @classmethod
    def inbox_consumer(cls, session_id: str) -> str:
        """Per-session registry recording whether a run is currently
        consuming this inbox.

        Deliberately **not** derived from
        :meth:`session_lock` — the lock is still held while a finished
        run persists its state, and during that window no further drain
        will happen, so producers must already treat the session as
        having no consumer.
        """
        return cls._INBOX_CONSUMER.format(sid=session_id)

    # ------------------------------------------------------------------
    # Session user-input queue
    # ------------------------------------------------------------------
    # These keys intentionally use ``session_id`` rather than ``user_id``.
    # This relies on storage assigning globally unique session ids and on
    # every public queue endpoint enforcing session ownership before access.
    # Revisit the key shape if a storage backend ever accepts caller-chosen
    # session ids.

    _CHAT_INPUTS = "agentscope:chat:inputs:{sid}"
    _CHAT_INPUT_DISPATCH_LOCK = "agentscope:chat:inputs:lock:{sid}"
    _CHAT_INPUT_MUTATION_LOCK = "agentscope:chat:inputs:mutation:{sid}"
    _CHAT_INPUT_PENDING_REGISTRY = "agentscope:chat:pending"
    _CHAT_INPUT_INFLIGHT_REGISTRY = "agentscope:chat:inflight"
    _CHAT_INPUT_RECOVERY_LOCK = "agentscope:chat:recovery:lock"
    _CHAT_INPUT_RECOVERY_STATE = "agentscope:chat:recovery:state"
    _CHAT_INPUT_USER_QUOTA_LOCK = "agentscope:chat:quota:lock:{uid}"

    CHAT_INPUT_MAX_LEN: Final = 100
    """Maximum number of pending ordinary user turns per session."""

    CHAT_INPUT_USER_MAX_LEN: Final = 500
    """Maximum pending/in-flight ordinary turns owned by one user."""

    CHAT_INPUT_MAX_BYTES: Final = 1_000_000
    """Maximum serialized size of one queued turn."""

    CHAT_INPUT_MUTATION_TTL_SECS: Final = 10
    """Crash lease for short queue mutations."""

    CHAT_INPUT_DISPATCH_TTL_SECS: Final = 30
    """Renewed claim-pump lease; bounds hard-crash queue stalls."""

    CHAT_INPUT_MUTATION_TIMEOUT_SECS: Final = 2.0
    """Maximum foreground wait for a queue mutation lock."""

    CHAT_INPUT_RECOVERY_LOCK_TIMEOUT_SECS: Final = 0.1
    """Maximum recovery-sweep wait for one session mutation lock."""

    @classmethod
    def chat_inputs(cls, session_id: str) -> str:
        """Return the FIFO key for ordinary user turns.

        Args:
            session_id (`str`):
                Session that owns the queue.

        Returns:
            `str`:
                Per-session pending-input queue key.
        """
        return cls._CHAT_INPUTS.format(sid=session_id)

    @classmethod
    def chat_input_dispatch_lock(cls, session_id: str) -> str:
        """Return the cross-process queue-dispatch mutex key.

        Args:
            session_id (`str`):
                Session whose queue drain must be serialized.

        Returns:
            `str`:
                Per-session dispatch-lock key.
        """
        return cls._CHAT_INPUT_DISPATCH_LOCK.format(sid=session_id)

    @classmethod
    def chat_input_mutation_lock(cls, session_id: str) -> str:
        """Return the short-lived queue-mutation mutex key.

        Args:
            session_id (`str`):
                Session whose pending queue may be changed.

        Returns:
            `str`:
                Per-session mutation-lock key.
        """
        return cls._CHAT_INPUT_MUTATION_LOCK.format(sid=session_id)

    @classmethod
    def chat_input_pending_registry(cls) -> str:
        """Return the registry key for sessions with pending input.

        Returns:
            `str`:
                Global pending-session registry key.
        """
        return cls._CHAT_INPUT_PENDING_REGISTRY

    @classmethod
    def chat_input_inflight_registry(cls) -> str:
        """Return the registry key for durably claimed turns.

        Returns:
            `str`:
                Global per-session in-flight claim registry key.
        """
        return cls._CHAT_INPUT_INFLIGHT_REGISTRY

    @classmethod
    def chat_input_user_quota_lock(cls, user_id: str) -> str:
        """Return the per-user pending-turn quota mutex key.

        Args:
            user_id (`str`):
                User whose aggregate quota check must be serialized.

        Returns:
            `str`:
                Per-user quota-lock key.
        """
        return cls._CHAT_INPUT_USER_QUOTA_LOCK.format(uid=user_id)

    @classmethod
    def chat_input_recovery_lock(cls) -> str:
        """Return the cluster-wide recovery-sweep mutex key.

        Returns:
            `str`:
                Recovery leadership lock key.
        """
        return cls._CHAT_INPUT_RECOVERY_LOCK

    @classmethod
    def chat_input_recovery_state(cls) -> str:
        """Return the recovery leadership metadata registry key.

        Returns:
            `str`:
                Registry key containing recovery owner and expiry fields.
        """
        return cls._CHAT_INPUT_RECOVERY_STATE

    # ------------------------------------------------------------------
    # Run trigger queue (wakeup / resume)
    # ------------------------------------------------------------------

    _WAKEUP_QUEUE = "agentscope:wakeups"
    _WAKEUP_SIGNAL = "agentscope:wakeup_signal"

    @classmethod
    def wakeup_queue(cls) -> str:
        """Shared run-trigger queue key."""
        return cls._WAKEUP_QUEUE

    @classmethod
    def wakeup_signal(cls) -> str:
        """Shared signal channel that nudges dispatchers to drain."""
        return cls._WAKEUP_SIGNAL

    # ------------------------------------------------------------------
    # Cross-process cancel
    # ------------------------------------------------------------------

    _SESSION_CANCEL = "agentscope:session:cancel"
    _TASK_CANCEL = "agentscope:task:cancel"
    _SESSION_INTERRUPT = "agentscope:session:interrupt"

    @classmethod
    def session_cancel_channel(cls) -> str:
        """Global session-cancel broadcast channel."""
        return cls._SESSION_CANCEL

    @classmethod
    def task_cancel_channel(cls) -> str:
        """Single-task cancel broadcast channel."""
        return cls._TASK_CANCEL

    @classmethod
    def session_interrupt_channel(cls) -> str:
        """Global session-interrupt broadcast channel.

        Used by the interrupt API endpoint to request graceful
        interruption of a running agent.  Separate from
        ``session_cancel_channel`` (which is used for hard-cancel on
        session deletion).
        """
        return cls._SESSION_INTERRUPT

    # ------------------------------------------------------------------
    # Background task registry
    # ------------------------------------------------------------------

    _BG_TASKS = "agentscope:bg_tasks:{sid}"

    BG_TASKS_TTL_SECS = 86400
    """Fallback TTL for the per-session BG task registry (24 h)."""

    @classmethod
    def bg_tasks(cls, session_id: str) -> str:
        """Per-session background task registry key."""
        return cls._BG_TASKS.format(sid=session_id)

    # ------------------------------------------------------------------
    # Knowledge-base indexing pipeline
    # ------------------------------------------------------------------

    _INDEX_TASKS_QUEUE = "agentscope:index:tasks"
    _INDEX_TASKS_SIGNAL = "agentscope:index:tasks:wake"

    @classmethod
    def index_tasks_queue(cls) -> str:
        """Shared, durable index-task queue.

        The producer-side
        :class:`~agentscope.app._service.KnowledgeBaseService`
        ``queue_push``\\ es here through
        :func:`~agentscope.app._bus_ops.enqueue_index_task`; the
        consumer-side
        :class:`~agentscope.app._service.IndexTaskConsumer`
        ``queue_drain``\\ s it on each signal — plus once eagerly on
        consumer start-up so tasks queued while every worker was down
        are picked up immediately.
        """
        return cls._INDEX_TASKS_QUEUE

    @classmethod
    def index_tasks_signal(cls) -> str:
        """Shared pub/sub channel that nudges every running index-task
        consumer to drain the queue.

        Payload is opaque — only its arrival matters. Redis pub/sub is
        fire-and-forget, so a published signal does not guarantee
        delivery; the durable queue keeps work safe in case every
        subscriber happens to be offline.
        """
        return cls._INDEX_TASKS_SIGNAL

    # ------------------------------------------------------------------
    # Channels. A reply never travels through the bus: delivery is plain
    # REST, so the node running the agent sends it directly. What does
    # cross nodes is coordination — reconcile nudges, the status
    # heartbeat that lets a connection-free replica answer, and the
    # per-chat buffers.
    # ------------------------------------------------------------------

    _CHANNEL_LIFECYCLE = "agentscope:channel:lifecycle"
    _CHANNEL_LIVENESS = "agentscope:channel:liveness:{cid}"
    _CHANNEL_MEDIA = "agentscope:channel:media:{cid}:{chat}:{uid}"
    _CHANNEL_SEEN_CHATS = "agentscope:channel:seen_chats:{cid}"

    @classmethod
    def channel_lifecycle(cls) -> str:
        """Pub/sub channel that nudges every node to reconcile its
        running channel instances against storage."""
        return cls._CHANNEL_LIFECYCLE

    @classmethod
    def channel_liveness(cls, channel_id: str) -> str:
        """Per-channel per-node status heartbeat namespace."""
        return cls._CHANNEL_LIVENESS.format(cid=channel_id)

    @classmethod
    def channel_media_buffer(
        cls,
        channel_id: str,
        chat_id: str,
        user_id: str,
    ) -> str:
        """Queue key buffering media until the next text message."""
        return cls._CHANNEL_MEDIA.format(
            cid=channel_id,
            chat=chat_id,
            uid=user_id,
        )

    @classmethod
    def channel_seen_chats(cls, channel_id: str) -> str:
        """Registry namespace of chat_ids the bot has been messaged in."""
        return cls._CHANNEL_SEEN_CHATS.format(cid=channel_id)
