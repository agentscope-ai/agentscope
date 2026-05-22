# -*- coding: utf-8 -*-
"""TracingMiddleware and supporting utilities for OpenTelemetry tracing."""
import json
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Awaitable,
    Union,
    TypeVar,
    TYPE_CHECKING,
)

import aioitertools

from opentelemetry import trace as otel_trace
from opentelemetry.trace import StatusCode

from .._base import MiddlewareBase
from ...event import (
    ExternalExecutionResultEvent,
    RequireExternalExecutionEvent,
    RequireUserConfirmEvent,
    ReplyStartEvent,
)
from ...message import Msg, ToolCallBlock
from ...model import ChatModelBase

from ._attributes import SpanAttributes, OperationNameValues
from ._extractor import (
    _get_common_attributes,
    _get_agent_request_attributes,
    _get_agent_span_name,
    _get_agent_response_attributes,
    _get_llm_request_attributes,
    _get_llm_span_name,
    _get_llm_response_attributes,
    _get_tool_request_attributes,
    _get_tool_span_name,
    _get_tool_response_attributes,
)
from ._setup import _get_tracer
from ._utils import _serialize_to_str

if TYPE_CHECKING:
    from opentelemetry.trace import Span
    from ...agent import Agent
    from ...model import ChatResponse

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _check_tracing_enabled() -> bool:
    """Check if the OpenTelemetry tracer is initialised with a real SDK
    TracerProvider (i.e. ``setup_tracing`` was called).  Returns ``False``
    when only the default no-op proxy provider is active or when the SDK
    package is not installed.
    """
    try:
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return False

    return isinstance(otel_trace.get_tracer_provider(), TracerProvider)


def _set_span_success_status(span: "Span") -> None:
    """Set the span status to OK and end it."""
    span.set_status(StatusCode.OK)
    span.end()


def _set_span_error_status(span: "Span", e: BaseException) -> None:
    """Set the span status to ERROR, record the exception and end it."""
    span.set_status(StatusCode.ERROR, str(e))
    span.record_exception(e)
    span.end()


async def _trace_async_generator_wrapper(
    res: AsyncGenerator[T, None],
    span: "Span",
) -> AsyncGenerator[T, None]:
    """Wrap an async generator so that response attributes are captured from
    the last yielded chunk before the span is closed."""
    has_error = False

    try:
        last_chunk = None
        async for chunk in aioitertools.iter(res):
            last_chunk = chunk
            yield chunk

    except BaseException as e:
        has_error = True
        _set_span_error_status(span, e)
        raise

    finally:
        if not has_error:
            response_attributes = _get_llm_response_attributes(last_chunk)
            span.set_attributes(response_attributes)
            _set_span_success_status(span)


# ---------------------------------------------------------------------------
# TracingMiddleware
# ---------------------------------------------------------------------------


