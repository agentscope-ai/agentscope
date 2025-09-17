# -*- coding: utf-8 -*-
"""Model wrapper for Ollama models."""
from datetime import datetime
from typing import (
    Any,
    TYPE_CHECKING,
    List,
    AsyncGenerator,
    AsyncIterator,
    Literal,
    Type,
)
from collections import OrderedDict

from pydantic import BaseModel

from . import ChatResponse
from ._model_base import ChatModelBase
from ._model_usage import ChatUsage
from ..tracing import trace_llm
from ..message import ToolUseBlock, TextBlock, ThinkingBlock
from .._logging import logger
from .._utils._common import _json_loads_with_repair


if TYPE_CHECKING:
    from ollama._types import ChatResponse as OllamaChatResponse
else:
    OllamaChatResponse = "ollama._types.ChatResponse"


import re


class _HarmonyParser:
    """A parser for OpenAI Harmony response format."""

    # Special tokens
    START_TOKEN = "<|start|>"
    END_TOKEN = "<|end|>"
    MESSAGE_TOKEN = "<|message|>"
    CHANNEL_TOKEN = "<|channel|>"
    CONSTRAIN_TOKEN = "<|constrain|>"
    RETURN_TOKEN = "<|return|>"
    CALL_TOKEN = "<|call|>"

    # Regular expressions
    MESSAGE_PATTERN = re.compile(
        r"<\|start\|>(.*?)<\|message\|>(.*?)<\|end\|>",
        re.DOTALL,
    )
    CHANNEL_PATTERN = re.compile(r"<\|channel\|>(\w+)")
    TOOL_CALL_PATTERN = re.compile(r"to=([\w\.]+)")

    def parse(
        self,
        response_text: str,
    ) -> list:
        """
        Parses the full response text in Harmony format and extracts
        content blocks.

        Args:
            response_text (`str`):
                The full response text from the model.

        Returns:
            `list`:
                A list of content blocks (TextBlock, ThinkingBlock,
                ToolUseBlock).
        """
        content_blocks = []
        # In harmony format, the response is a sequence of messages
        # starting with <|start|> and ending with <|end|>.
        # Sometimes the response may start with a channel token immediately.
        if not response_text.strip().startswith(self.START_TOKEN):
            response_text = f"{self.START_TOKEN}assistant{response_text}"
        if response_text.strip().endswith(self.RETURN_TOKEN):
            response_text = response_text.replace(
                self.RETURN_TOKEN,
                self.END_TOKEN,
            )
        for match in self.MESSAGE_PATTERN.finditer(response_text):
            header = match.group(1)
            content = match.group(2)

            channel_match = self.CHANNEL_PATTERN.search(header)
            channel = channel_match.group(1) if channel_match else "final"

            if channel == "analysis":
                content_blocks.append(
                    ThinkingBlock(type="thinking", thinking=content.strip()),
                )
            elif channel == "final":
                content_blocks.append(
                    TextBlock(type="text", text=content.strip()),
                )
            elif channel == "commentary":
                tool_call_match = self.TOOL_CALL_PATTERN.search(header)
                if tool_call_match:
                    tool_name = tool_call_match.group(1)
                    # The tool name is in the format of "functions.xxx"
                    tool_name = tool_name.split(".")[-1]
                    try:
                        tool_input = _json_loads_with_repair(content)
                        content_blocks.append(
                            ToolUseBlock(
                                type="tool_use",
                                id=f"tool_call_{tool_name}",
                                name=tool_name,
                                input=tool_input,
                            ),
                        )
                    except Exception as e:
                        logger.warning(
                            "Failed to parse tool call input: %s",
                            e,
                        )
        return content_blocks

    async def parse_stream(
        self,
        response: AsyncIterator[OllamaChatResponse],
    ) -> AsyncGenerator[list, None]:
        """
        Parses a streaming response in Harmony format and yields content
        blocks.

        Args:
            response (`AsyncIterator[OllamaChatResponse]`):
                The streaming response from the model.

        Yields:
            `AsyncGenerator[list, None]`:
                A list of content blocks for each chunk.
        """
        # Note: This implementation accumulates the entire stream into a single
        # string before parsing. This is simpler but less memory-efficient
        # for very long streams. A potential future improvement is to implement
        # a true streaming parser that can process chunks as they arrive.
        full_text = ""
        async for chunk in response:
            full_text += chunk.message.content
            if chunk.done:
                yield self.parse(full_text)


