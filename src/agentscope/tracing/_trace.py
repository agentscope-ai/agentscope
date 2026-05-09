# -*- coding: utf-8 -*-
"""The tracing decorators for agent, formatter, toolkit, chat and embedding
models."""
import inspect
import json
from functools import wraps
from typing import (
    Generator,
    AsyncGenerator,
    Callable,
    Any,
    Coroutine,
    TypeVar,
    TYPE_CHECKING,
)

from contextvars import ContextVar

import aioitertools

from .._logging import logger
from ..message import Msg, ToolCallBlock

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
    _get_formatter_request_attributes,
    _get_formatter_span_name,
    _get_formatter_response_attributes,
    _get_generic_function_request_attributes,
    _get_generic_function_span_name,
    _get_generic_function_response_attributes,
    _get_embedding_request_attributes,
    _get_embedding_span_name,
    _get_embedding_response_attributes,
)
from ._setup import _get_tracer
from ._utils import _serialize_to_str

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from ..agent import Agent
    from ..formatter import FormatterBase
    from ..state import AgentState
    from ..tool import (
        Toolkit,
        ToolChunk,
        ToolResponse,
    )


T = TypeVar("T")

# Context variable to propagate the session ID from agent reply through
# all inner tracing spans (LLM calls, tool calls, formatter calls, etc.)
_current_session_id: ContextVar[str] = ContextVar(
    "_current_session_id",
    default="",
)


def _check_tracing_enabled() -> bool:
    """Check if the OpenTelemetry tracer is initialized in AgentScope with an
    endpoint.

    Checks whether a real SDK TracerProvider has been configured via
    `setup_tracing`. Returns False when only the default no-op proxy provider
    is active so that tracing decorators incur no overhead.

    Also returns False if the OpenTelemetry SDK is not installed (only the
    API package is present), so that tracing decorators remain no-ops without
    raising ImportError.
    """
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
    except ImportError:
        return False

    return isinstance(otel_trace.get_tracer_provider(), TracerProvider)


def _set_span_success_status(span: "Span") -> None:
    """Set the status of the span.
    Args:
        span (`Span`):
            The OpenTelemetry span to be used for tracing.
    """
    from opentelemetry import trace as trace_api

    span.set_status(trace_api.StatusCode.OK)
    span.end()


def _set_span_error_status(span: "Span", e: BaseException) -> None:
    """Set the status of the span.
    Args:
        span (`Span`):
            The OpenTelemetry span to be used for tracing.
        e (`BaseException`):
            The exception to be recorded.
    """
    from opentelemetry import trace as trace_api

    span.set_status(trace_api.StatusCode.ERROR, str(e))
    span.record_exception(e)
    span.end()


def _trace_sync_generator_wrapper(
    res: Generator[T, None, None],
    span: "Span",
) -> Generator[T, None, None]:
    """Trace the sync generator output with OpenTelemetry."""

    has_error = False

    try:
        last_chunk = None
        for chunk in res:
            last_chunk = chunk
            yield chunk
    except BaseException as e:
        has_error = True
        _set_span_error_status(span, e)
        raise

    finally:
        if not has_error:
            # Set the last chunk as output
            span.set_attributes(
                _get_generic_function_response_attributes(last_chunk),
            )
            _set_span_success_status(span)


async def _trace_async_generator_wrapper(
    res: AsyncGenerator[T, None],
    span: "Span",
) -> AsyncGenerator[T, None]:
    """Trace the async generator output with OpenTelemetry.

    Args:
        res (`AsyncGenerator[T, None]`):
            The generator or async generator to be traced.
        span (`Span`):
            The OpenTelemetry span to be used for tracing.

    Yields:
        `T`:
            The output of the async generator.
    """
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
            # Set the last chunk as output

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
                response_attributes = (
                    _get_generic_function_response_attributes(
                        last_chunk,
                    )
                )

            span.set_attributes(response_attributes)
            _set_span_success_status(span)


