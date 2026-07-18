# -*- coding: utf-8 -*-
"""ChannelGateway — core orchestration for the channel module.

Responsibilities:
1. Per-user distributed lock (prevents concurrent-session conflicts).
2. Multi-agent routing (resolves agent_id from routing_rules).
3. Session acquisition/creation (based on DmScope).
4. Chat run triggering via ChatRunRegistry.
5. Response collection directly from MessageBus (no SSE).
"""
import asyncio
from datetime import datetime

from ...event import EventType, UserConfirmResultEvent, ConfirmResult
from ..._logging import logger
from ...message import UserMsg, ToolCallBlock
from ...permission import PermissionContext, PermissionMode
from ...state import AgentState
from ..message_bus import MessageBusKeys, MessageBus
from ..storage import (
    StorageBase,
    SessionConfig,
    SessionSource,
    ChatModelConfig,
)
from .._bus_ops import enqueue_run_trigger
from .._service import ChatService
from .._manager import ChatRunRegistry
from ._base import ChannelBase, ChannelEvent
from ._config import ChannelSessionDefaults
from ._session_mapper import SessionMapperBase, SessionMappingRecord
from ..storage import ChannelRecord

_LOCK_PREFIX = "agentscope:channel:user_lock:"

_TOOL_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EventType.TOOL_CALL_START,
        EventType.TOOL_CALL_DELTA,
        EventType.TOOL_CALL_END,
        EventType.TOOL_RESULT_START,
        EventType.TOOL_RESULT_TEXT_DELTA,
        EventType.TOOL_RESULT_DATA_DELTA,
        EventType.TOOL_RESULT_END,
    },
)

_THINKING_EVENT_TYPES: frozenset[str] = frozenset(
    {
        EventType.THINKING_BLOCK_START,
        EventType.THINKING_BLOCK_DELTA,
        EventType.THINKING_BLOCK_END,
    },
)


