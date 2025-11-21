# -*- coding: utf-8 -*-
"""The MemoryWithCompress class for memory management with compression."""

import copy
from typing import Any, Callable, Iterable, List, Optional, Sequence, Union

from agentscope.formatter import FormatterBase
from agentscope.memory import MemoryBase
from agentscope.message import Msg
from agentscope.model import ChatModelBase
from agentscope.token import OpenAITokenCounter, TokenCounterBase

from examples.react_memory._mc_utils import count_words, format_msgs


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
        compress_func: Callable[[List[Msg]], Msg] | None = None,
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
            compress_func (Callable[[List[Msg]], Msg]):
                the function to compress the memory, it should return a Msg
                object, the input is the list of messages to compress
        """
        super().__init__()

        self._chat_history: List[Msg] = []
        self._memory: List[Msg] = []

        self.model = model
        self.formatter = formatter
        self.max_token = max_token
        self.token_counter = token_counter
        self.compress_func = (
            compress_func
            if compress_func is not None
            else self._compress_memory
        )

    async def add(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
    ) -> None:
        """
        Add new messages to both _chat_history and _memory.

        Args:
            msgs (Union[Sequence[Msg], Msg, None]):
                Messages to be added.
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

    async def get_memory(
        self,
        recent_n: Optional[int] = None,
        filter_func: Optional[Callable[[int, Msg], bool]] = None,
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

        Returns:
            list[Msg]: The memory content.
        """
        # Check if _memory needs compression
        if len(self._memory) > 0:
            # Calculate total token count of _memory
            total_tokens = await count_words(
                self.token_counter,
                format_msgs(self._memory),
            )

            if total_tokens > self.max_token:
                # Compress all messages in _memory
                # Replace all messages with a single compressed message
                compressed_msg = await self._compress_memory()
                self._memory = [compressed_msg]

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

    async def _compress_memory(self) -> Msg:
        """
        Compress all messages in _memory using LLM.

        Returns:
            Msg: The compressed message.
        """
        # Format all messages for compression
        messages_text = format_msgs(self._memory)

        # Create a prompt for compression
        from examples.react_memory._mc_utils import MemoryCompressionSchema

        compression_prompt = (
            f"You are a memory compression assistant. Please summarize and "
            f"compress the following conversation history into a concise "
            f"summary that preserves the key information. \n\n You should "
            f"compress the conversation into with less than {self.max_token} "
            f"tokens. The summary should be in the following json format:\n\n"
            f"{MemoryCompressionSchema.model_json_schema()}"
            f"\n\nThe conversation history is:\n\n{messages_text}"
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
                return Msg(
                    name="assistant",
                    role="assistant",
                    content=(
                        f"The compressed of previous conversation is: "
                        f"<compressed_memory>\n{parsed_schema.compressed_text}"
                        f"\n</compressed_memory>"
                    ),
                )
            else:
                raise ValueError(
                    "No structured output found in stream response",
                )
        else:
            if res.metadata:
                # Validate and parse the structured output
                parsed_schema = MemoryCompressionSchema(**res.metadata)
                return Msg(
                    name="assistant",
                    role="assistant",
                    content=(
                        f"The compressed of previous conversation is: "
                        f"<compressed_memory>\n{parsed_schema.compressed_text}"
                        f"\n</compressed_memory>"
                    ),
                )
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