class OllamaChatModel(ChatModelBase):
    """The Ollama chat model class in agentscope."""

    def __init__(
        self,
        model_name: str,
        stream: bool = False,
        options: dict = None,
        keep_alive: str = "5m",
        enable_thinking: bool | None = None,
        host: str | None = None,
        support_harmony_response: bool = False,
        **kwargs: Any,
    ) -> None:
        """Initialize the Ollama chat model.

        Args:
           model_name (`str`):
               The name of the model.
           stream (`bool`, default `True`):
               Whether to stream the response from the model.
           options (`dict`, default `None`):
               Additional parameters to pass to the Ollama API. These can
               include temperature etc.
           keep_alive (`str`, default `"5m"`):
               Duration to keep the model loaded in memory. The format is a
               number followed by a unit suffix (s for seconds, m for minutes
               , h for hours).
           enable_thinking (`bool | None`, default `None`)
               Whether enable thinking or not, only for models such as qwen3,
               deepseek-r1, etc. For more details, please refer to
               https://ollama.com/search?c=thinking
           host (`str | None`, default `None`):
               The host address of the Ollama server. If None, uses the
               default address (typically http://localhost:11434).
           support_harmony_response (`bool`, default `False`):
                Whether to support OpenAI Harmony response format.
                This is necessary for models such as gpt-oss.
                For more details, please refer to
                https://cookbook.openai.com/articles/openai-harmony
           **kwargs (`Any`):
               Additional keyword arguments to pass to the base chat model
               class.
        """

        try:
            import ollama
        except ImportError as e:
            raise ImportError(
                "The package ollama is not found. Please install it by "
                'running command `pip install "ollama>=0.1.7"`',
            ) from e

        super().__init__(model_name, stream)

        self.client = ollama.AsyncClient(
            host=host,
            **kwargs,
        )
        self.options = options
        self.keep_alive = keep_alive
        self.think = enable_thinking
        self.support_harmony_response = support_harmony_response
        if self.support_harmony_response:
            self._harmony_parser = _HarmonyParser()

    @trace_llm
    async def __call__(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        tool_choice: Literal["auto", "none", "any", "required"]
        | str
        | None = None,
        structured_model: Type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> ChatResponse | AsyncGenerator[ChatResponse, None]:
        """Get the response from Ollama chat completions API by the given
        arguments.

        Args:
            messages (`list[dict]`):
                A list of dictionaries, where `role` and `content` fields are
                required, and `name` field is optional.
            tools (`list[dict]`, default `None`):
                The tools JSON schemas that the model can use.
            tool_choice (`Literal["auto", "none", "any", "required"] | str \
                | None`, default `None`):
                Controls which (if any) tool is called by the model.
                 Can be "auto", "none", "any", "required", or specific tool
                 name.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.
            **kwargs (`Any`):
                The keyword arguments for Ollama chat completions API,
                e.g. `think`etc. Please refer to the Ollama API
                documentation for more details.

        Returns:
            `ChatResponse | AsyncGenerator[ChatResponse, None]`:
                The response from the Ollama chat completions API.
        """

        kwargs = {
            "model": self.model_name,
            "messages": messages,
            "stream": self.stream,
            "options": self.options,
            "keep_alive": self.keep_alive,
            **kwargs,
        }

        if self.think is not None and "think" not in kwargs:
            kwargs["think"] = self.think

        if tools:
            kwargs["tools"] = self._format_tools_json_schemas(tools)

        if tool_choice:
            logger.warning("Ollama does not support tool_choice yet, ignored.")

        if structured_model:
            kwargs["format"] = structured_model.model_json_schema()

        start_datetime = datetime.now()
        
        # Use Harmony format input when support_harmony_response is enabled
        if self.support_harmony_response:
            try:
                # Remove messages and tools from kwargs to avoid duplication
                kwargs_harmony = kwargs.copy()
                kwargs_harmony.pop('messages', None)
                kwargs_harmony.pop('tools', None)
                harmony_response = await self._call_with_harmony_format(
                    messages, tools, structured_model, **kwargs_harmony
                )
                return harmony_response
            except Exception as e:
                logger.warning(f"Failed to use Harmony format, falling back to standard: {e}")
        
        response = await self.client.chat(**kwargs)

        if self.stream:
            return self._parse_ollama_stream_completion_response(
                start_datetime,
                response,
                structured_model,
            )

        parsed_response = await self._parse_ollama_completion_response(
            start_datetime,
            response,
            structured_model,
        )

        return parsed_response

    def _convert_to_harmony_format(
        self, 
        messages: list,
        tools: list = None,
    ) -> str:
        """Convert standard messages to Harmony format string.
        
        Args:
            messages: List of message dicts in OpenAI format
            tools: Optional tool definitions
            
        Returns:
            Harmony format string ready for direct model input
        """
        harmony_parts = []
        
        # Add system message with tool definitions if present
        system_added = False
        for msg in messages:
            if msg['role'] == 'system':
                harmony_parts.append(f"<|start|>system<|message|>{msg['content']}")
                
                # Add tool definitions to system message if present
                if tools:
                    harmony_parts.append("\n\n# Available Tools\n")
                    for tool in tools:
                        func = tool['function']
                        harmony_parts.append(f"## {func['name']}\n")
                        if func.get('description'):
                            harmony_parts.append(f"// {func['description']}\n")
                        
                        # Add parameter info
                        if func.get('parameters', {}).get('properties'):
                            props = func['parameters']['properties']
                            params_info = []
                            for param, info in props.items():
                                param_type = info.get('type', 'any')
                                params_info.append(f"{param}: {param_type}")
                            harmony_parts.append(f"type {func['name']} = (_: {{{', '.join(params_info)}}}) => any;\n")
                        else:
                            harmony_parts.append(f"type {func['name']} = () => any;\n")
                
                harmony_parts.append("<|end|>")
                system_added = True
                break
        
        # Add default system if none provided but tools exist
        if not system_added and tools:
            harmony_parts.append("<|start|>system<|message|>You are a helpful assistant.")
            harmony_parts.append("\n\n# Available Tools\n")
            for tool in tools:
                func = tool['function']
                harmony_parts.append(f"## {func['name']}\n")
                if func.get('description'):
                    harmony_parts.append(f"// {func['description']}\n")
            harmony_parts.append("\n# Valid channels: analysis, commentary, final. Use commentary for tool calls.<|end|>")
        
        # Add conversation messages
        for msg in messages:
            role = msg['role']
            content = msg['content']
            
            if role == 'user':
                harmony_parts.append(f"<|start|>user<|message|>{content}<|end|>")
            elif role == 'assistant':
                # For assistant messages, use final channel by default
                harmony_parts.append(f"<|start|>assistant<|channel|>final<|message|>{content}<|end|>")
            elif role == 'tool':
                # Tool results
                tool_name = msg.get('tool_name', 'unknown')
                harmony_parts.append(f"<|start|>{tool_name} to=assistant<|message|>{content}<|end|>")
        
        # Add assistant start for response generation
        harmony_parts.append("<|start|>assistant")
        
        return "".join(harmony_parts)

    async def _call_with_harmony_format(
        self,
        messages: list,
        tools: list = None,
        structured_model = None,
        **kwargs
    ) -> "ChatResponse":
        """Call Ollama with Harmony format input using generate API.
        
        Args:
            messages: Standard message format
            tools: Tool definitions
            structured_model: Structured output model
            **kwargs: Additional parameters
            
        Returns:
            ChatResponse with proper content blocks
        """
        # Convert to Harmony format
        harmony_prompt = self._convert_to_harmony_format(messages, tools)
        
        # Prepare generate API call
        generate_kwargs = {
            "model": self.model_name,
            "prompt": harmony_prompt,
            "stream": self.stream,
            "options": self.options,
            "keep_alive": self.keep_alive,
        }
        
        if structured_model:
            generate_kwargs["format"] = structured_model.model_json_schema()
        
        # Use generate API instead of chat API
        start_time = datetime.now()
        response = await self.client.generate(**generate_kwargs)
        
        if self.stream:
            # Return streaming generator for Harmony format
            return self._parse_harmony_stream_response(start_time, response)
        
        # Parse the Harmony format response (non-streaming)
        response_text = response.get('response', '')
        
        # Parse with Harmony parser
        parsed_blocks = self._harmony_parser.parse(response_text)
        content_blocks = []
        
        # The parser already returns proper Block objects, just use them directly
        for block in parsed_blocks:
            # Check if it's already a proper Block object
            if isinstance(block, (TextBlock, ThinkingBlock, ToolUseBlock)):
                content_blocks.append(block)
            # If it's a dict (legacy behavior), convert it
            elif isinstance(block, dict):
                if block.get("type") == "thinking":
                    content_blocks.append(
                        ThinkingBlock(
                            type="thinking",
                            signature="ollama_harmony",
                            thinking=block.get("thinking", ""),
                        ),
                    )
                elif block.get("type") == "text":
                    content_blocks.append(
                        TextBlock(
                            type="text",
                            text=block.get("text", ""),
                        ),
                    )
                elif block.get("type") == "tool_use":
                    content_blocks.append(
                        ToolUseBlock(
                            type="tool_use",
                            id=block.get("id", ""),
                            name=block.get("name", ""),
                            input=block.get("input", {}),
                        ),
                    )
        
        # Fallback if no parsed blocks (add as single text block)
        if not content_blocks and response_text:
            content_blocks.append(
                TextBlock(
                    type="text",
                    text=response_text,
                )
            )
        
        # Create usage info
        usage = None
        if "prompt_eval_count" in response and "eval_count" in response:
            usage = ChatUsage(
                input_tokens=response.get("prompt_eval_count", 0),
                output_tokens=response.get("eval_count", 0),
                time=(datetime.now() - start_time).total_seconds(),
            )
        
        return ChatResponse(
            content=content_blocks,
            usage=usage,
            metadata=None,
        )

    async def _parse_ollama_stream_completion_response(
        self,
        start_datetime: datetime,
        response: AsyncIterator[OllamaChatResponse],
        structured_model: Type[BaseModel] | None = None,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Given an Ollama streaming completion response, extract the
        content blocks and usages from it and yield ChatResponse objects.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`AsyncIterator[OllamaChatResponse]`):
                Ollama streaming response async iterator to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            AsyncGenerator[ChatResponse, None]:
                An async generator that yields ChatResponse objects containing
                the content blocks and usage information for each chunk in the
                streaming response.

        .. note::
            If `structured_model` is not `None`, the expected structured output
            will be stored in the metadata of the `ChatResponse`.

        """
        if self.support_harmony_response:
            async for content_blocks in self._harmony_parser.parse_stream(
                response,
            ):
                # The Harmony format via the /api/generate endpoint does not
                # provide token usage details per chunk. A dummy usage object
                # is created here. The final usage is calculated and
                # returned in the last chunk in _parse_harmony_stream_response.
                yield ChatResponse(
                    content=content_blocks,
                    usage=ChatUsage(
                        input_tokens=0,
                        output_tokens=0, 
                        time=0.0
                    ),
                    metadata=None,
                )
        else:
            accumulated_text = ""
            acc_thinking_content = ""
            tool_calls = OrderedDict()  # Store tool calls
            metadata = None

            async for chunk in response:
                # Handle text content
                msg = chunk.message
                acc_thinking_content += msg.thinking or ""
                accumulated_text += msg.content or ""

                # Handle tool calls
                for idx, tool_call in enumerate(msg.tool_calls or []):
                    function = tool_call.function
                    tool_id = f"{idx}_{function.name}"
                    tool_calls[tool_id] = {
                        "type": "tool_use",
                        "id": tool_id,
                        "name": function.name,
                        "input": function.arguments,
                    }
                # Calculate usage statistics
                current_time = (
                    datetime.now() - start_datetime
                ).total_seconds()
                usage = ChatUsage(
                    input_tokens=getattr(chunk, "prompt_eval_count", 0) or 0,
                    output_tokens=getattr(chunk, "eval_count", 0) or 0,
                    time=current_time,
                )
                # Create content blocks
                contents: list = []

                if acc_thinking_content:
                    contents.append(
                        ThinkingBlock(
                            type="thinking",
                            signature="ollama_thinking",
                            thinking=acc_thinking_content,
                        ),
                    )

                if accumulated_text:
                    contents.append(
                        TextBlock(type="text", text=accumulated_text),
                    )
                    if structured_model:
                        metadata = _json_loads_with_repair(accumulated_text)

                # Add tool call blocks
                for tool_call in tool_calls.values():
                    try:
                        input_data = tool_call["input"]
                        if isinstance(input_data, str):
                            input_data = _json_loads_with_repair(input_data)
                        contents.append(
                            ToolUseBlock(
                                type=tool_call["type"],
                                id=tool_call["id"],
                                name=tool_call["name"],
                                input=input_data,
                            ),
                        )
                    except Exception as e:
                        logger.warning(
                            "Error parsing tool call input: %s",
                            e,
                        )

                # Generate response when there's new content or at final chunk
                if chunk.done and contents:
                    res = ChatResponse(
                        content=contents,
                        usage=usage,
                        metadata=metadata,
                    )
                    yield res

    async def _parse_ollama_completion_response(
        self,
        start_datetime: datetime,
        response: OllamaChatResponse,
        structured_model: Type[BaseModel] | None = None,
    ) -> ChatResponse:
        """Given an Ollama chat completion response object, extract the content
        blocks and usages from it.

        Args:
            start_datetime (`datetime`):
                The start datetime of the response generation.
            response (`OllamaChatResponse`):
                Ollama OllamaChatResponse object to parse.
            structured_model (`Type[BaseModel] | None`, default `None`):
                A Pydantic BaseModel class that defines the expected structure
                for the model's output.

        Returns:
            `ChatResponse`:
                A ChatResponse object containing the content blocks and usage.

        .. note::
            If `structured_model` is not `None`, the expected structured output
            will be stored in the metadata of the `ChatResponse`.
        """
        if self.support_harmony_response:
            parsed_blocks = self._harmony_parser.parse(
                response.message.content,
            )
            content_blocks = []
            metadata = None
            
            # If harmony parsing produced blocks, use them
            if parsed_blocks:
                # Convert dict blocks to proper objects
                for block in parsed_blocks:
                    if block.get("type") == "thinking":
                        content_blocks.append(
                            ThinkingBlock(
                                type="thinking", 
                                signature="ollama_thinking",
                                thinking=block.get("thinking", ""),
                            ),
                        )
                    elif block.get("type") == "text":
                        content_blocks.append(
                            TextBlock(
                                type="text",
                                text=block.get("text", ""),
                            ),
                        )
                    elif block.get("type") == "tool_use":
                        content_blocks.append(
                            ToolUseBlock(
                                type="tool_use",
                                id=block.get("id", ""),
                                name=block.get("name", ""),
                                input=block.get("input", {}),
                            ),
                        )
            else:
                # Fallback to standard parsing if no harmony blocks found
                if response.message.thinking:
                    content_blocks.append(
                        ThinkingBlock(
                            type="thinking",
                            signature="ollama_thinking", 
                            thinking=response.message.thinking,
                        ),
                    )

                if response.message.content:
                    content_blocks.append(
                        TextBlock(
                            type="text",
                            text=response.message.content,
                        ),
                    )
                    if structured_model:
                        metadata = _json_loads_with_repair(
                            response.message.content,
                        )

                for idx, tool_call in enumerate(
                    response.message.tool_calls or [],
                ):
                    content_blocks.append(
                        ToolUseBlock(
                            type="tool_use",
                            id=f"{idx}_{tool_call.function.name}",
                            name=tool_call.function.name,
                            input=tool_call.function.arguments,
                        ),
                    )
        else:
            content_blocks: List[TextBlock | ToolUseBlock | ThinkingBlock] = []
            metadata = None

            if response.message.thinking:
                content_blocks.append(
                    ThinkingBlock(
                        type="thinking",
                        signature="ollama_thinking",
                        thinking=response.message.thinking,
                    ),
                )

            if response.message.content:
                content_blocks.append(
                    TextBlock(
                        type="text",
                        text=response.message.content,
                    ),
                )
                if structured_model:
                    metadata = _json_loads_with_repair(
                        response.message.content,
                    )

            for idx, tool_call in enumerate(
                response.message.tool_calls or [],
            ):
                content_blocks.append(
                    ToolUseBlock(
                        type="tool_use",
                        id=f"{idx}_{tool_call.function.name}",
                        name=tool_call.function.name,
                        input=tool_call.function.arguments,
                    ),
                )

        usage = None
        if "prompt_eval_count" in response and "eval_count" in response:
            usage = ChatUsage(
                input_tokens=response.get("prompt_eval_count", 0),
                output_tokens=response.get("eval_count", 0),
                time=(datetime.now() - start_datetime).total_seconds(),
            )

        parsed_response = ChatResponse(
            content=content_blocks,
            usage=usage,
            metadata=metadata,
        )

        return parsed_response

    async def _parse_harmony_stream_response(
        self,
        start_datetime: datetime,
        response: AsyncIterator,
    ) -> AsyncGenerator[ChatResponse, None]:
        """Parse Harmony format streaming response from generate API.
        
        Args:
            start_datetime: Start time of the request
            response: Streaming response from generate API
            
        Yields:
            ChatResponse objects with Harmony-parsed content
        """
        accumulated_text = ""
        
        try:
            async for chunk in response:
                response_text = chunk.get('response', '')
                done = chunk.get('done', False)
                
                accumulated_text += response_text
                
                if done:
                    # Parse final accumulated text with Harmony parser
                    parsed_blocks = self._harmony_parser.parse(accumulated_text)
                    content_blocks = []
                    
                    # The parser already returns proper Block objects
                    for block in parsed_blocks:
                        if isinstance(block, (TextBlock, ThinkingBlock, ToolUseBlock)):
                            content_blocks.append(block)
                        elif isinstance(block, dict):
                            # Fallback for dict format
                            if block.get("type") == "thinking":
                                content_blocks.append(
                                    ThinkingBlock(
                                        type="thinking",
                                        signature="ollama_harmony",
                                        thinking=block.get("thinking", ""),
                                    ),
                                )
                            elif block.get("type") == "text":
                                content_blocks.append(
                                    TextBlock(
                                        type="text",
                                        text=block.get("text", ""),
                                    ),
                                )
                            elif block.get("type") == "tool_use":
                                content_blocks.append(
                                    ToolUseBlock(
                                        type="tool_use",
                                        id=block.get("id", ""),
                                        name=block.get("name", ""),
                                        input=block.get("input", {}),
                                    ),
                                )
                    
                    # Fallback if no parsed blocks
                    if not content_blocks and accumulated_text:
                        content_blocks.append(
                            TextBlock(
                                type="text",
                                text=accumulated_text,
                            )
                        )
                    
                    # Calculate usage
                    usage = None
                    if "prompt_eval_count" in chunk and "eval_count" in chunk:
                        usage = ChatUsage(
                            input_tokens=chunk.get("prompt_eval_count", 0),
                            output_tokens=chunk.get("eval_count", 0),
                            time=(datetime.now() - start_datetime).total_seconds(),
                        )
                    
                    yield ChatResponse(
                        content=content_blocks,
                        usage=usage,
                        metadata=None,
                    )
                    break
                else:
                    # Yield intermediate response if needed
                    if response_text:
                        yield ChatResponse(
                            content=[TextBlock(type="text", text=response_text)],
                            usage=ChatUsage(input_tokens=0, output_tokens=0, time=0.0),
                            metadata=None,
                        )
        except Exception as e:
            logger.error(f"Error in Harmony streaming: {e}")
            # Fallback to simple text response
            yield ChatResponse(
                content=[TextBlock(type="text", text=accumulated_text)],
                usage=ChatUsage(input_tokens=0, output_tokens=0, time=0.0),
                metadata=None,
            )

    def _format_tools_json_schemas(
        self,
        schemas: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Format the tools JSON schemas to the Ollama format."""
        return schemas
