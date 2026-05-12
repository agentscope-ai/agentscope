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

from contextvars import ContextVar

import aioitertools

from ..middleware import MiddlewareBase
from ..message import Msg

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

    from ..agent import Agent
    from ..model import ChatResponse

T = TypeVar("T")

# Context variable to propagate the session ID from agent reply through
# all inner tracing spans (LLM calls, tool calls, formatter calls, etc.)
_current_session_id: ContextVar[str] = ContextVar(
    "_current_session_id",
    default="",
)


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
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return False

    return isinstance(otel_trace.get_tracer_provider(), TracerProvider)


def _set_span_success_status(span: "Span") -> None:
    """Set the span status to OK and end it."""
    from opentelemetry import trace as trace_api

    span.set_status(trace_api.StatusCode.OK)
    span.end()


def _set_span_error_status(span: "Span", e: BaseException) -> None:
    """Set the span status to ERROR, record the exception and end it."""
    from opentelemetry import trace as trace_api

    span.set_status(trace_api.StatusCode.ERROR, str(e))
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
            if (
                getattr(span, "attributes", {}).get(
                    SpanAttributes.GEN_AI_OPERATION_NAME,
                )
                == OperationNameValues.CHAT
            ):
                response_attributes = _get_llm_response_attributes(last_chunk)
            elif (
                getattr(span, "attributes", {}).get(
                    SpanAttributes.GEN_AI_OPERATION_NAME,
                )
                == OperationNameValues.EXECUTE_TOOL
            ):
                response_attributes = _get_tool_response_attributes(last_chunk)
            else:
                response_attributes = {}

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

        from agentscope.tracing import TracingMiddleware

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

        session_id = getattr(
            getattr(agent, "state", None),
            "session_id",
            "",
        )
        token = _current_session_id.set(session_id or "")

        try:
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
                    **_get_common_attributes(),
                },
                end_on_exit=False,
            ) as span:
                from ..event import (
                    ExternalExecutionResultEvent,
                    RequireExternalExecutionEvent as _RequireExtExec,
                    RequireUserConfirmEvent as _RequireUserConfirm,
                    ReplyStartEvent as _ReplyStart,
                )

                # Synthetic execute_tool spans for externally executed tools
                event_arg = input_kwargs.get("event")
                if isinstance(event_arg, ExternalExecutionResultEvent):
                    common_attrs = _get_common_attributes()
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
                        if isinstance(item, _ReplyStart):
                            observed_reply_id = item.reply_id
                        elif isinstance(item, _RequireUserConfirm):
                            hitl_pending.extend(
                                t.name for t in item.tool_calls
                            )
                        elif isinstance(item, _RequireExtExec):
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
                    reply_id = observed_reply_id or getattr(
                        getattr(agent, "state", None),
                        "reply_id",
                        None,
                    )
                    if reply_id:
                        span.set_attribute(
                            SpanAttributes.AGENTSCOPE_REPLY_ID,
                            reply_id,
                        )
                    if hitl_pending:
                        span.set_attribute(
                            SpanAttributes.AGENTSCOPE_HITL_PENDING_TOOLS,
                            json.dumps(hitl_pending),
                        )
                    if external_pending:
                        span.set_attribute(
                            SpanAttributes.AGENTSCOPE_EXTERNAL_EXECUTION_PENDING_TOOLS,  # noqa
                            json.dumps(external_pending),
                        )
                    if has_error and error_exc is not None:
                        _set_span_error_status(span, error_exc)
                    else:
                        if last_msg is not None:
                            span.set_attributes(
                                _get_agent_response_attributes(last_msg),
                            )
                        _set_span_success_status(span)
        finally:
            _current_session_id.reset(token)

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

        from ..model import ChatModelBase

        tracer = _get_tracer()
        model = input_kwargs.get("current_model")
        assert isinstance(model, ChatModelBase)

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
                **_get_common_attributes(),
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

        from ..message import ToolCallBlock

        tracer = _get_tracer()
        tool_call = input_kwargs.get("tool_call")
        assert isinstance(tool_call, ToolCallBlock)

        request_attributes = _get_tool_request_attributes(
            agent.toolkit,
            tool_call,
        )
        span_name = _get_tool_span_name(request_attributes)

        with tracer.start_as_current_span(
            name=span_name,
            attributes={
                **request_attributes,
                **_get_common_attributes(),
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


# ---------------------------------------------------------------------------
# Decorator: trace_llm
# ---------------------------------------------------------------------------


def trace_llm(func: Callable) -> Callable:
    """No-op decorator retained for backward compatibility.

    LLM call tracing is now handled by :class:`TracingMiddleware` via the
    ``on_model_call`` hook.  This decorator simply returns the original
    function unchanged so that existing ``@trace_llm`` usages in model
    classes continue to work without modification.
    """
    return func
