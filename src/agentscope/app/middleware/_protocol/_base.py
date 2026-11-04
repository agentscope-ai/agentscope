# -*- coding: utf-8 -*-
"""Protocol middleware base class for converting AgentEvent stream to
various protocols."""

import json
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Callable

from fastapi import Request, Response
from fastapi.responses import StreamingResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from agentscope.event import AgentEvent


class ProtocolMiddlewareBase(BaseHTTPMiddleware, ABC):
    """Base middleware for converting AgentEvent stream to protocol format.

    This middleware intercepts ``text/event-stream`` responses, deserializes
    AgentEvent objects from SSE ``data:`` frames, and converts them to a
    specific protocol format.

    Subclasses should implement the `_convert_to_protocol` method to define
    the conversion logic for their specific protocol (e.g., AGUI, A2A).

    Example:
        ```python
        class AGUIMiddleware(ProtocolMiddlewareBase):
            def _convert_to_protocol(self, event: AgentEvent) -> dict:
                # Implement AGUI-specific conversion logic
                return {...}

        app = FastAPI()
        app.add_middleware(AGUIMiddleware)
        ```
    """

    def __init__(self, app: ASGIApp) -> None:
        """Initialize the protocol middleware.

        Args:
            app: The ASGI application to wrap.
        """
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: Callable,
    ) -> Response:
        """Process the request and convert AgentEvent stream to protocol
        format.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or endpoint handler.

        Returns:
            The response, potentially with converted stream content.
        """
        # Call the next middleware or endpoint
        response = await call_next(request)

        content_type = response.headers.get("content-type", "")
        body_iterator = getattr(response, "body_iterator", None)

        if (
            content_type.startswith("text/event-stream")
            and body_iterator is not None
        ):
            # Wrap the original stream with our conversion logic
            converted_stream = self._convert_stream(body_iterator)

            # Create a new StreamingResponse with the converted stream
            return StreamingResponse(
                content=converted_stream,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type,
            )

        return response

    async def _convert_stream(
        self,
        original_stream: AsyncGenerator,
    ) -> AsyncGenerator[bytes, None]:
        """Convert AgentEvent stream to protocol format.

        Args:
            original_stream: The original stream yielding serialized
                AgentEvent objects.

        Yields:
            Bytes in protocol format.
        """
        async for chunk in original_stream:
            if isinstance(chunk, bytes):
                chunk_str = chunk.decode("utf-8")
            else:
                chunk_str = chunk

            converted = self._convert_sse_frame(chunk_str)
            if converted is not None:
                yield converted
                continue

            # Fallback for subclasses that may override dispatch() to handle
            # non-SSE streams while still reusing this converter.
            converted = self._convert_event_json(chunk_str)
            if converted is not None:
                yield converted
                continue

            if isinstance(chunk, bytes):
                yield chunk
            else:
                yield chunk.encode("utf-8")

    def _convert_sse_frame(self, frame: str) -> bytes | None:
        """Convert AgentEvent payloads inside an SSE frame.

        Note:
            This method targets the AgentScope service's SSE stream shape:
            each ``data:`` line contains a complete JSON payload, and each
            input ``frame`` contains one or more complete SSE frames. SSE
            multi-line ``data:`` concatenation and cross-chunk frame
            reassembly are intentionally out of scope here.

        Args:
            frame: A server-sent event frame.

        Returns:
            Converted frame bytes if at least one ``data:`` payload was
            converted, otherwise ``None``.
        """
        lines = frame.splitlines(keepends=True)
        converted_lines: list[str] = []
        converted_any = False

        for line in lines:
            if not line.startswith("data:"):
                converted_lines.append(line)
                continue

            line_content, line_ending = self._split_line_ending(line)
            payload = line_content.removeprefix("data:")
            if payload.startswith(" "):
                payload = payload[1:]

            converted = self._convert_event_json(payload)
            if converted is None:
                converted_lines.append(line)
                continue

            converted_json = converted.decode("utf-8").rstrip("\n")
            converted_lines.append(f"data: {converted_json}{line_ending}")
            converted_any = True

        if not converted_any:
            return None

        return "".join(converted_lines).encode("utf-8")

    @staticmethod
    def _split_line_ending(line: str) -> tuple[str, str]:
        """Split a line into content and its original line ending."""
        if line.endswith("\r\n"):
            return line[:-2], "\r\n"
        if line.endswith("\n"):
            return line[:-1], "\n"
        if line.endswith("\r"):
            return line[:-1], "\r"
        return line, ""

    def _convert_event_json(self, chunk_str: str) -> bytes | None:
        """Convert a serialized AgentEvent JSON string.

        Args:
            chunk_str: Serialized AgentEvent JSON.

        Returns:
            Converted protocol JSON bytes with trailing newline, or ``None``
            when ``chunk_str`` is not a valid AgentEvent payload.
        """
        try:
            event_dict = json.loads(chunk_str)
            agent_event = self._deserialize_event(event_dict)
            protocol_data = self._convert_to_protocol(agent_event)
            return (
                json.dumps(protocol_data, ensure_ascii=False).encode(
                    "utf-8",
                )
                + b"\n"
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def _deserialize_event(self, event_dict: dict) -> AgentEvent:
        """Deserialize event dictionary to AgentEvent object.

        Args:
            event_dict: Dictionary containing event data with 'type' field.

        Returns:
            Deserialized AgentEvent object.

        Raises:
            ValueError: If event type is unknown or deserialization fails.
        """
        from pydantic import Field, TypeAdapter
        from typing import Annotated

        # Use Pydantic's discriminated union to automatically deserialize
        # based on the 'type' field
        adapter = TypeAdapter(
            Annotated[AgentEvent, Field(discriminator="type")],
        )
        return adapter.validate_python(event_dict)

    @abstractmethod
    def _convert_to_protocol(self, event: AgentEvent) -> dict:
        """Convert AgentEvent to protocol format.

        This is an abstract method that must be implemented by subclasses
        to define the conversion logic for their specific protocol.

        Args:
            event: The AgentEvent object to convert.

        Returns:
            Dictionary in the target protocol format.

        Example:
            ```python
            class AGUIMiddleware(ProtocolMiddlewareBase):
                def _convert_to_protocol(self, event: AgentEvent) -> dict:
                    # Convert to AGUI format
                    agui_data = event.model_dump()
                    agui_data["agui_version"] = "1.0"
                    return agui_data
            ```
        """
