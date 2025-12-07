# -*- coding: utf-8 -*-
"""The tracing decorators for agent, formatter, toolkit, chat and embedding
models."""
import inspect
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

import aioitertools

from .. import _config
from ..embedding import EmbeddingModelBase, EmbeddingResponse
from .._logging import logger
from ..message import Msg, ToolUseBlock
from ..model import ChatModelBase, ChatResponse

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

if TYPE_CHECKING:
    from opentelemetry.trace import Span

    from ..agent import AgentBase
    from ..formatter import FormatterBase
    from ..tool import (
        Toolkit,
        ToolResponse,
    )

else:
    AgentBase = "AgentBase"
    FormatterBase = "FormatterBase"
    Span = "Span"
    Toolkit = "Toolkit"
    ToolResponse = "ToolResponse"


T = TypeVar("T")


def _check_tracing_enabled() -> bool:
    """Check if the OpenTelemetry tracer is initialized in AgentScope with an
    endpoint.

    TODO: We expect an OpenTelemetry official interface to check if the
     tracer is initialized. Leaving this function here as a temporary
     solution.
    """
    return _config.trace_enabled


def _set_span_success_status(span: Span) -> None:
    """Set the status of the span.
    Args:
        span (`Span`):
            The OpenTelemetry span to be used for tracing.
    """
    from opentelemetry import trace as trace_api

    span.set_status(trace_api.StatusCode.OK)
    span.end()


def _set_span_error_status(span: Span, e: Exception) -> None:
    """Set the status of the span.
    Args:
        span (`Span`):
            The OpenTelemetry span to be used for tracing.
        e (`Exception`):
            The exception to be recorded.
    """
    from opentelemetry import trace as trace_api

    span.set_status(trace_api.StatusCode.ERROR, str(e))
    span.record_exception(e)
    span.end()


def _trace_sync_generator_wrapper(
    res: Generator[T, None, None],
    span: Span,
) -> Generator[T, None, None]:
    """Trace the sync generator output with OpenTelemetry."""

    has_error = False

    try:
        last_chunk = None
        for chunk in res:
            last_chunk = chunk
            yield chunk
    except Exception as e:
        has_error = True
        _set_span_error_status(span, e)
        raise e from None

    finally:
        if not has_error:
            # Set the last chunk as output
            span.set_attributes(
                _get_generic_function_response_attributes(last_chunk),
            )
            _set_span_success_status(span)


async def _trace_async_generator_wrapper(
    res: AsyncGenerator[T, None],
    span: Span,
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

    except Exception as e:
        has_error = True
        _set_span_error_status(span, e)
        raise e from None

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

                    except Exception as e:
                        _set_span_error_status(span, e)
                        raise e from None

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

                except Exception as e:
                    _set_span_error_status(span, e)
                    raise e from None

        return sync_wrapper

    return decorator


def trace_toolkit(
    func: Callable[
        ...,
        Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]],
    ],
) -> Callable[..., Coroutine[Any, Any, AsyncGenerator[ToolResponse, None]]]:
    """Trace the toolkit `call_tool_function` method with OpenTelemetry."""

    @wraps(func)
    async def wrapper(
        self: Toolkit,
        tool_call: ToolUseBlock,
    ) -> AsyncGenerator[ToolResponse, None]:
        """The wrapper function for tracing the toolkit call_tool_function
        method."""
        if not _check_tracing_enabled():
            return await func(self, tool_call=tool_call)

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
            try:
                # Call the toolkit function
                res = await func(self, tool_call=tool_call)

                # The result must be an AsyncGenerator of ToolResponse objects
                return _trace_async_generator_wrapper(res, span)

            except Exception as e:
                _set_span_error_status(span, e)
                span.end()
                raise e from None

    return wrapper


def trace_reply(
    func: Callable[..., Coroutine[Any, Any, Msg]],
) -> Callable[..., Coroutine[Any, Any, Msg]]:
    """Trace the agent reply call with OpenTelemetry.

    Args:
        func (`Callable[..., Coroutine[Any, Any, Msg]]`):
            The agent async reply function to be traced.

    Returns:
        `Callable[..., Coroutine[Any, Any, Msg]]`:
            A wrapper function that traces the agent reply call and handles
            input/output and exceptions.
    """

    @wraps(func)
    async def wrapper(
        self: "AgentBase",
        *args: Any,
        **kwargs: Any,
    ) -> Msg:
        """The wrapper function for tracing the agent reply function call."""
        if not _check_tracing_enabled():
            return await func(self, *args, **kwargs)

        from ..agent import AgentBase

        if not isinstance(self, AgentBase):
            logger.warning(
                "Skipping tracing for %s as the first argument"
                "is not an instance of AgentBase, but %s",
                func.__name__,
                type(self),
            )
            return await func(self, *args, **kwargs)

        tracer = _get_tracer()

        # Prepare the attributes for the span

        request_attributes = _get_agent_request_attributes(self, args, kwargs)
        span_name = _get_agent_span_name(request_attributes)
        function_name = f"{self.__class__.__name__}.{func.__name__}"
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
                # Call the agent reply function
                res = await func(self, *args, **kwargs)

                # Set the output attribute
                span.set_attributes(_get_agent_response_attributes(res))
                _set_span_success_status(span)
                return res

            except Exception as e:
                _set_span_error_status(span, e)
                raise e from None

    return wrapper


def trace_embedding(
    func: Callable[..., Coroutine[Any, Any, EmbeddingResponse]],
) -> Callable[..., Coroutine[Any, Any, EmbeddingResponse]]:
    """Trace the embedding call with OpenTelemetry."""

    @wraps(func)
    async def wrapper(
        self: EmbeddingModelBase,
        *args: Any,
        **kwargs: Any,
    ) -> EmbeddingResponse:
        """The wrapper function for tracing the embedding call."""
        if not _check_tracing_enabled():
            return await func(self, *args, **kwargs)

        if not isinstance(self, EmbeddingModelBase):
            logger.warning(
                "Skipping tracing for %s as the first argument"
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

            except Exception as e:
                _set_span_error_status(span, e)
                raise e from None

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
                "Skipping tracing for %s as the first argument"
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

            except Exception as e:
                _set_span_error_status(span, e)
                raise e from None

    return wrapper


def trace_llm(
    func: Callable[
        ...,
        Coroutine[
            Any,
            Any,
            ChatResponse | AsyncGenerator[ChatResponse, None],
        ],
    ],
) -> Callable[
    ...,
    Coroutine[Any, Any, ChatResponse | AsyncGenerator[ChatResponse, None]],
]:
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
        self: ChatModelBase,
        *args: Any,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """The wrapper function for tracing the LLM call."""
        if not _check_tracing_enabled():
            return await func(self, *args, **kwargs)

        if not isinstance(self, ChatModelBase):
            logger.warning(
                "Skipping tracing for %s as the first argument"
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

            except Exception as e:
                _set_span_error_status(span, e)
                raise e from None

    return async_wrapper