class TracingMiddleware(MiddlewareBase):
    """Agent middleware that adds OpenTelemetry tracing to the reply,
    model-call and tool-execution lifecycles.

    When tracing has not been configured (``setup_tracing`` was not called),
    every hook short-circuits to ``next_handler`` with near-zero overhead.

    Example::

        from agentscope.middleware import TracingMiddleware

        agent = Agent(
            ...,
            middlewares=[TracingMiddleware()],
        )
    """

    # ------------------------------------------------------------------
    # on_reply
    # ------------------------------------------------------------------
    async def on_reply(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        if not _check_tracing_enabled():
            async for item in next_handler(**input_kwargs):
                yield item
            return

        session_id = agent.state.session_id
        common_attrs = _get_common_attributes(session_id)

        tracer = _get_tracer()
        request_attributes = _get_agent_request_attributes(
            agent,
            input_kwargs,
        )
        span_name = _get_agent_span_name(request_attributes)

        with tracer.start_as_current_span(
            name=span_name,
            attributes={
                **request_attributes,
                **common_attrs,
            },
            end_on_exit=False,
        ) as span:
            # Synthetic execute_tool spans for externally executed tools
            event_arg = input_kwargs.get("inputs")
            if isinstance(event_arg, ExternalExecutionResultEvent):
                for result in event_arg.execution_results:
                    tool_attrs: dict[str, Any] = {
                        SpanAttributes.GEN_AI_OPERATION_NAME: (
                            OperationNameValues.EXECUTE_TOOL
                        ),
                        SpanAttributes.GEN_AI_TOOL_CALL_ID: result.id,
                        SpanAttributes.GEN_AI_TOOL_NAME: result.name,
                        SpanAttributes.AGENTSCOPE_IS_EXTERNAL_EXECUTION: (
                            True
                        ),
                        **common_attrs,
                    }
                    if result.output is not None:
                        tool_attrs[
                            SpanAttributes.GEN_AI_TOOL_CALL_RESULT
                        ] = _serialize_to_str(result.output)
                    with tracer.start_as_current_span(
                        name=(
                            f"{OperationNameValues.EXECUTE_TOOL}"
                            f" {result.name}"
                        ),
                        attributes=tool_attrs,
                    ):
                        pass

            has_error = False
            error_exc: BaseException | None = None
            last_msg: Msg | None = None
            hitl_pending: list[str] = []
            external_pending: list[str] = []
            observed_reply_id: str | None = None

            try:
                async for item in next_handler(**input_kwargs):
                    if isinstance(item, ReplyStartEvent):
                        observed_reply_id = item.reply_id
                    elif isinstance(item, RequireUserConfirmEvent):
                        hitl_pending.extend(t.name for t in item.tool_calls)
                    elif isinstance(item, RequireExternalExecutionEvent):
                        external_pending.extend(
                            t.name for t in item.tool_calls
                        )
                    if isinstance(item, Msg):
                        last_msg = item
                    yield item
            except BaseException as e:
                has_error = True
                error_exc = e
                raise
            finally:
                reply_id = observed_reply_id or agent.state.reply_id
                if reply_id:
                    span.set_attribute(
                        SpanAttributes.AGENTSCOPE_REPLY_ID,
                        reply_id,
                    )
                if hitl_pending:
                    span.set_attribute(
                        SpanAttributes.AGENTSCOPE_HITL_PENDING_TOOLS,
                        json.dumps(hitl_pending, ensure_ascii=False),
                    )
                if external_pending:
                    span.set_attribute(
                        SpanAttributes.AGENTSCOPE_EXTERNAL_EXECUTION_PENDING_TOOLS,  # noqa
                        json.dumps(external_pending, ensure_ascii=False),
                    )
                if has_error and error_exc is not None:
                    _set_span_error_status(span, error_exc)
                else:
                    if last_msg is not None:
                        span.set_attributes(
                            _get_agent_response_attributes(last_msg),
                        )
                    _set_span_success_status(span)

    # ------------------------------------------------------------------
    # on_model_call
    # ------------------------------------------------------------------
    async def on_model_call(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[
            ...,
            Awaitable["ChatResponse" | AsyncGenerator["ChatResponse", None]],
        ],
    ) -> Union["ChatResponse", AsyncGenerator["ChatResponse", None]]:
        if not _check_tracing_enabled():
            return await next_handler(**input_kwargs)

        model = input_kwargs.get("current_model")
        if not isinstance(model, ChatModelBase):
            return await next_handler(**input_kwargs)

        tracer = _get_tracer()

        combined_kwargs = {
            **getattr(model, "generate_kwargs", {}),
            **input_kwargs,
        }
        request_attributes = _get_llm_request_attributes(
            model,
            combined_kwargs,
        )
        span_name = _get_llm_span_name(request_attributes)

        with tracer.start_as_current_span(
            name=span_name,
            attributes={
                **request_attributes,
                **_get_common_attributes(agent.state.session_id),
            },
            end_on_exit=False,
        ) as span:
            try:
                result = await next_handler(**input_kwargs)

                if isinstance(result, AsyncGenerator):
                    return _trace_async_generator_wrapper(result, span)

                span.set_attributes(_get_llm_response_attributes(result))
                _set_span_success_status(span)
                return result

            except BaseException as e:
                _set_span_error_status(span, e)
                raise

    # ------------------------------------------------------------------
    # on_acting
    # ------------------------------------------------------------------
    async def on_acting(
        self,
        agent: "Agent",
        input_kwargs: dict,
        next_handler: Callable[..., AsyncGenerator],
    ) -> AsyncGenerator:
        if not _check_tracing_enabled():
            async for item in next_handler(**input_kwargs):
                yield item
            return

        tool_call = input_kwargs.get("tool_call")
        if not isinstance(tool_call, ToolCallBlock):
            async for item in next_handler(**input_kwargs):
                yield item
            return

        tracer = _get_tracer()

        request_attributes = _get_tool_request_attributes(
            agent.toolkit,
            tool_call,
        )
        span_name = _get_tool_span_name(request_attributes)

        with tracer.start_as_current_span(
            name=span_name,
            attributes={
                **request_attributes,
                **_get_common_attributes(agent.state.session_id),
            },
            end_on_exit=False,
        ) as span:
            has_error = False
            last_item = None
            try:
                async for item in next_handler(**input_kwargs):
                    last_item = item
                    yield item
            except BaseException as e:
                has_error = True
                _set_span_error_status(span, e)
                raise
            finally:
                if not has_error:
                    if last_item is not None:
                        span.set_attributes(
                            _get_tool_response_attributes(last_item),
                        )
                    _set_span_success_status(span)
