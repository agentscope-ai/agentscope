# -*- coding: utf-8 -*-
"""The MiniMax chat model implementation (OpenAI-compatible)."""

from datetime import datetime
from typing import Literal, Any, AsyncGenerator, TYPE_CHECKING, Type

from pydantic import BaseModel, Field

from .._base import ChatModelBase
from .._model_response import ChatResponse
from .._model_usage import ChatUsage
from ..._utils._common import _generate_id
from ...credential import MiniMaxCredential
from ...formatter import FormatterBase, DashScopeChatFormatter
from ...message import Msg, TextBlock, ToolCallBlock
from ...tool import ToolChoice

if TYPE_CHECKING:
    from openai.types.chat import ChatCompletion
    from openai import AsyncStream
else:
    ChatCompletion = Any
    AsyncStream = Any


class MiniMaxChatModel(ChatModelBase):
    """The MiniMax chat model (OpenAI-compatible implementation).

    MiniMax exposes an OpenAI-compatible ``/v1/chat/completions`` endpoint,
    so this class uses the official ``openai`` Python SDK to dispatch calls,
    similar to how the DashScope and DeepSeek chat models are wired.
    """

    class Parameters(BaseModel):
        """The parameters for the MiniMax chat model."""

        max_tokens: int | None = Field(
            default=None,
            title="Max Tokens",
            description="The maximum number of tokens for the LLM output.",
            gt=0,
        )

        temperature: float | None = Field(
            default=None,
            title="Temperature",
            description="The temperature for the LLM output.",
            ge=0,
            lt=2,
        )

        top_p: float | None = Field(
            default=None,
            title="Top P",
            description="The top P value for the LLM output.",
            gt=0,
            le=1,
        )

        parallel_tool_calls: bool = Field(
            default=True,
            title="Parallel Tool Calls",
            description="If enable parallel tool calls for the LLM output.",
        )

    type: Literal["MiniMax_chat"] = "MiniMax_chat"
    """The type of the chat model."""

    _API_MODEL_NAMES: dict[str, str] = {
        "minimax-m3": "MiniMax-M3",
    }
    """Mapping from AgentScope model ids (used in ``model=`` and YAML files)
    to the upstream MiniMax API ids accepted by the
    ``/v1/chat/completions`` endpoint."""

    def __init__(
        self,
        credential: MiniMaxCredential,
        model: str,
        parameters: "MiniMaxChatModel.Parameters | None" = None,
        stream: bool = True,
        max_retries: int = 3,
        retry_delay: float = 1.0,
        context_size: int = 131072,
        formatter: FormatterBase | None = None,
        client_kwargs: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the MiniMax chat model.

        Args:
            credential (`MiniMaxCredential`):
                The MiniMax credential used to authenticate API calls.
            model (`str`):
                The MiniMax model name, e.g. ``minimax-m3``.
            parameters (`MiniMaxChatModel.Parameters | None`, defaults to \
            `None`):
                The MiniMax API parameters. When ``None``, the default
                parameters will be used.
            stream (`bool`, defaults to `True`):
                Whether to enable streaming output.
            max_retries (`int`, defaults to `3`):
                The maximum number of retries for the MiniMax API.
            retry_delay (`float`, defaults to `1.0`):
                Seconds to sleep between retry attempts.
            context_size (`int`, defaults to `131072`):
                The model context size used for context compression.
            formatter (`FormatterBase | None`, defaults to `None`):
                The formatter that converts ``Msg`` objects to the format
                required by the MiniMax API. When ``None``, a
                ``DashScopeChatFormatter`` instance will be used, since
                MiniMax speaks the OpenAI-compatible format.
            client_kwargs (`dict[str, Any] | None`, defaults to `None`):
                Extra keyword arguments forwarded to ``openai.AsyncClient``
                (e.g. ``timeout``, ``default_headers``, ``http_client``).
        """
        super().__init__(
            credential=credential,
            model=model,
            parameters=parameters or self.Parameters(),
            stream=stream,
            max_retries=max_retries,
            retry_delay=retry_delay,
            context_size=context_size,
        )
        # Normalize the AgentScope model id (``minimax-m3``) to the upstream
        # MiniMax API id (``MiniMax-M3``) before issuing the request.
        self._model_name = self._API_MODEL_NAMES.get(model, model)
        self.formatter = formatter or DashScopeChatFormatter()
        self.client_kwargs = client_kwargs or {}

        import openai

        self.client: openai.AsyncClient = openai.AsyncClient(
            api_key=self.credential.api_key.get_secret_value(),
            base_url=self.credential.base_url,
            **self.client_kwargs,
        )

    @classmethod
    def _get_retryable_exceptions(cls) -> tuple[Type[Exception], ...]:
        import openai

        return (
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.RateLimitError,
            openai.InternalServerError,
        )

    @classmethod
    def _get_structured_output_fallback_exceptions(
        cls,
    ) -> tuple[Type[Exception], ...]:
        import openai

        return (openai.BadRequestError,)

    async def _call_api(
        self,
        model_name: str,
        messages: list[Msg],
        tools: list[dict] | None = None,
        tool_choice: ToolChoice | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Call the MiniMax chat completions API via OpenAI-compatible SDK.

        Args:
            model_name (`str`):
                The model name to use for the API call.
            messages (`list[Msg]`):
                The messages to send to the API.
            tools (`list[dict] | None`, optional):
                The tools to make available to the model.
            tool_choice (`ToolChoice | None`, optional):
                The tool choice configuration.
            **kwargs (`Any`):
                Additional keyword arguments to pass to the API.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                Either a single response or an async generator that yields
                streaming response chunks.
        """
        # Format messages and tools
        formatted_messages = await self.formatter.format(messages)

        params: dict[str, Any] = {
            "model": self._model_name,
            "messages": formatted_messages,
            "stream": self.stream,
        }

        # Add optional parameters
        if self.parameters.max_tokens is not None:
            params["max_tokens"] = self.parameters.max_tokens
        if self.parameters.temperature is not None:
            params["temperature"] = self.parameters.temperature
        if self.parameters.top_p is not None:
            params["top_p"] = self.parameters.top_p

        if tools:
            params["tools"] = tools
        if tool_choice is not None:
            self._validate_tool_choice(tool_choice, tools)

        # Add any extra kwargs
        params.update(kwargs)

        start_datetime = datetime.now()

        if self.stream:
            return self._parse_stream_response(
                await self.client.chat.completions.create(**params),
                start_datetime,
            )

        response = await self.client.chat.completions.create(**params)
        return self._parse_completion_response(response, start_datetime)

    async def _parse_stream_response(
        self,
        response: "AsyncStream",
        start_datetime: datetime,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse the streaming response from the MiniMax API.

        Args:
            response (`AsyncStream`):
                The streaming response from the API.
            start_datetime (`datetime`):
                The wall-clock time the request started, used to compute
                the elapsed time reported in the final ``ChatUsage``.

        Yields:
            `ChatResponse`:
                Individual response chunks as they arrive.
        """

        response_id: str = _generate_id()
        text_id: str = _generate_id()
        tool_call_id_map: dict[int, str] = {}
        tool_call_state: dict[int, dict] = {}

        async for chunk in response:
            delta_res = ChatResponse(
                content=[],
                is_last=False,
                id=response_id,
            )

            # Update the response ID if the chunk provides one
            chunk_id = getattr(chunk, "id", None)
            if chunk_id:
                response_id = chunk_id
                delta_res.id = response_id

            # Handle the usage chunk (often the final one with no choices)
            if hasattr(chunk, "usage") and chunk.usage:
                u = chunk.usage
                delta_res.usage = ChatUsage(
                    input_tokens=u.prompt_tokens or 0,
                    output_tokens=u.completion_tokens or 0,
                    time=(datetime.now() - start_datetime).total_seconds(),
                )

            if not chunk.choices:
                if delta_res.usage is not None or delta_res.content:
                    yield delta_res
                continue

            choice = chunk.choices[0]
            delta = choice.delta

            # Append text delta — using a stable ``block_id`` ensures
            # the same TextBlock accumulates text across chunks, so the
            # final yielded response contains the full concatenated text.
            text = delta.content or ""
            if text:
                delta_res.append_text(block_id=text_id, text=text)

            # Aggregate tool call deltas
            if hasattr(delta, "tool_calls") and delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_call_state:
                        tool_call_state[idx] = {
                            "id": tc.id,
                            "name": tc.function.name or "",
                            "input": "",
                        }
                        if tc.id:
                            tool_call_id_map[idx] = tc.id
                    state = tool_call_state[idx]
                    if tc.id:
                        state["id"] = tc.id
                        tool_call_id_map[idx] = tc.id
                    if tc.function.name:
                        state["name"] = tc.function.name
                    if tc.function.arguments:
                        state["input"] += tc.function.arguments

                # Once we have at least one tool call, replace content
                # blocks with the aggregated ToolCallBlocks.
                if tool_call_state:
                    delta_res.content = [
                        ToolCallBlock(
                            type="tool_call",
                            id=state["id"],
                            name=state["name"],
                            input=state["input"],
                        )
                        for state in tool_call_state.values()
                    ]

            if delta_res.content or delta_res.usage is not None:
                yield delta_res

    def _parse_completion_response(
        self,
        response: "ChatCompletion",
        start_datetime: datetime,
    ) -> ChatResponse:
        """Parse the non-streaming response from the MiniMax API.

        Args:
            response (`ChatCompletion`):
                The completion response from the API.
            start_datetime (`datetime`):
                The wall-clock time the request started, used to compute
                the elapsed time reported in the ``ChatUsage``.

        Returns:
            `ChatResponse`:
                The parsed chat response.
        """
        choice = response.choices[0]
        message = choice.message

        blocks: list[Any] = []
        if message.content:
            blocks.append(TextBlock(type="text", text=message.content))
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                blocks.append(
                    ToolCallBlock(
                        type="tool_call",
                        id=tc.id,
                        name=tc.function.name,
                        input=tc.function.arguments,
                    ),
                )

        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = ChatUsage(
                input_tokens=response.usage.prompt_tokens or 0,
                output_tokens=response.usage.completion_tokens or 0,
                time=(datetime.now() - start_datetime).total_seconds(),
            )

        return ChatResponse(
            content=blocks,
            is_last=True,
            usage=usage,
            id=getattr(response, "id", None) or _generate_id(),
            created_at=datetime.now().isoformat(),
        )

    def _format_tools(self, schemas: list[dict]) -> list[dict]:
        """Convert AgentScope tool schemas into OpenAI-compatible tool dicts.

        Args:
            schemas (`list[dict]`):
                The AgentScope tool schemas.

        Returns:
            `list[dict]`:
                The OpenAI-compatible tool definitions.
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": s["function"]["name"],
                    "description": s["function"].get("description", ""),
                    "parameters": s["function"].get("parameters", {}),
                },
            }
            for s in schemas
        ]

    def _get_disable_thinking_kwargs(self) -> dict:
        """Return kwargs that disable the model's thinking mode.

        MiniMax currently does not advertise a thinking toggle via the
        OpenAI-compatible surface, so we return an empty dict.
        """
        return {}