def trace(
    name: str | None = None,
) -> Callable:
    """A generic tracing decorator for synchronous and asynchronous functions.

    Args:
        name (`str | None`, optional):
            The name of the span to be created. If not provided,
            the name of the function will be used.

    Returns:
        `Callable`:
            Returns a decorator that wraps the given function with
            OpenTelemetry tracing.
    """

    def decorator(
        func: Callable,
    ) -> Callable:
        """A decorator that wraps the given function with OpenTelemetry tracing

        Args:
            func (`Callable`):
                The function to be traced, which can be sync or async function,
                and returns an object or a generator.

        Returns:
            `Callable`:
                A wrapper function that traces the function call and handles
                input/output and exceptions.
        """
        # Async function
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def wrapper(
                *args: Any,
                **kwargs: Any,
            ) -> Any:
                """The wrapper function for tracing the sync function call."""
                if not _check_tracing_enabled():
                    return await func(*args, **kwargs)

                tracer = _get_tracer()

                function_name = name if name else func.__name__
                request_attributes = _get_generic_function_request_attributes(
                    function_name,
                    args,
                    kwargs,
                )

                span_name = _get_generic_function_span_name(request_attributes)
                with tracer.start_as_current_span(
                    name=span_name,
                    attributes=request_attributes,
                    end_on_exit=False,
                ) as span:
                    try:
                        res = await func(*args, **kwargs)

                        # If generator or async generator
                        if isinstance(res, AsyncGenerator):
                            return _trace_async_generator_wrapper(res, span)
                        if isinstance(res, Generator):
                            return _trace_sync_generator_wrapper(res, span)

                        # non-generator result
                        span.set_attributes(
                            _get_generic_function_response_attributes(res),
                        )
                        _set_span_success_status(span)
                        return res

                    except BaseException as e:
                        _set_span_error_status(span, e)
                        raise

            return wrapper

        # Sync function
        @wraps(func)
        def sync_wrapper(
            *args: Any,
            **kwargs: Any,
        ) -> Any:
            """The wrapper function for tracing the sync function call."""
            if not _check_tracing_enabled():
                return func(*args, **kwargs)

            tracer = _get_tracer()

            function_name = name if name else func.__name__
            request_attributes = _get_generic_function_request_attributes(
                function_name,
                args,
                kwargs,
            )

            span_name = _get_generic_function_span_name(request_attributes)
            with tracer.start_as_current_span(
                name=span_name,
                attributes=request_attributes,
                end_on_exit=False,
            ) as span:
                try:
                    res = func(*args, **kwargs)

                    # If generator or async generator
                    if isinstance(res, AsyncGenerator):
                        return _trace_async_generator_wrapper(res, span)
                    if isinstance(res, Generator):
                        return _trace_sync_generator_wrapper(res, span)

                    # non-generator result
                    span.set_attributes(
                        _get_generic_function_response_attributes(res),
                    )
                    _set_span_success_status(span)
                    return res

                except BaseException as e:
                    _set_span_error_status(span, e)
                    raise

        return sync_wrapper

    return decorator


def trace_toolkit(
    func: Callable[
        ...,
        AsyncGenerator["ToolChunk | ToolResponse", None],
    ],
) -> Callable[..., AsyncGenerator["ToolChunk | ToolResponse", None]]:
    """Trace the toolkit `call_tool` method with OpenTelemetry.

    The wrapper is an async generator so that the caller can iterate over it
    directly without an intermediate ``await``, matching the original
    ``call_tool`` interface.
    """

    @wraps(func)
    async def wrapper(
        self: "Toolkit",
        tool_call: ToolCallBlock,
        state: "AgentState",
    ) -> AsyncGenerator["ToolChunk | ToolResponse", None]:
        """The wrapper function for tracing the toolkit call_tool method."""
        if not _check_tracing_enabled():
            async for item in func(self, tool_call=tool_call, state=state):
                yield item
            return

        tracer = _get_tracer()

        request_attributes = _get_tool_request_attributes(self, tool_call)
        span_name = _get_tool_span_name(request_attributes)
        function_name = f"{self.__class__.__name__}.{func.__name__}"
        with tracer.start_as_current_span(
            name=span_name,
            attributes={
                **request_attributes,
                **_get_common_attributes(),
                SpanAttributes.AGENTSCOPE_FUNCTION_NAME: function_name,
            },
            end_on_exit=False,
        ) as span:
            has_error = False
            last_item = None
            try:
                async for item in func(self, tool_call=tool_call, state=state):
                    last_item = item
                    yield item
            except BaseException as e:
                has_error = True
                _set_span_error_status(span, e)
                raise
            finally:
                if not has_error:
                    if last_item is not None:
                        response_attributes = _get_tool_response_attributes(
                            last_item,
                        )
                        span.set_attributes(response_attributes)
                    _set_span_success_status(span)

    return wrapper


