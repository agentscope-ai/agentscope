# -*- coding: utf-8 -*-
"""The AGUI middleware class."""
from ag_ui.core.events import (
    BaseEvent as AGUIBaseEvent,
    CustomEvent as AGUICustomEvent,
    ReasoningMessageContentEvent as AGUIReasoningMessageContentEvent,
    ReasoningMessageEndEvent as AGUIReasoningMessageEndEvent,
    ReasoningMessageStartEvent as AGUIReasoningMessageStartEvent,
    RunErrorEvent as AGUIRunErrorEvent,
    RunFinishedEvent as AGUIRunFinishedEvent,
    RunStartedEvent as AGUIRunStartedEvent,
    StepFinishedEvent as AGUIStepFinishedEvent,
    StepStartedEvent as AGUIStepStartedEvent,
    TextMessageContentEvent as AGUITextMessageContentEvent,
    TextMessageEndEvent as AGUITextMessageEndEvent,
    TextMessageStartEvent as AGUITextMessageStartEvent,
    ToolCallArgsEvent as AGUIToolCallArgsEvent,
    ToolCallEndEvent as AGUIToolCallEndEvent,
    ToolCallResultEvent as AGUIToolCallResultEvent,
    ToolCallStartEvent as AGUIToolCallStartEvent,
)
from starlette.types import ASGIApp

from ._base import ProtocolMiddlewareBase
from ....event import (
    AgentEvent,
    DataBlockDeltaEvent,
    DataBlockEndEvent,
    DataBlockStartEvent,
    ExceedMaxItersEvent,
    ExternalExecutionResultEvent,
    ModelCallEndEvent,
    ModelCallStartEvent,
    ReplyEndEvent,
    ReplyStartEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    TextBlockDeltaEvent,
    TextBlockEndEvent,
    TextBlockStartEvent,
    ThinkingBlockDeltaEvent,
    ThinkingBlockEndEvent,
    ThinkingBlockStartEvent,
    ToolCallDeltaEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
    ToolResultDataDeltaEvent,
    ToolResultEndEvent,
    ToolResultStartEvent,
    ToolResultTextDeltaEvent,
    UserConfirmResultEvent,
)


class AGUIProtocolMiddleware(ProtocolMiddlewareBase):
    """The middleware that converts the AgentScope events into AGUI
    protocol."""

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the AGUI protocol middleware.

        Args:
            app: The ASGI application to wrap.
        """
        super().__init__(app)
        # Per-instance state; safe under typical single-stream usage
        # but not across concurrent requests.  Use contextvars if
        # concurrency is needed.
        self._last_model_name: str = "model_call"
        self._tool_result_buffers: dict[str, list[str]] = {}

    def _convert_to_protocol(self, event: AgentEvent) -> dict:
        """Convert the AgentScope events into AGUI protocol."""
        agui_event = self._to_agui_event(event)
        return agui_event.model_dump(
            mode="json",
            exclude_none=True,
            by_alias=True,
        )

    # pylint: disable=too-many-return-statements
    def _to_agui_event(  # noqa: C901
        self,
        event: AgentEvent,
    ) -> AGUIBaseEvent:
        """Convert an AgentScope event to an AGUI event."""

        if isinstance(event, ReplyStartEvent):
            return AGUIRunStartedEvent(
                thread_id=event.session_id,
                run_id=event.reply_id,
            )

        if isinstance(event, ReplyEndEvent):
            return AGUIRunFinishedEvent(
                thread_id=event.session_id,
                run_id=event.reply_id,
            )

        if isinstance(event, ExceedMaxItersEvent):
            return AGUIRunErrorEvent(
                message=(f"Agent '{event.name}' exceeded max iterations"),
                code="exceed_max_iters",
            )

        if isinstance(event, ModelCallStartEvent):
            self._last_model_name = event.model_name
            return AGUIStepStartedEvent(
                step_name=event.model_name,
            )

        if isinstance(event, ModelCallEndEvent):
            return AGUIStepFinishedEvent(
                step_name=self._last_model_name,
            )

        if isinstance(event, TextBlockStartEvent):
            return AGUITextMessageStartEvent(
                message_id=event.block_id,
            )

        if isinstance(event, TextBlockDeltaEvent):
            return AGUITextMessageContentEvent(
                message_id=event.block_id,
                delta=event.delta,
            )

        if isinstance(event, TextBlockEndEvent):
            return AGUITextMessageEndEvent(
                message_id=event.block_id,
            )

        # AGUI has a two-level reasoning structure (ReasoningStart/End wrapping
        # ReasoningMessage*), but _convert_to_protocol returns a single dict
        # per input event, so only the inner ReasoningMessage* events are
        # emitted.  Most AGUI consumers render correctly with message-level
        # events alone.
        if isinstance(event, ThinkingBlockStartEvent):
            return AGUIReasoningMessageStartEvent(
                message_id=event.block_id,
                role="reasoning",
            )

        if isinstance(event, ThinkingBlockDeltaEvent):
            return AGUIReasoningMessageContentEvent(
                message_id=event.block_id,
                delta=event.delta,
            )

        if isinstance(event, ThinkingBlockEndEvent):
            return AGUIReasoningMessageEndEvent(
                message_id=event.block_id,
            )

        if isinstance(event, ToolCallStartEvent):
            return AGUIToolCallStartEvent(
                tool_call_id=event.tool_call_id,
                tool_call_name=event.tool_call_name,
                parent_message_id=event.reply_id,
            )

        if isinstance(event, ToolCallDeltaEvent):
            return AGUIToolCallArgsEvent(
                tool_call_id=event.tool_call_id,
                delta=event.delta,
            )

        if isinstance(event, ToolCallEndEvent):
            return AGUIToolCallEndEvent(
                tool_call_id=event.tool_call_id,
            )

        if isinstance(event, ToolResultStartEvent):
            return AGUICustomEvent(
                name="tool_result_start",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, ToolResultTextDeltaEvent):
            self._tool_result_buffers.setdefault(
                event.tool_call_id,
                [],
            ).append(event.delta)
            return AGUICustomEvent(
                name="tool_result_text_delta",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, ToolResultDataDeltaEvent):
            return AGUICustomEvent(
                name="tool_result_data_delta",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, ToolResultEndEvent):
            content = "".join(
                self._tool_result_buffers.pop(event.tool_call_id, []),
            )
            return AGUIToolCallResultEvent(
                tool_call_id=event.tool_call_id,
                message_id=event.reply_id,
                content=content or str(event.state),
            )

        if isinstance(event, DataBlockStartEvent):
            return AGUICustomEvent(
                name="data_block_start",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, DataBlockDeltaEvent):
            return AGUICustomEvent(
                name="data_block_delta",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, DataBlockEndEvent):
            return AGUICustomEvent(
                name="data_block_end",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, RequireUserConfirmEvent):
            return AGUICustomEvent(
                name="require_user_confirm",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, RequireExternalExecutionEvent):
            return AGUICustomEvent(
                name="require_external_execution",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, UserConfirmResultEvent):
            return AGUICustomEvent(
                name="user_confirm_result",
                value=event.model_dump(exclude_none=True),
            )

        if isinstance(event, ExternalExecutionResultEvent):
            return AGUICustomEvent(
                name="external_execution_result",
                value=event.model_dump(exclude_none=True),
            )

        return AGUICustomEvent(
            name="unknown",
            value=event.model_dump(exclude_none=True),
        )
