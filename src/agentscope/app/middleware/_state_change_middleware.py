# -*- coding: utf-8 -*-
"""Middleware that detects agent state / team changes after each tool
call and pushes a :class:`CustomEvent` notification to the session's
event stream.

Two kinds of change are detected:

- **State change** — ``tasks_context`` or ``permission_context``
  modified (detected via separate hash comparisons). Checked both
  around each tool call (``on_acting``, for incremental updates during
  a turn) and around the whole reply (``on_reply``, to catch changes
  made outside the tool-execution window — e.g. permission rules added
  while handling a user confirmation). Pushes
  ``CustomEvent(name="state_updated", value={...})`` containing only
  the fields that changed.
- **Team change** — the tool that just ran is one of the team tools
  (``TeamCreate``, ``AgentCreate``, ``AgentInvite``, ``TeamDelete``).
  These tools directly mutate storage (``TeamRecord``,
  ``SessionRecord.team_id``), so we don't need to check storage; the
  fact that the tool ran is the trigger. Pushes
  ``CustomEvent(name="team_updated", value={})``.

Both events are published directly to the bus (via
``session_publish_event``) instead of being yielded through the agent's
event chain, because ``on_acting`` yields ``ToolChunk | ToolResponse``
— not ``AgentEvent``. The SSE ``/stream`` endpoint picks them up from
the bus like any other session event.
"""
import hashlib
from typing import Any, AsyncGenerator, Callable

from ..message_bus import MessageBus
from .._bus_ops import publish_session_event
from ...event import CustomEvent
from ...middleware import MiddlewareBase

_TEAM_TOOL_NAMES = frozenset(
    {"TeamCreate", "AgentCreate", "AgentInvite", "TeamDelete"},
)
# Tool names whose execution implies a team membership change.


class StateChangeMiddleware(MiddlewareBase):  # pylint: disable=abstract-method
    """Detect state / team changes after each tool call and push
    notifications to the session event stream.

    Args:
        message_bus (`MessageBus`):
            Used to publish ``CustomEvent`` to the session's event
            stream via :meth:`MessageBus.session_publish_event`.
        session_id (`str`):
            The session whose event stream to publish to.
    """

    def __init__(
        self,
        message_bus: MessageBus,
        session_id: str,
    ) -> None:
        """Initialise the middleware.

        Args:
            message_bus (`MessageBus`):
                Application message bus.
            session_id (`str`):
                The session id to publish events for.
        """
        self._bus = message_bus
        self._session_id = session_id

    @staticmethod
    def _state_hashes(agent: Any) -> tuple[str, str]:
        """Compute separate hashes of the state fields we track.

        Only ``tasks_context`` and ``permission_context`` are included;
        ``context`` (the message history) is intentionally excluded
        because it changes on every reasoning step and is not what
        this middleware cares about.

        Args:
            agent: The agent instance.

        Returns:
            `tuple[str, str]`: Hashes for ``tasks_context`` and
            ``permission_context``, respectively.
        """
        return (
            hashlib.md5(
                agent.state.tasks_context.model_dump_json().encode(),
            ).hexdigest(),
            hashlib.md5(
                agent.state.permission_context.model_dump_json().encode(),
            ).hexdigest(),
        )

    async def _publish_state_changes(
        self,
        agent: Any,
        hashes_before: tuple[str, str],
    ) -> None:
        """Publish the tracked state fields that changed.

        Args:
            agent: The agent instance whose state to publish.
            hashes_before (`tuple[str, str]`):
                Tracked state hashes captured before execution.
        """
        hashes_after = self._state_hashes(agent)
        value = {}
        if hashes_before[0] != hashes_after[0]:
            value["tasks_context"] = agent.state.tasks_context.model_dump(
                mode="json",
            )
        if hashes_before[1] != hashes_after[1]:
            value[
                "permission_context"
            ] = agent.state.permission_context.model_dump(
                mode="json",
            )
        if not value:
            return

        event = CustomEvent(
            name="state_updated",
            value=value,
        )
        await publish_session_event(
            self._bus,
            self._session_id,
            event.model_dump(mode="json"),
        )

    async def on_reply(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Wrap the whole reply turn to catch state changes that happen
        **outside** the ``on_acting`` tool-execution window.

        Permission rules added while handling a
        ``UserConfirmResultEvent`` (the user's "always allow" choice)
        mutate ``permission_context`` in ``_handle_incoming_event`` —
        which runs at the *start* of the reply turn, before the
        confirmed tool's ``on_acting`` snapshot is taken. ``on_acting``
        therefore sees no diff (the rule is already present in both its
        before- and after-hash) and never pushes. Snapshotting around
        the entire reply closes that gap.

        Args:
            agent: The executing agent.
            input_kwargs (`dict`):
                The reply inputs (new message(s) or a resumption event).
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core reply logic.

        Yields:
            ``AgentEvent | Msg`` — unchanged from downstream.
        """
        hashes_before = self._state_hashes(agent)

        async for item in next_handler(**input_kwargs):
            yield item

        await self._publish_state_changes(agent, hashes_before)

    async def on_acting(
        self,
        agent: Any,
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        """Wrap tool execution: snapshot state hash before, compare
        after, and push notifications if anything changed.

        Args:
            agent: The executing agent.
            input_kwargs (`dict`):
                Contains ``tool_call`` (``ToolCallBlock``).
            next_handler (`Callable[..., AsyncGenerator]`):
                The downstream middleware or core acting logic.

        Yields:
            ``ToolChunk | ToolResponse`` — unchanged from downstream.
        """
        tool_call = input_kwargs.get("tool_call")
        tool_name = tool_call.name if tool_call else ""

        hashes_before = self._state_hashes(agent)

        async for item in next_handler(**input_kwargs):
            yield item

        # Check 1: state fields changed?
        await self._publish_state_changes(agent, hashes_before)

        # Check 2: team tool ran?
        if tool_name in _TEAM_TOOL_NAMES:
            event = CustomEvent(
                name="team_updated",
                value={},
            )
            await publish_session_event(
                self._bus,
                self._session_id,
                event.model_dump(mode="json"),
            )
