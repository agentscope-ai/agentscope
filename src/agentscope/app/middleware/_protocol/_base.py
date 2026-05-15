# -*- coding: utf-8 -*-
"""Protocol middleware base class for converting AgentEvent stream to various protocols."""
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

    This middleware intercepts streaming responses that yield AgentEvent
    objects, deserializes them, and converts them to a specific protocol format.

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
        """Process the request and convert AgentEvent stream to protocol format.

        Args:
            request: The incoming HTTP request.
            call_next: The next middleware or endpoint handler.

        Returns:
            The response, potentially with converted stream content.
        """
        # Call the next middleware or endpoint
        response = await call_next(request)

        # Check if the response is a streaming response
        if isinstance(response, StreamingResponse):
            # Wrap the original stream with our conversion logic
            original_stream = response.body_iterator
            converted_stream = self._convert_stream(original_stream)

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
            # Decode the chunk if it's bytes
            if isinstance(chunk, bytes):
                chunk_str = chunk.decode("utf-8")
            else:
                chunk_str = chunk

            # Try to deserialize the chunk as AgentEvent
            try:
                # Parse the JSON string to dict
                event_dict = json.loads(chunk_str)

                # Deserialize to AgentEvent based on the 'type' field
                agent_event = self._deserialize_event(event_dict)

                # Convert AgentEvent to protocol format
                protocol_data = self._convert_to_protocol(agent_event)

                # Serialize and yield the protocol data
                yield json.dumps(protocol_data, ensure_ascii=False).encode(
                    "utf-8",
                ) + b"\n"

            except (json.JSONDecodeError, KeyError, ValueError) as e:
                # If deserialization fails, pass through the original chunk
                # or log the error
                # For now, we'll pass through the original chunk
                if isinstance(chunk, bytes):
                    yield chunk
                else:
                    yield chunk.encode("utf-8")

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