class ChannelGateway:
    """Core orchestration engine for channel events (data plane).

    Handles per-event processing: routing to agents, session management,
    chat run triggering, response collection, and HITL approval flows.
    Uses MessageBus distributed lock for per-user serialization so that
    multiple nodes in a cluster don't process events for the same user
    concurrently.

    See ``ChannelManager`` for the lifecycle/control plane counterpart.
    """

    def __init__(
        self,
        storage: StorageBase,
        message_bus: MessageBus,
        chat_service: ChatService,
        chat_run_registry: ChatRunRegistry,
        mapper: SessionMapperBase,
        session_config: ChannelSessionDefaults,
        response_timeout: float = 60.0,
        concurrent_users_limit: int = 1000,
    ) -> None:
        """Initialise the gateway with its runtime dependencies.

        Args:
            storage: App-level persistence for sessions, agents, etc.
            message_bus: Distributed pub/sub + locking for event streaming
                and per-user serialization across cluster nodes.
            chat_service: Service for triggering agent chat runs.
            chat_run_registry: Registry for spawning and tracking async
                chat run tasks (handles session-busy conflicts).
            mapper: Maps channel peer/chat identifiers to AgentScope
                session ids (``DmScope``-based).
            session_config: Module-level defaults for workspace_id and
                chat_model_config, used as fallback when creating sessions.
            response_timeout: Maximum seconds to wait for an agent reply
                before timing out.
            concurrent_users_limit: Per-node concurrency semaphore for
                simultaneous event processing.
        """
        self._storage = storage
        self._bus = message_bus
        self._chat_service = chat_service
        self._chat_run_registry = chat_run_registry
        self._mapper = mapper
        self._session_config = session_config
        self._timeout = response_timeout
        self._channels: dict[str, ChannelBase] = {}
        self._concurrent_limit = asyncio.Semaphore(concurrent_users_limit)

    def register_channel(self, channel: ChannelBase) -> None:
        """Register a channel instance for event dispatch."""
        self._channels[channel.channel_id] = channel

    def unregister_channel(self, channel_id: str) -> None:
        """Unregister a channel instance."""
        self._channels.pop(channel_id, None)

    def get_channel(self, channel_id: str) -> ChannelBase | None:
        """Return a registered channel instance by id, or None."""
        return self._channels.get(channel_id)

    def iter_channels(self) -> list[ChannelBase]:
        """Return all registered channel instances."""
        return list(self._channels.values())

    async def list_bot_chats(self, channel_id: str) -> list[dict]:
        """Fetch the bot's chat list from the platform for a channel."""
        channel = self._channels.get(channel_id)
        if channel is None:
            return []
        return await channel.list_bot_chats()

    # ── Public entry point ──

    async def handle_event(self, event: ChannelEvent) -> None:
        """Process an event with per-user distributed lock.

        The distributed lock (via MessageBus.acquire_lock) ensures that
        events for the same user are serialized even across cluster nodes.

        Note on TTL: The lock TTL is set to `response_timeout` as a
        best-effort first-pass guard. For approval flows that may exceed
        this duration (max_confirms × approval_timeout), the underlying
        heartbeat mechanism extends the lock while the holder is alive.
        If the lock expires (e.g., process crash), `_trigger_with_409_retry`
        provides a secondary serialization guarantee at the session level.
        """
        user_key = f"{event.channel_id}:{event.channel_user_id}"
        lock_key = f"{_LOCK_PREFIX}{user_key}"

        async with self._concurrent_limit:
            try:
                async with self._bus.acquire_lock(
                    lock_key,
                    ttl_secs=int(self._timeout),
                ):
                    await self._process_event(event)
            except (asyncio.TimeoutError, TimeoutError):
                logger.warning(
                    "Lock acquisition timed out for %s",
                    user_key,
                )
                channel = self._channels.get(event.channel_id)
                if channel:
                    await channel.send_response(
                        event,
                        "⚠️ Too many requests, please try again later.",
                    )
            except Exception:
                logger.exception(
                    "Error processing event for %s",
                    user_key,
                )
                channel = self._channels.get(event.channel_id)
                if channel:
                    await channel.send_response(
                        event,
                        "❌ Service error, please try again later.",
                    )

    # ── Core processing flow ──

    async def _process_event(self, event: ChannelEvent) -> None:
        channel = self._channels.get(event.channel_id)
        if channel is None:
            logger.error("No channel registered for id: %s", event.channel_id)
            return

        session_id = "unknown"
        reaction_id: str | None = None
        response_sent = False
        try:
            # Step 0: Add a "processing" reaction to acknowledge receipt
            reaction_id = await channel.add_reaction(event, "OnIt")

            # Step 1: Get ChannelRecord + resolve agent via routing rules
            channel_record = await self._storage.get_channel_by_id(
                event.channel_id,
            )
            if not channel_record:
                logger.error(
                    "No channel record for: %s",
                    event.channel_id,
                )
                return

            agent_id = self._resolve_agent_id(event, channel_record)

            # Record chat_id for future routing-rule lookups
            if event.chat_id:
                await self._mapper.record_chat_id(
                    event.channel_id,
                    event.chat_id,
                )

            # Step 2: Get or create session (DmScope-based)
            agentscope_user_id, session_id = await self._ensure_session(
                event,
                channel_record,
                agent_id,
            )

            # Step 3-6: Run and collect (may loop for approval flow)
            response = await self._run_and_collect(
                event=event,
                channel=channel,
                channel_record=channel_record,
                session_id=session_id,
                agentscope_user_id=agentscope_user_id,
                agent_id=agent_id,
            )

            # Step 7: Send back to channel
            await channel.send_response(event, response)
            response_sent = True

            # Step 8: Remove the "processing" reaction
            if reaction_id:
                await channel.remove_reaction(event, reaction_id)
                reaction_id = None

        except (asyncio.TimeoutError, TimeoutError):
            logger.warning(
                "Timeout for user %s session %s",
                event.channel_user_id,
                session_id,
            )
            await channel.send_response(
                event,
                "⏳ Agent response timed out, please try again later.",
            )

        except Exception as e:
            logger.exception("_process_event failed: %s", e)
            if not response_sent:
                await channel.send_response(
                    event,
                    "❌ Service error, please try again later.",
                )

        finally:
            if reaction_id:
                try:
                    await channel.remove_reaction(event, reaction_id)
                except Exception:
                    pass

    async def _run_and_collect(
        self,
        *,
        event: ChannelEvent,
        channel: ChannelBase,
        channel_record: ChannelRecord,
        session_id: str,
        agentscope_user_id: str,
        agent_id: str,
        max_confirms: int = 5,
    ) -> str:
        """Run the agent and collect response, handling approval loops.

        If the agent requests user confirmation, sends an interactive card
        to the channel, waits for the user's decision, resumes the agent,
        and continues collecting. Up to max_confirms approvals per request.
        """
        all_text_parts: list[str] = []
        confirms_count = 0
        event_key = MessageBusKeys.session_events(session_id)

        # Start first subscription + trigger initial run
        subscribe_ready = asyncio.Event()
        collect_task = asyncio.create_task(
            self._collect_from_bus(
                event_key,
                filter_tool_messages=channel_record.filter_tool_messages,
                filter_thinking_messages=(
                    channel_record.filter_thinking_messages
                ),
                ready_signal=subscribe_ready,
            ),
        )
        try:
            await asyncio.wait_for(subscribe_ready.wait(), timeout=5.0)
        except (asyncio.TimeoutError, TimeoutError):
            collect_task.cancel()
            raise

        run_task: asyncio.Task | None = await self._trigger_with_409_retry(
            event,
            session_id,
            agentscope_user_id,
            agent_id,
        )

        while confirms_count <= max_confirms:
            # Wait for collection or run completion
            wait_tasks = [collect_task]
            if run_task is not None:
                wait_tasks.append(run_task)

            done, _ = await asyncio.wait(
                wait_tasks,
                timeout=self._timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                collect_task.cancel()
                if run_task is not None:
                    run_task.cancel()
                raise TimeoutError("No response within timeout")

            if collect_task in done:
                text, confirm_data = collect_task.result()
                # Allow run_task a grace period to finish persistence
                # (upsert_message + update_session_state) before cleanup.
                # The reply text was already collected from the bus, but
                # ChatService._run_impl persists the reply Msg and updated
                # agent state AFTER the reply_stream ends — cancelling
                # too early loses that write.
                if run_task is not None:
                    if not run_task.done():
                        _, _ = await asyncio.wait(
                            [run_task],
                            timeout=10.0,
                        )
                        if not run_task.done():
                            logger.warning(
                                "run_task did not finish within grace "
                                "period, cancelling.",
                            )
                            run_task.cancel()
                    if run_task.done() and not run_task.cancelled():
                        exc = run_task.exception()
                        if exc is not None:
                            logger.error("Agent run task failed: %s", exc)
            elif run_task and run_task in done:
                # Log run_task exception if any
                if (
                    not run_task.cancelled()
                    and run_task.exception() is not None
                ):
                    logger.error(
                        "Agent run task failed: %s",
                        run_task.exception(),
                    )
                try:
                    text, confirm_data = await asyncio.wait_for(
                        collect_task,
                        timeout=3.0,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    return (
                        "❌ Agent encountered an error. Please check the "
                        "agent configuration."
                    )
            else:
                collect_task.cancel()
                if run_task is not None:
                    run_task.cancel()
                raise TimeoutError("No response within timeout")

            if text:
                all_text_parts.append(text)

            # No confirmation needed — done
            if confirm_data is None:
                break

            # ── Approval flow ──
            confirms_count += 1

            # CRITICAL: Subscribe BEFORE sending resume trigger to avoid
            # missing events from the resumed run.
            subscribe_ready = asyncio.Event()
            collect_task = asyncio.create_task(
                self._collect_from_bus(
                    event_key,
                    filter_tool_messages=channel_record.filter_tool_messages,
                    filter_thinking_messages=(
                        channel_record.filter_thinking_messages
                    ),
                    ready_signal=subscribe_ready,
                ),
            )
            try:
                await asyncio.wait_for(
                    subscribe_ready.wait(),
                    timeout=5.0,
                )
            except (asyncio.TimeoutError, TimeoutError):
                collect_task.cancel()
                raise

            approved = await self._handle_approval(
                event=event,
                channel=channel,
                confirm_data=confirm_data,
                session_id=session_id,
                agent_id=agent_id,
                agentscope_user_id=agentscope_user_id,
            )

            if approved is None:
                collect_task.cancel()
                all_text_parts.append("\n⌛ Tool approval timed out, aborted.")
                break

            # Both approve and deny trigger a resume (agent processes
            # the result either way). Continue collecting to see the
            # agent's next output (may be text or another tool call).
            if not approved:
                all_text_parts.append("\n🚫 User rejected tool execution.")

            logger.info(
                "Approval %s (round %d), waiting for resumed run...",
                "granted" if approved else "denied",
                confirms_count,
            )
            run_task = None

        result = "\n".join(all_text_parts).strip()
        logger.info(
            "run_and_collect done: parts=%d, confirms=%d, result_len=%d",
            len(all_text_parts),
            confirms_count,
            len(result),
        )
        return result or "(Agent returned no text content)"

    async def _handle_approval(
        self,
        *,
        event: ChannelEvent,
        channel: ChannelBase,
        confirm_data: dict,
        session_id: str,
        agent_id: str,
        agentscope_user_id: str,
        approval_timeout: float = 120.0,
    ) -> bool | None:
        """Send approval card and wait for user response.

        Returns True (approved), False (denied), or None (timeout).
        """
        tool_calls = confirm_data.get("tool_calls", [])
        reply_id = confirm_data.get("reply_id", "")
        if not tool_calls:
            return None

        first_call = tool_calls[0]
        tool_name = first_call.get("name", "unknown_tool")
        tool_input = first_call.get("input", "")
        request_id = confirm_data.get("id", "")

        # Build and send approval card
        card_json = channel.build_approval_card(
            request_id=request_id,
            tool_name=tool_name,
            tool_input_summary=tool_input[:500] if tool_input else "",
            session_id=session_id,
            agent_id=agent_id,
            user_id=agentscope_user_id,
        )

        logger.info(
            "Sending approval card: tool=%s request_id=%s",
            tool_name,
            request_id[:12],
        )
        card_msg_id = await channel.send_interactive_card(event, card_json)
        if not card_msg_id:
            logger.warning(
                "Failed to send approval card for tool=%s, auto-denying.",
                tool_name,
            )
            return None
        logger.info(
            "Approval card sent: msg_id=%s, waiting for user...",
            card_msg_id[:16],
        )

        # Register and wait for approval
        approval_fut = channel.register_approval(request_id)
        try:
            approved = await asyncio.wait_for(
                approval_fut,
                timeout=approval_timeout,
            )
        except (asyncio.TimeoutError, TimeoutError):
            # Clean up the pending approval to prevent memory leak
            channel.resolve_approval(request_id, False)
            # Update card to show timeout
            await channel.update_card(
                card_msg_id,
                channel.build_resolved_card(
                    tool_name=tool_name,
                    action="timeout",
                ),
            )
            return None

        # Build confirm results for all tool calls
        confirm_results = []
        for tc in tool_calls:
            confirm_results.append(
                ConfirmResult(
                    confirmed=approved,
                    tool_call=ToolCallBlock(**tc),
                ),
            )

        confirm_event = UserConfirmResultEvent(
            reply_id=reply_id,
            confirm_results=confirm_results,
        )

        # Enqueue resume trigger
        await enqueue_run_trigger(
            self._bus,
            user_id=agentscope_user_id,
            session_id=session_id,
            agent_id=agent_id,
            kind=MessageBusKeys.WAKEUP_KIND_RESUME,
            inputs=confirm_event,
        )

        return approved

    # ── Session acquisition/creation ──

    async def _ensure_session(
        self,
        event: ChannelEvent,
        channel_record: ChannelRecord,
        agent_id: str,
    ) -> tuple[str, str]:
        """Get existing session or create new one.

        Implements Strategy A: if the mapped session no longer exists in
        storage (externally deleted), the stale mapping is purged and a
        fresh session is created.

        To prevent orphaned sessions (e.g. after mapper data loss), we
        search for an existing session by channel naming convention before
        creating a new one.

        Returns (agentscope_user_id, session_id).
        """
        agentscope_user_id = self._build_agentscope_user_id(
            event,
            channel_record,
        )
        mapper_key = self._build_mapper_key(event, channel_record)

        existing = await self._mapper.get(event.channel_id, mapper_key)
        if existing:
            session_record = await self._storage.get_session(
                user_id=agentscope_user_id,
                agent_id=agent_id,
                session_id=existing,
            )
            if session_record is not None:
                await self._sync_session_permission(
                    agentscope_user_id,
                    agent_id,
                    existing,
                    channel_record,
                )
                return agentscope_user_id, existing
            # Session was externally deleted — purge stale mapping
            logger.warning(
                "Session %s not found in storage, recreating",
                existing,
            )
            await self._mapper.delete(event.channel_id, mapper_key)

        # Resolve chat_model_config: per-channel > global default
        model_cfg_dict = (
            channel_record.chat_model_config
            or self._session_config.chat_model_config
        )
        if not model_cfg_dict:
            raise RuntimeError(
                f"Channel '{event.channel_id}' has no chat_model_config "
                "and no global default is configured. Cannot create session.",
            )
        chat_model_config = ChatModelConfig(**model_cfg_dict)

        # Resolve fallback model config (optional)
        fallback_cfg_dict = channel_record.fallback_chat_model_config
        fallback_chat_model_config = (
            ChatModelConfig(**fallback_cfg_dict) if fallback_cfg_dict else None
        )

        session_name = f"channel:{event.channel_id}:{mapper_key}"

        # Search for an existing session with the same channel name to
        # prevent orphaned duplicates after mapper data loss.
        # NOTE: This is O(N) over all sessions for the agent — acceptable
        # because it only runs when the mapper has no entry (first message
        # or after data loss). The loop exits on the first match.
        existing_sessions = await self._storage.list_sessions(
            user_id=agentscope_user_id,
            agent_id=agent_id,
        )
        if len(existing_sessions) > 500:
            logger.warning(
                "Agent %s has %d sessions; channel session search "
                "may be slow. Consider cleanup of unused sessions.",
                agent_id,
                len(existing_sessions),
            )
        for sess in existing_sessions:
            if sess.config and sess.config.name == session_name:
                logger.info(
                    "Reusing existing channel session %s (mapper rebuilt)",
                    sess.id,
                )
                record = SessionMappingRecord(
                    channel_id=event.channel_id,
                    mapper_key=mapper_key,
                    agent_id=agent_id,
                    session_id=sess.id,
                    agentscope_user_id=agentscope_user_id,
                    created_at=datetime.now().isoformat(),
                    last_active_at=datetime.now().isoformat(),
                )
                await self._mapper.set_if_absent(
                    event.channel_id,
                    mapper_key,
                    sess.id,
                    record,
                )
                await self._sync_session_permission(
                    agentscope_user_id,
                    agent_id,
                    sess.id,
                    channel_record,
                )
                return agentscope_user_id, sess.id

        session_config = SessionConfig(
            workspace_id=self._session_config.workspace_id,
            chat_model_config=chat_model_config,
            fallback_chat_model_config=fallback_chat_model_config,
            name=session_name,
        )

        perm_mode = PermissionMode(channel_record.permission_mode)
        initial_state = AgentState(
            permission_context=PermissionContext(mode=perm_mode),
        )

        session_record = await self._storage.upsert_session(
            user_id=agentscope_user_id,
            agent_id=agent_id,
            config=session_config,
            state=initial_state,
            source=SessionSource.USER,
        )
        session_id = session_record.id

        record = SessionMappingRecord(
            channel_id=event.channel_id,
            mapper_key=mapper_key,
            agent_id=agent_id,
            session_id=session_id,
            agentscope_user_id=agentscope_user_id,
            created_at=datetime.now().isoformat(),
            last_active_at=datetime.now().isoformat(),
        )
        actual_session_id = await self._mapper.set_if_absent(
            event.channel_id,
            mapper_key,
            session_id,
            record,
        )

        # Clean up orphaned session if we lost the race
        if actual_session_id != session_id:
            try:
                await self._storage.delete_session(
                    user_id=agentscope_user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                )
            except Exception:
                pass

        return agentscope_user_id, actual_session_id

    async def _sync_session_permission(
        self,
        user_id: str,
        agent_id: str,
        session_id: str,
        channel_record: ChannelRecord,
    ) -> None:
        """Sync the session's permission mode with the channel config.

        Called on every event to ensure channel config changes are
        reflected in existing sessions.
        """
        session_record = await self._storage.get_session(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
        )
        if session_record is None:
            return

        desired_mode = PermissionMode(channel_record.permission_mode)
        current_state = session_record.state or AgentState()
        current_ctx = current_state.permission_context or PermissionContext()

        if current_ctx.mode != desired_mode:
            current_ctx.mode = desired_mode
            current_state.permission_context = current_ctx
            session_record.state = current_state
            await self._storage.upsert_session(
                user_id=user_id,
                agent_id=agent_id,
                config=session_record.config,
                state=session_record.state,
                session_id=session_id,
            )
            logger.info(
                "Synced session %s permission to %s",
                session_id,
                desired_mode.value,
            )

    def _build_agentscope_user_id(
        self,
        _event: ChannelEvent,
        channel_record: ChannelRecord,
    ) -> str:
        """Return the tenant user_id that owns this channel.

        Storage is multi-tenant — agents, sessions, and configs are all
        scoped by the *real* user_id (tenant). The channel owner's
        tenant_user_id is used so that the agent and any shared configs
        are accessible. Session isolation between different channel peers
        is achieved via unique session_ids (through the DmScope-based
        mapper key), NOT by fabricating synthetic user_ids.
        """
        return channel_record.tenant_user_id or "system"

    def _build_mapper_key(
        self,
        event: ChannelEvent,
        channel_record: ChannelRecord,
    ) -> str:
        """Generate the SessionMapper lookup key based on DmScope."""
        uid = event.channel_user_id
        chat_id = event.chat_id
        scope = channel_record.dm_scope

        if scope == "MAIN":
            return "main"
        elif scope == "PER_CHAT":
            return chat_id or "main"
        elif scope == "PER_CHANNEL_PEER":
            return f"{chat_id}:{uid}" if chat_id else uid
        else:  # PER_PEER (default)
            return uid

    # ── Multi-agent routing ──

    def _resolve_agent_id(
        self,
        event: ChannelEvent,
        channel_record: ChannelRecord,
    ) -> str:
        """Match routing rules to determine agent_id.

        Rules are evaluated by priority (descending). Supports '*'
        wildcard in metadata_value. Falls back to default_agent_id.
        """
        rules = sorted(
            channel_record.routing_rules,
            key=lambda r: r.priority,
            reverse=True,
        )
        for rule in rules:
            value = event.metadata.get(rule.metadata_key)
            if value and (rule.metadata_value in ("*", value)):
                return rule.agent_id

        return channel_record.default_agent_id

    # ── Chat triggering ──

    async def _trigger_with_409_retry(
        self,
        event: ChannelEvent,
        session_id: str,
        agentscope_user_id: str,
        agent_id: str,
        max_wait: float = 30.0,
    ) -> asyncio.Task:
        """Trigger chat run with retry on session-busy conflict.

        Returns the spawned asyncio.Task so callers can monitor for
        early failures (e.g. agent not found).
        """
        deadline = asyncio.get_running_loop().time() + max_wait
        backoff = 1.0

        msg_content: list | str = event.content if event.content else ""

        while True:
            coro = self._chat_service.run(
                user_id=agentscope_user_id,
                session_id=session_id,
                agent_id=agent_id,
                input_msg=UserMsg(
                    name=event.channel_user_id,
                    content=msg_content,
                ),
            )
            try:
                task = self._chat_run_registry.spawn(
                    coro,
                    session_id=session_id,
                )
                return task
            except RuntimeError as exc:
                coro.close()
                if asyncio.get_running_loop().time() >= deadline:
                    raise TimeoutError(
                        f"Session {session_id} still running"
                        f" after {max_wait}s",
                    ) from exc
                logger.debug(
                    "Session %s busy, retrying in %.1fs",
                    session_id,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 5.0)

    # ── Response collection ──

    async def _collect_from_bus(
        self,
        event_key: str,
        filter_tool_messages: bool = False,
        filter_thinking_messages: bool = True,
        ready_signal: asyncio.Event | None = None,
    ) -> tuple[str, dict | None]:
        """Collect agent reply directly from the MessageBus.

        Returns (text, confirm_data) where confirm_data is not None if
        the agent requested user confirmation (REQUIRE_USER_CONFIRM).
        """
        text_parts: list[str] = []
        run_started = False
        confirm_data: dict | None = None

        def _on_ready() -> None:
            if ready_signal and not ready_signal.is_set():
                ready_signal.set()

        subscription = self._bus.subscribe(event_key, on_ready=_on_ready)

        async for raw_event in subscription:
            if hasattr(raw_event, "model_dump"):
                evt = raw_event.model_dump(mode="json")
            elif isinstance(raw_event, dict):
                evt = raw_event
            else:
                evt = {
                    "type": getattr(raw_event, "type", ""),
                    **raw_event.__dict__,
                }

            evt_type = evt.get("type", "")

            if evt_type == EventType.REPLY_START:
                run_started = True
                logger.debug("_collect_from_bus: REPLY_START received")
                continue

            if not run_started:
                logger.debug(
                    "_collect_from_bus: skipping (no REPLY_START): %s",
                    evt_type,
                )
                continue

            if filter_tool_messages and evt_type in _TOOL_EVENT_TYPES:
                continue

            if filter_thinking_messages and evt_type in _THINKING_EVENT_TYPES:
                continue

            if evt_type == EventType.THINKING_BLOCK_START:
                text_parts.append("\n💭 ")
            elif evt_type == EventType.THINKING_BLOCK_DELTA:
                text_parts.append(evt.get("delta", ""))
            elif evt_type == EventType.THINKING_BLOCK_END:
                text_parts.append("\n\n")
            elif evt_type == EventType.TOOL_CALL_START:
                tool_name = evt.get("tool_call_name", "")
                text_parts.append(f"\n🔧 Calling tool: {tool_name}\n")
            elif evt_type == EventType.TOOL_RESULT_TEXT_DELTA:
                text_parts.append(evt.get("delta", ""))
            elif evt_type == EventType.TOOL_RESULT_END:
                text_parts.append("\n")
            elif evt_type == EventType.TEXT_BLOCK_DELTA:
                text_parts.append(evt.get("delta", ""))
            elif evt_type == EventType.REPLY_END:
                logger.debug(
                    "_collect_from_bus: REPLY_END, text_len=%d",
                    len("".join(text_parts)),
                )
                break
            elif evt_type == EventType.EXCEED_MAX_ITERS:
                text_parts.append("\n⚠️ Maximum reasoning rounds reached.")
                break
            elif evt_type == EventType.REQUIRE_USER_CONFIRM:
                confirm_data = evt
                break

        text = "".join(text_parts).strip()
        return text, confirm_data