def trace_reply_stream(
    func: Callable[..., AsyncGenerator[Any, None]],
) -> Callable[..., AsyncGenerator[Any, None]]:
    """Trace the agent's internal async-generator reply with OpenTelemetry.

    Designed for :meth:`Agent._reply`, which yields a mix of
    ``AgentEvent`` and a final ``Msg``.  The ``invoke_agent`` span is
    opened before the first item is yielded and closed after the generator
    is exhausted (or on error).

    **Delegation assumption**

    This decorator is applied to :meth:`Agent._reply`.  Both the public
    :meth:`Agent.reply` and :meth:`Agent.reply_stream` methods delegate to
    ``_reply``, so all tracing goes through this single decorator.  If a
    future refactor bypasses ``_reply``, tracing must be re-wired
    accordingly.

    **HITL and external-execution support**

    A "logical reply" may be split into two separate ``_reply`` calls:

    1. First call yields ``RequireUserConfirmEvent`` or
       ``RequireExternalExecutionEvent`` and then returns.  Both calls share
       the same ``self.state.reply_id`` (reset only on the first call).
    2. Second call receives ``UserConfirmResultEvent`` or
       ``ExternalExecutionResultEvent`` via kwargs.

    This decorator handles both calls transparently:

    - ``agentscope.agent.reply_id``:  set on every span so that observers
      can correlate the two ``invoke_agent`` spans of a single logical reply.
    - ``agentscope.agent.hitl_pending_tools`` /
      ``agentscope.agent.external_execution_pending_tools``:  set on the first
      span to signal why the reply was suspended.
    - ``agentscope.agent.incoming_event_type``:  set on the second span to
      indicate it is a HITL or external-execution continuation.
    - Synthetic ``execute_tool`` spans:  emitted when the second call brings
      an ``ExternalExecutionResultEvent`` so that externally executed tools
      appear in the trace alongside internally executed ones.

    Args:
        func (`Callable[..., AsyncGenerator[Any, None]]`):
            The async-generator reply function to be traced.

    Returns:
        `Callable[..., AsyncGenerator[Any, None]]`:
            An async-generator wrapper that traces the agent reply stream.
    """

    @wraps(func)
    async def wrapper(
        self: "Agent",
        *args: Any,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        """Wrap the async-generator reply with an OpenTelemetry span."""
        if not _check_tracing_enabled():
            async for item in func(self, *args, **kwargs):
                yield item
            return

        from ..agent import Agent

        if not isinstance(self, Agent):
            logger.warning(
                "Skipping tracing for %s as the first argument "
                "is not an instance of Agent, but %s",
                func.__name__,
                type(self),
            )
            async for item in func(self, *args, **kwargs):
                yield item
            return

        # Propagate the agent's session_id to all inner tracing spans
        session_id = getattr(
            getattr(self, "state", None),
            "session_id",
            "",
        )
        token = _current_session_id.set(session_id or "")

        try:
            tracer = _get_tracer()
            request_attributes = _get_agent_request_attributes(
                self,
                args,
                kwargs,
            )
            span_name = _get_agent_span_name(request_attributes)
            function_name = f"{self.__class__.__name__}.{func.__name__}"

            with tracer.start_as_current_span(
                name=span_name,
                attributes={
                    **request_attributes,
                    **_get_common_attributes(),
                    SpanAttributes.AGENTSCOPE_FUNCTION_NAME: function_name,
                },
                end_on_exit=False,
            ) as span:
                # Emit synthetic execute_tool spans for externally executed
                # tools as children of the invoke_agent span so that they
                # appear in the same subtree as internally executed tools.
                #
                # Note: GEN_AI_TOOL_CALL_ARGUMENTS is intentionally omitted
                # here.  ExternalExecutionResultEvent only carries the tool
                # result; the original call arguments were in the preceding
                # RequireExternalExecutionEvent (first call).  Observers can
                # correlate the two calls via the shared reply_id.
                from ..event import ExternalExecutionResultEvent

                event_arg = kwargs.get("event")
                if isinstance(event_arg, ExternalExecutionResultEvent):
                    # Evaluate once outside the loop; it contains a local
                    # import that Python caches, but calling it per-iteration
                    # is still unnecessary overhead when results are many.
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
                        # Only record the result when the tool succeeded and
                        # output is available.  A None/absent output (e.g.
                        # state=FAIL) would serialize to the string "null",
                        # which is misleading, so we omit it in that case.
                        if result.output is not None:
                            tool_attrs[
                                SpanAttributes.GEN_AI_TOOL_CALL_RESULT
                            ] = _serialize_to_str(result.output)
                        # end_on_exit defaults to True here: this is a
                        # synchronous, instantaneous record-only span with no
                        # body, so it is safe (and correct) to let the context
                        # manager close it immediately on exit.
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
                # Track pending tools requiring external intervention
                hitl_pending: list[str] = []
                external_pending: list[str] = []
                # Capture reply_id from ReplyStartEvent; the first call emits
                # one, the second (continuation) call does not.
                observed_reply_id: str | None = None

                try:
                    from ..event import (
                        RequireExternalExecutionEvent as _RequireExtExec,
                        RequireUserConfirmEvent as _RequireUserConfirm,
                        ReplyStartEvent as _ReplyStart,
                    )

                    async for item in func(self, *args, **kwargs):
                        if isinstance(item, _ReplyStart):
                            # Emitted only on the first call of a logical reply
                            observed_reply_id = item.reply_id
                        elif isinstance(item, _RequireUserConfirm):
                            hitl_pending.extend(
                                t.name for t in item.tool_calls
                            )
                        elif isinstance(item, _RequireExtExec):
                            external_pending.extend(
                                t.name for t in item.tool_calls
                            )
                        # _reply yields both AgentEvent and Msg objects.
                        # Track the last Msg to populate response attributes.
                        if isinstance(item, Msg):
                            last_msg = item
                        yield item
                except BaseException as e:
                    has_error = True
                    error_exc = e
                    raise
                finally:
                    # reply_id: prefer value from ReplyStartEvent; fall back to
                    # state (set before yield, safe to read here for
                    # continuation calls that don't emit ReplyStartEvent).
                    reply_id = observed_reply_id or getattr(
                        getattr(self, "state", None),
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

                    # End the span last, after all attributes are set
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

    return wrapper


def trace_embedding(
    func: Callable[..., Coroutine[Any, Any, Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Trace the embedding call with OpenTelemetry."""

    @wraps(func)
    async def wrapper(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """The wrapper function for tracing the embedding call."""
        if not _check_tracing_enabled():
            return await func(self, *args, **kwargs)

        from ..embedding import EmbeddingModelBase

        if not isinstance(self, EmbeddingModelBase):
            logger.warning(
                "Skipping tracing for %s as the first argument "
                "is not an instance of EmbeddingModelBase, but %s",
                func.__name__,
                type(self),
            )
            return await func(self, *args, **kwargs)

        tracer = _get_tracer()

        # Prepare the attributes for the span
        request_attributes = _get_embedding_request_attributes(
            self,
            args,
            kwargs,
        )
        span_name = _get_embedding_span_name(request_attributes)
        function_name = f"{self.__class__.__name__}.{func.__name__}"

        with tracer.start_as_current_span(
            name=span_name,
            attributes={
                **request_attributes,
                **_get_common_attributes(),
                SpanAttributes.AGENTSCOPE_FUNCTION_NAME: function_name,
            },
            end_on_exit=False,
        ) as span:
            try:
                # Call the embedding function
                res = await func(self, *args, **kwargs)

                # Set the output attribute
                span.set_attributes(_get_embedding_response_attributes(res))
                _set_span_success_status(span)
                return res

            except BaseException as e:
                _set_span_error_status(span, e)
                raise

    return wrapper


def trace_format(
    func: Callable[..., Coroutine[Any, Any, list[dict]]],
) -> Callable[..., Coroutine[Any, Any, list[dict]]]:
    """Trace the format function of the formatter with OpenTelemetry.

    Args:
        func (`Callable[..., Coroutine[Any, Any, list[dict]]]`):
            The async format function to be traced.

    Returns:
        `Callable[..., Coroutine[Any, Any, list[dict]]]`:
            An async wrapper function that traces the format call and handles
            input/output and exceptions.
    """

    @wraps(func)
    async def wrapper(
        self: "FormatterBase",
        *args: Any,
        **kwargs: Any,
    ) -> list[dict]:
        """Wrap the formatter __call__ method with OpenTelemetry tracing."""
        if not _check_tracing_enabled():
            return await func(self, *args, **kwargs)

        from ..formatter import FormatterBase

        if not isinstance(self, FormatterBase):
            logger.warning(
                "Skipping tracing for %s as the first argument "
                "is not an instance of FormatterBase, but %s",
                func.__name__,
                type(self),
            )
            return await func(self, *args, **kwargs)

        tracer = _get_tracer()

        # Prepare the attributes for the span
        request_attributes = _get_formatter_request_attributes(
            self,
            args,
            kwargs,
        )
        span_name = _get_formatter_span_name(request_attributes)
        function_name = f"{self.__class__.__name__}.{func.__name__}"
        with tracer.start_as_current_span(
            name=span_name,
            attributes={
                **request_attributes,
                **_get_common_attributes(),
                SpanAttributes.AGENTSCOPE_FUNCTION_NAME: function_name,
            },
            end_on_exit=False,
        ) as span:
            try:
                # Call the formatter function
                res = await func(self, *args, **kwargs)

                # Set the output attribute
                span.set_attributes(_get_formatter_response_attributes(res))
                _set_span_success_status(span)
                return res

            except BaseException as e:
                _set_span_error_status(span, e)
                raise

    return wrapper


def trace_llm(
    func: Callable[..., Coroutine[Any, Any, Any]],
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Trace the LLM call with OpenTelemetry.

    Args:
        func (`Callable`):
            The function to be traced, which should be a coroutine that
            returns either a `ChatResponse` or an `AsyncGenerator`
            of `ChatResponse`.

    Returns:
        `Callable`:
            A wrapper function that traces the LLM call and handles
            input/output and exceptions.
    """

    @wraps(func)
    async def async_wrapper(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """The wrapper function for tracing the LLM call."""
        if not _check_tracing_enabled():
            return await func(self, *args, **kwargs)

        from ..model import ChatModelBase

        if not isinstance(self, ChatModelBase):
            logger.warning(
                "Skipping tracing for %s as the first argument "
                "is not an instance of ChatModelBase, but %s",
                func.__name__,
                type(self),
            )
            return await func(self, *args, **kwargs)

        tracer = _get_tracer()

        # Prepare the attributes for the span
        request_attributes = _get_llm_request_attributes(self, args, kwargs)
        span_name = _get_llm_span_name(request_attributes)
        function_name = f"{self.__class__.__name__}.__call__"
        # Begin the llm call span
        with tracer.start_as_current_span(
            name=span_name,
            attributes={
                **request_attributes,
                **_get_common_attributes(),
                SpanAttributes.AGENTSCOPE_FUNCTION_NAME: function_name,
            },
            end_on_exit=False,
        ) as span:
            try:
                # Must be an async calling
                res = await func(self, *args, **kwargs)

                # If the result is a AsyncGenerator
                if isinstance(res, AsyncGenerator):
                    return _trace_async_generator_wrapper(res, span)

                # non-generator result
                span.set_attributes(_get_llm_response_attributes(res))
                _set_span_success_status(span)
                return res

            except BaseException as e:
                _set_span_error_status(span, e)
                raise

    return async_wrapper
