# -*- coding: utf-8 -*-
"""The MemoryWithCompress class for memory management with compression."""

import asyncio
import concurrent.futures
import copy
import functools
import json
from typing import (
    Any,
    Awaitable,
    Callable,
    Iterable,
    List,
    Optional,
    Sequence,
    TypeVar,
    Union,
)
from pydantic import ValidationError
from _mc_utils import (  # noqa: E402
    count_words,
    format_msgs,
    DEFAULT_COMPRESSION_PROMPT_TEMPLATE,
    MemoryWithCompressionState,
    MemoryCompressionSchema,
)
from _memory_storage import (  # noqa: E402
    InMemoryMessageStorage,
    MessageStorageBase,
)
from agentscope.formatter import FormatterBase
from agentscope.memory import MemoryBase
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from agentscope.token import TokenCounterBase

T = TypeVar("T")


def ensure_health(
    func: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    """Async decorator that ensures the MemoryWithCompress health check
    passes before method execution.

    Args:
        func (`Callable[..., Awaitable[T]]`):
            The async function to be decorated

    Returns:
        `Callable[..., Awaitable[T]]`:
            The wrapped async function with health check
    """

    @functools.wraps(func)
    async def wrapper(self: Any, *args: Any, **kwargs: Any) -> T:
        await self.check_health()
        return await func(self, *args, **kwargs)

    return wrapper


class MemoryWithCompress(MemoryBase):
    """
    MemoryWithCompress is a memory manager that stores original messages
    in chat_history_storage and compressed messages in memory_storage in the
    current conversation session.
    The difference between this memory and longterm memory is that this
    memory is used to store the messages in the current conversation session,
    while longterm memory is used to store the important information across
    multiple conversation sessions.
    """

    def __init__(
        self,
        model: ChatModelBase,
        formatter: FormatterBase,
        max_token: int = 28000,
        chat_history_storage: MessageStorageBase = InMemoryMessageStorage(),
        memory_storage: MessageStorageBase = InMemoryMessageStorage(),
        token_counter: Optional[TokenCounterBase] = None,
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
            model (`ChatModelBase`):
                the model to use for compression
            formatter (`FormatterBase`):
                the formatter to use for formatting messages
            max_token (`int`):
                the maximum token count for memories in memory_storage.
                If exceeded, MemoryWithCompress will compress the memory.
            chat_history_storage (`MessageStorageBase`):
                the storage to use for chat history, default is
                InMemoryMessageStorage. It is used to store the original
                messages in the current conversation session.
            memory_storage (`MessageStorageBase`):
                the storage to use for memory, default is
                InMemoryMessageStorage. It is used to store the compressed
                messages in the current conversation session.
            token_counter (`Optional[TokenCounterBase]`):
                the token counter to use for counting tokens, default
                is None. If None, it will return the character count of
                the JSON string representation of messages (i.e.,
                len(json.dumps(messages, ensure_ascii=False))).
            compress_func (`Callable[[List[Msg]], Awaitable[List[Msg]]]`):
                the function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress
            compression_trigger_func (
                `Callable[[List[Msg]], Awaitable[bool]]`
            ):
                Optional function to trigger compression when token count
                is below max_token. It receives the list of messages in
                memory_storage as input and returns an Awaitable[bool]. If it
                returns True, compression will be triggered even when
                token count hasn't exceeded max_token. If None (default),
                compression only occurs when token count exceeds max_token.
            compression_on_add (`bool`):
                Whether to check and compress the memory when adding messages.
                If True, the memory will be checked for compression needs and
                compressed if necessary. If False, the memory will not be
                compressed on add. Default is False, because when checking
                memory during add operations, compression may not be finished
                yet, and get_memory will return uncompressed memory.
            compression_on_get (`bool`):
                Whether to check and compress the memory when getting messages.
                If True, the memory will be checked for compression needs and
                compressed if necessary. Default is True.
            customized_compression_prompt (`str | None`):
                Optional customized compression prompt template. If None
                (default), the default compression prompt template will be
                used. If a string is provided, it should be a template
                string with placeholders: {max_token}, {messages_list_json},
                {schema_json}. The template will be formatted with these
                values when generating the prompt.
        """
        super().__init__()

        self.chat_history_storage = chat_history_storage
        self.memory_storage = memory_storage
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

    async def check_health(self) -> None:
        """
        Check if the memory system is healthy.
        Verifies that storage backends and model are accessible.
        """
        # Check if chat_history_storage is accessible
        if not await self.chat_history_storage.health():
            await self.chat_history_storage.start()
        if not await self.memory_storage.health():
            await self.memory_storage.start()

    @ensure_health
    async def add(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
        compress_func: Callable[[List[Msg]], Awaitable[List[Msg]]]
        | None = None,
        compression_trigger_func: Callable[[List[Msg]], Awaitable[bool]]
        | None = None,
    ) -> None:
        """
        Add new messages to both chat_history_storage and memory_storage.

        Args:
            msgs (`Union[Sequence[Msg], Msg, None]`):
                Messages to be added.
            compress_func (
                `Callable[[List[Msg]], Awaitable[List[Msg]]] | None`
            ):
                the function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress. If None (default), the default
                compress_func will be used. if provided, it will replace
                the self.compress_func in the add call.
            compression_trigger_func (
                `Callable[[List[Msg]], Awaitable[bool]] | None`
            ):
                Optional function to trigger compression when token count
                is below max_token. It receives the list of messages in
                memory_storage as input and returns an Awaitable[bool]. If it
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

        # Add to chat_history_storage (original messages)
        await self.chat_history_storage.add(deep_copied_msgs)

        # Add to memory_storage (same messages, will be compressed if needed)
        await self.memory_storage.add(deep_copied_msgs)

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
                    await self.memory_storage.replace(compressed_memory)

    @ensure_health
    async def direct_update_memory(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
    ) -> None:
        """
        Directly update the memory with new messages.

        Args:
            msgs (`Union[Sequence[Msg], Msg, None]`):
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
        await self.memory_storage.replace(msg_list)

    @ensure_health
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
        Get memory content. If memory_storage token count exceeds max_token,
        compress all messages into a single message.

        Args:
            recent_n (`Optional[int]`):
                The number of memories to return.
            filter_func (`Optional[Callable[[int, Msg], bool]]`):
                The function to filter memories, which takes the index and
                message as input, and returns a boolean value.
            compress_func (
                `Callable[[List[Msg]], Awaitable[List[Msg]]] | None`
            ):
                the function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress. If None (default), the default
                compress_func will be used. if provided, it will replace
                the self.compress_func in the get_memory call.
            compression_trigger_func (
                `Callable[[List[Msg]], Awaitable[bool]] | None`
            ):
                Optional function to trigger compression when token count
                is below max_token. It receives the list of messages in
                memory_storage as input and returns an Awaitable[bool]. If it
                returns True, compression will be triggered even when
                token count hasn't exceeded max_token. If None (default),
                compression only occurs when token count exceeds max_token.
                If None (default), the self.compression_trigger_func will be
                used. if provided, it will replace the
                self.compression_trigger_func in the get_memory call.

        Returns:
            `list[Msg]`:
                The memory content
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
                    await self.memory_storage.replace(compressed_memory)

        # Apply filter if provided
        memories = await self.memory_storage.get()
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
        """Compress all messages using LLM.

        Args:
            msgs (`List[Msg]`):
                The list of messages to compress

        Returns:
            `List[Msg]`:
                The compressed messages
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
                if not isinstance(structured_data, dict):
                    raise ValueError(
                        f"Expected structured_data to be a dict, "
                        f"got {type(structured_data)}: {structured_data}",
                    )
                try:
                    parsed_schema = MemoryCompressionSchema(**structured_data)
                except ValidationError as e:
                    raise ValueError(
                        f"Failed to parse memory compression schema "
                        f"from stream metadata. "
                        f"Metadata: {structured_data}. "
                        f"Validation errors: "
                        f"{e.errors() if hasattr(e, 'errors') else str(e)}",
                    ) from e
                return [
                    Msg(
                        name="assistant",
                        role="assistant",
                        content=(
                            f"The compression of the previous "
                            f"conversation is: "
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
                if not isinstance(res.metadata, dict):
                    raise ValueError(
                        f"Expected metadata to be a dict, "
                        f"got {type(res.metadata)}: {res.metadata}",
                    )
                try:
                    parsed_schema = MemoryCompressionSchema(**res.metadata)
                except ValidationError as e:
                    raise ValueError(
                        f"Failed to parse memory compression schema "
                        f"from metadata. "
                        f"Metadata: {res.metadata}. "
                        f"Validation errors: "
                        f"{e.errors() if hasattr(e, 'errors') else str(e)}",
                    ) from e
                return [
                    Msg(
                        name="assistant",
                        role="assistant",
                        content=(
                            f"The compression of the previous "
                            f"conversation is: "
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

    @ensure_health
    async def delete(self, indices: Union[Iterable[int], int]) -> None:
        """
        Delete memory fragments.

        Args:
            indices (`Union[Iterable[int], int]`):
                indices of the memory fragments to delete
        """
        await self.memory_storage.delete(indices)

    @ensure_health
    async def _check_length_and_compress(
        self,
        compress_func: Callable[[List[Msg]], Awaitable[List[Msg]]]
        | None = None,
    ) -> bool:
        """
        Check if the memory needs compression by the provided
        compress_func and compress it if needed. If compress_func is called,
        the self._memory_storage will be replaced with the compressed memory.

        Args:
            compress_func (
                `Callable[[List[Msg]], Awaitable[List[Msg]]] | None`
            ):
                the function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress. If None (default), the
                self.compress_func will be used. if provided, it will replace
                the self.compress_func in the check_length_and_compress call.

        Returns:
            `bool`:
                True if compression was triggered, False otherwise
        """
        is_compressed = False
        if compress_func is None:
            compress_func = self.compress_func
        memory_msgs = await self.memory_storage.get()
        if len(memory_msgs) > 0:
            total_tokens = await count_words(
                self.token_counter,
                format_msgs(memory_msgs),
            )
            if total_tokens > self.max_token:
                await self.memory_storage.replace(
                    await compress_func(memory_msgs),
                )
                is_compressed = True
        return is_compressed

    @ensure_health
    async def check_and_compress(
        self,
        compress_func: Callable[[List[Msg]], Awaitable[List[Msg]]]
        | None = None,
        compression_trigger_func: Callable[[List[Msg]], Awaitable[bool]]
        | None = None,
        memory: List[Msg] | None = None,
    ) -> tuple[bool, List[Msg]]:
        """
        Check if the memory needs compression by the provided
        compression_trigger_func and compress it by the provided
        compress_func if needed.

        Args:
            compress_func (
                `Callable[[List[Msg]], Awaitable[List[Msg]]] | None`
            ):
                Optional function to compress the memory, it should return
                an Awaitable[List[Msg]] object, the input is the list
                of messages to compress. If None (default), the
                self.compress_func will be used.
            compression_trigger_func (
                `Callable[[List[Msg]], Awaitable[bool]] | None`
            ):
                Optional function to trigger compression. If None (default),
                the self.compression_trigger_func will be used.
            memory (`List[Msg] | None`):
                The memory to check and compress. If None (default), the
                self.memory_storage will be used.

        Returns:
            `tuple[bool, List[Msg]]`: A tuple containing a boolean value
                    indicating if compression was triggered and the
                    compressed memory. The boolean value is True if
                    compression was triggered, False otherwise. The
                    compressed memory is the list of messages that were
                    compressed. If compression was not triggered, the
                    compressed memory is the same as the input memory.
        """
        # if memory is not provided, use the _memory
        if memory is None:
            memory = await self.memory_storage.get()
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

    @ensure_health
    async def retrieve(self, *args: Any, **kwargs: Any) -> None:
        """
        Retrieve items from the memory.
        This method is not implemented as get_memory is used instead.
        """
        raise NotImplementedError(
            "Use get_memory() instead of retrieve() for MemoryWithCompress",
        )

    @ensure_health
    async def size(self) -> int:
        """
        Get the size of the memory.

        Returns:
            `int`:
                The number of messages in chat_history_storage
        """
        return len(await self.chat_history_storage.get())

    @ensure_health
    async def clear(self) -> None:
        """
        Clear all memory.
        """
        await self.chat_history_storage.clear()
        await self.memory_storage.clear()

    def state_dict(self) -> MemoryWithCompressionState:
        """
        Get the state dictionary of the memory.

        Returns:
            `MemoryWithCompressionState`:
                The state dictionary containing chat_history_storage and
                memory_storage, and max_token
        """

        async def _get_state_dict() -> MemoryWithCompressionState:
            chat_history_state_dict = [
                _.to_dict() for _ in await self.chat_history_storage.get()
            ]
            memory_state_dict = [
                _.to_dict() for _ in await self.memory_storage.get()
            ]
            return MemoryWithCompressionState(
                max_token=self.max_token,
                chat_history_storage=chat_history_state_dict,
                memory_storage=memory_state_dict,
            )

        try:
            # Try to get the running event loop
            asyncio.get_running_loop()
            # If we're in a running event loop, we need to create a new one
            # in a separate thread or use nest_asyncio
            # For simplicity, we'll create a new event loop
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _get_state_dict())
                return future.result()
        except RuntimeError:
            # No running event loop, safe to use asyncio.run
            return asyncio.run(_get_state_dict())

    def load_state_dict(
        self,
        state_dict: MemoryWithCompressionState,
        strict: bool = True,  # pylint: disable=unused-argument
    ) -> None:
        """
        Load the state dictionary of the memory.

        Args:
            state_dict (`MemoryWithCompressionState`):
                The state dictionary to load
            strict (`bool`):
                Whether to strictly enforce that the keys in state_dict
                match the keys returned by state_dict()
        """

        async def _load_state_dict() -> None:
            if state_dict.chat_history_storage:
                await self.chat_history_storage.replace(
                    [
                        Msg.from_dict(msg)
                        for msg in state_dict.chat_history_storage
                    ],
                )
            if state_dict.memory_storage:
                await self.memory_storage.replace(
                    [Msg.from_dict(msg) for msg in state_dict.memory_storage],
                )
            if state_dict.max_token:
                self.max_token = state_dict.max_token

        try:
            # Try to get the running event loop
            asyncio.get_running_loop()
            # If we're in a running event loop, we need to create a new one
            # in a separate thread or use nest_asyncio
            # For simplicity, we'll create a new event loop
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, _load_state_dict())
                future.result()
        except RuntimeError:
            # No running event loop, safe to use asyncio.run
            asyncio.run(_load_state_dict())
