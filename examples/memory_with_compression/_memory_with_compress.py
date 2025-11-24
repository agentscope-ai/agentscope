# -*- coding: utf-8 -*-
"""The MemoryWithCompress class for memory management with compression."""

import copy
import json
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    List,
    Optional,
    Sequence,
    Union,
)

from agentscope.formatter import FormatterBase
from agentscope.memory import MemoryBase
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from agentscope.token import OpenAITokenCounter, TokenCounterBase

from _mc_utils import (  # noqa: E402  # pylint: disable=wrong-import-order
    count_words,
    format_msgs,
    DEFAULT_COMPRESSION_PROMPT_TEMPLATE,
    MemoryCompressionSchema,
)


class MemoryWithCompress(MemoryBase):
    """
    MemoryWithCompress is a memory manager that stores original messages
    in _chat_history and compressed messages in _memory.
    """

    def __init__(
        self,
        model: ChatModelBase,
        formatter: FormatterBase,
        max_token: int = 28000,
        token_counter: Optional[TokenCounterBase] = OpenAITokenCounter,
        compress_func: Callable[[List[Msg]], Awaitable[List[Msg]]]
        | None = None,
        compression_trigger_func: Callable[[List[Msg]], Awaitable[bool]]
        | None = None,
        compression_on_add: bool = False,
        compression_on_get: bool = True,
        customized_compression_prompt: str | None = None,
    ) -> None:
        """Initialize the MemoryWithCompress.

        Args:
            model (ChatModelBase):
                the model to use for compression
            formatter (FormatterBase):
                the formatter to use for formatting messages
            max_token (int):
                the maximum token count for _memory. If exceeded,
                MemoryWithCompress will compress the memory.
            token_counter (Optional[TokenCounterBase]):
                the token counter to use for counting tokens
            compress_func (Callable[[List[Msg]], Awaitable[List[Msg]]]):
                the function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress
            compression_trigger_func (Callable[[List[Msg]], Awaitable[bool]]):
                Optional function to trigger compression when token count
                is below max_token. It receives the list of messages in
                _memory as input and returns an Awaitable[bool]. If it
                returns True, compression will be triggered even when
                token count hasn't exceeded max_token. If None (default),
                compression only occurs when token count exceeds max_token.
            compression_on_add (bool):
                Whether to check and compress the memory when adding messages.
                If True, the memory will be checked for compression needs and
                compressed if necessary. If False, the memory will not be
                compressed on add. Default is False, because when checking
                memory during add operations, compression may not be finished
                yet, and get_memory will return uncompressed memory.
            compression_on_get (bool):
                Whether to check and compress the memory when getting messages.
                If True, the memory will be checked for compression needs and
                compressed if necessary. Default is True.
            customized_compression_prompt (str | None):
                Optional customized compression prompt template. If None
                (default), the default compression prompt template will be
                used. If a string is provided, it should be a template
                string with placeholders: {max_token}, {messages_list_json},
                {schema_json}. The template will be formatted with these
                values when generating the prompt.
        """
        super().__init__()

        self._chat_history: List[Msg] = []
        self._memory: List[Msg] = []
        self.customized_compression_prompt = customized_compression_prompt

        self.model = model
        self.formatter = formatter
        self.max_token = max_token
        self.token_counter = token_counter
        self.compress_func = (
            compress_func
            if compress_func is not None
            else self._compress_memory
        )
        self.compression_trigger_func = compression_trigger_func
        self.compression_on_add = compression_on_add
        self.compression_on_get = compression_on_get

    async def add(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
        compress_func: Callable[[List[Msg]], Awaitable[List[Msg]]]
        | None = None,
        compression_trigger_func: Callable[[List[Msg]], Awaitable[bool]]
        | None = None,
    ) -> None:
        """
        Add new messages to both _chat_history and _memory.

        Args:
            msgs (Union[Sequence[Msg], Msg, None]):
                Messages to be added.
            compress_func (Callable[[List[Msg]], Awaitable[List[Msg]]]):
                the function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress. If None (default), the default
                compress_func will be used. if provided, it will replace
                the self.compress_func in the add call.
            compression_trigger_func (Callable[[List[Msg]], Awaitable[bool]]):
                Optional function to trigger compression when token count
                is below max_token. It receives the list of messages in
                _memory as input and returns an Awaitable[bool]. If it
                returns True, compression will be triggered even when
                token count hasn't exceeded max_token. If None (default),
                compression only occurs when token count exceeds max_token.
                If provided, it will replace the self.compression_trigger_func
                in the add call.
        """
        if msgs is None:
            return

        # Convert to list if single message
        if not isinstance(msgs, Sequence):
            msgs = [msgs]

        # Ensure all items are Msg objects
        msg_list: List[Msg] = []
        for msg in msgs:
            if not isinstance(msg, Msg):
                raise TypeError(f"Expected Msg object, got {type(msg)}")
            msg_list.append(msg)

        # Deep copy messages to avoid modifying originals
        deep_copied_msgs: List[Msg] = copy.deepcopy(msg_list)

        # Add to _chat_history (original messages)
        self._chat_history.extend(deep_copied_msgs)

        # Add to _memory (same messages, will be compressed if needed)
        self._memory.extend(deep_copied_msgs)

        if self.compression_on_add:
            # first check the total token of the memory is greater than
            # max_token and compress it if needed
            compressed = await self._check_length_and_compress(
                compress_func
                if compress_func is not None
                else self.compress_func,
            )
            if not compressed:
                # if the memory is not compressed, check if it needs
                # compression and compress it if needed
                compressed, compressed_memory = await self.check_and_compress(
                    compress_func
                    if compress_func is not None
                    else self.compress_func,
                    compression_trigger_func
                    if compression_trigger_func is not None
                    else self.compression_trigger_func,
                )
                if compressed:
                    self._memory = compressed_memory

    async def direct_update_memory(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
    ) -> None:
        """
        Directly update the memory with new messages.

        Args:
            msgs (Union[Sequence[Msg], Msg, None]):
                Messages to be added.

        """
        if msgs is None:
            return
        if not isinstance(msgs, Sequence):
            msgs = [msgs]
        # Ensure all items are Msg objects
        msg_list: List[Msg] = []
        for msg in msgs:
            if not isinstance(msg, Msg):
                raise TypeError(f"Expected Msg object, got {type(msg)}")
            msg_list.append(msg)
        # Deep copy messages to avoid modifying originals
        self._memory = copy.deepcopy(msg_list)

    async def get_memory(
        self,
        recent_n: Optional[int] = None,
        filter_func: Optional[Callable[[int, Msg], bool]] = None,
        compress_func: Callable[[List[Msg]], Awaitable[List[Msg]]]
        | None = None,
        compression_trigger_func: Callable[[List[Msg]], Awaitable[bool]]
        | None = None,
    ) -> list[Msg]:
        """
        Get memory content. If _memory token count exceeds max_token,
        compress all messages into a single message.

        Args:
            recent_n (Optional[int]):
                The number of memories to return.
            filter_func (Optional[Callable[[int, Msg], bool]]):
                The function to filter memories, which takes the index and
                message as input, and returns a boolean value.
            compress_func (Callable[[List[Msg]], Awaitable[List[Msg]]]):
                the function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress. If None (default), the default
                compress_func will be used. if provided, it will replace
                the self.compress_func in the get_memory call.
            compression_trigger_func (Callable[[List[Msg]], Awaitable[bool]]):
                Optional function to trigger compression when token count
                is below max_token. It receives the list of messages in
                _memory as input and returns an Awaitable[bool]. If it
                returns True, compression will be triggered even when
                token count hasn't exceeded max_token. If None (default),
                compression only occurs when token count exceeds max_token.
                If None (default), the self.compression_trigger_func will be
                used. if provided, it will replace the
                self.compression_trigger_func in the get_memory call.

        Returns:
            list[Msg]: The memory content.
        """
        if self.compression_on_get:
            # first check the total token of the memory is greater than
            # max_token and compress it if needed
            compressed = await self._check_length_and_compress(compress_func)
            if not compressed:
                # if the memory is not compressed, check if it needs
                # compression and compress it if needed
                compressed, compressed_memory = await self.check_and_compress(
                    compress_func,
                    compression_trigger_func,
                )
                if compressed:
                    self._memory = compressed_memory

        # Apply filter if provided
        memories = self._memory
        if filter_func is not None:
            filtered_memories = [
                msg for i, msg in enumerate(memories) if filter_func(i, msg)
            ]
        else:
            filtered_memories = memories

        # Return recent_n messages if specified
        if recent_n is not None and recent_n > 0:
            # Type assertion: recent_n is guaranteed to be int here
            assert recent_n is not None  # For type narrowing
            n: int = recent_n
            if n < len(filtered_memories):
                # pylint: disable=invalid-unary-operand-type
                return filtered_memories[-n:]
            return filtered_memories
        return filtered_memories

    async def _compress_memory(self, msgs: List[Msg]) -> List[Msg]:
        """
        Compress all messages using LLM.

        Returns:
            List[Msg]: The compressed messages.
        """
        # Format all messages for compression
        messages_list = format_msgs(msgs)

        # Prepare template variables
        messages_list_json = json.dumps(
            messages_list,
            ensure_ascii=False,
            indent=2,
        )
        schema_json = json.dumps(
            MemoryCompressionSchema.model_json_schema(),
            ensure_ascii=False,
            indent=2,
        )

        # Generate compression prompt using template
        prompt_template = (
            self.customized_compression_prompt
            if self.customized_compression_prompt is not None
            else DEFAULT_COMPRESSION_PROMPT_TEMPLATE
        )
        compression_prompt = prompt_template.format(
            max_token=self.max_token,
            messages_list_json=messages_list_json,
            schema_json=schema_json,
        )

        # Call the model to compress
        # Format the message using the formatter
        prompt_msg = Msg("user", compression_prompt, "user")
        formatted_prompt = await self.formatter.format([prompt_msg])

        # Use structured_model parameter (not response_schema)
        res = await self.model(
            formatted_prompt,
            structured_model=MemoryCompressionSchema,
        )

        # Extract structured output from metadata
        # The structured output is stored in res.metadata as a dict
        if self.model.stream:
            structured_data = None
            async for content_chunk in res:
                if content_chunk.metadata:
                    structured_data = content_chunk.metadata
            if structured_data:
                # Validate and parse the structured output
                parsed_schema = MemoryCompressionSchema(**structured_data)
                return [
                    Msg(
                        name="assistant",
                        role="assistant",
                        content=(
                            f"The compressed of previous conversation is: "
                            f"<compressed_memory>\n"
                            f"{parsed_schema.compressed_text}"
                            f"\n</compressed_memory>"
                        ),
                    ),
                ]
            else:
                raise ValueError(
                    "No structured output found in stream response",
                )
        else:
            if res.metadata:
                # Validate and parse the structured output
                parsed_schema = MemoryCompressionSchema(**res.metadata)
                return [
                    Msg(
                        name="assistant",
                        role="assistant",
                        content=(
                            f"The compressed of previous conversation is: "
                            f"<compressed_memory>\n"
                            f"{parsed_schema.compressed_text}"
                            f"\n</compressed_memory>"
                        ),
                    ),
                ]
            else:
                raise ValueError(
                    "No structured output found in response metadata",
                )

    async def delete(self, index: Union[Iterable, int]) -> None:
        """
        Delete memory fragments.

        Args:
            index (Union[Iterable, int]):
                indices of the memory fragments to delete
        """
        if isinstance(index, int):
            indices = [index]
        else:
            indices = list(index)

        # Sort indices in descending order to avoid index shifting
        indices.sort(reverse=True)

        # Delete from both _chat_history and _memory
        for idx in indices:
            if 0 <= idx < len(self._chat_history):
                self._chat_history.pop(idx)
            if 0 <= idx < len(self._memory):
                self._memory.pop(idx)

    async def _check_length_and_compress(
        self,
        compress_func: Callable[[List[Msg]], Awaitable[List[Msg]]]
        | None = None,
    ) -> bool:
        """
        Check if the memory needs compression and compress it if needed.
        """
        is_compressed = False
        if compress_func is None:
            compress_func = self.compress_func
        if len(self._memory) > 0:
            total_tokens = await count_words(
                self.token_counter,
                format_msgs(self._memory),
            )
            if total_tokens > self.max_token:
                self._memory = await compress_func(self._memory)
                is_compressed = True
        return is_compressed

    async def check_and_compress(
        self,
        compress_func: Callable[[List[Msg]], Awaitable[List[Msg]]]
        | None = None,
        compression_trigger_func: Callable[[List[Msg]], Awaitable[bool]]
        | None = None,
        memory: List[Msg] | None = None,
    ) -> tuple[bool, List[Msg]]:
        """
        Check if the memory needs compression and compress it if needed.

        Args:
            compress_func (Callable[[List[Msg]], Awaitable[List[Msg]]]):
                Optional function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress. If None (default), the
                self.compress_func will be used.
            compression_trigger_func (Callable[[List[Msg]], Awaitable[bool]]):
                Optional function to trigger compression. If None (default),
                the self.compression_trigger_func will be used.
            memory (List[Msg] | None):
                The memory to check and compress. If None (default), the
                _memory will be used.

        Returns:
            tuple[bool, List[Msg]]: A tuple containing a boolean value
                    indicating if compression was triggered and the
                    compressed memory. The boolean value is True if
                    compression was triggered, False otherwise. The
                    compressed memory is the list of messages that were
                    compressed. If compression was not triggered, the
                    compressed memory is the same as the input memory.
        """
        # if memory is not provided, use the _memory
        if memory is None:
            memory = copy.deepcopy(self._memory)
        # if compress_func is not provided, use the self.compress_func
        if compress_func is None:
            compress_func = self.compress_func
        # if compression_trigger_func is not provided, use the self.
        # compression_trigger_func
        if compression_trigger_func is None:
            compression_trigger_func = self.compression_trigger_func

        # check if the memory needs compression by compression_trigger_func
        # and compress it if needed.
        # Notice that compression_trigger_func is optional,
        # so if it is not provided, the memory will not be compressed
        # by compression_trigger_func.
        if compression_trigger_func is not None:
            should_compress = await compression_trigger_func(memory)
        else:
            should_compress = False

        if should_compress:
            compressed_memory = await compress_func(memory)
        else:
            compressed_memory = memory
        return should_compress, compressed_memory

    async def retrieve(self, *args: Any, **kwargs: Any) -> None:
        """
        Retrieve items from the memory.
        This method is not implemented as get_memory is used instead.
        """
        raise NotImplementedError(
            "Use get_memory() instead of retrieve() for MemoryWithCompress",
        )

    async def size(self) -> int:
        """
        Get the size of the memory.

        Returns:
            int: The number of messages in _chat_history.
        """
        return len(self._chat_history)

    async def clear(self) -> None:
        """
        Clear all memory.
        """
        self._chat_history = []
        self._memory = []

    def state_dict(self) -> dict:
        """
        Get the state dictionary of the memory.

        Returns:
            dict: The state dictionary containing _chat_history and _memory.
        """
        return {
            "chat_history": [msg.to_dict() for msg in self._chat_history],
            "memory": [msg.to_dict() for msg in self._memory],
            "max_token": self.max_token,
        }

    def load_state_dict(
        self,
        state_dict: dict,
        strict: bool = True,  # pylint: disable=unused-argument
    ) -> None:
        """
        Load the state dictionary of the memory.

        Args:
            state_dict (dict):
                The state dictionary to load.
            strict (bool):
                Whether to strictly enforce that the keys in state_dict
                match the keys returned by state_dict().
        """
        if "chat_history" in state_dict:
            self._chat_history = [
                Msg.from_dict(msg) for msg in state_dict["chat_history"]
            ]
        if "memory" in state_dict:
            self._memory = [Msg.from_dict(msg) for msg in state_dict["memory"]]
        if "max_token" in state_dict:
            self.max_token = state_dict["max_token"]
