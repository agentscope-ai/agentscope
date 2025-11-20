# -*- coding: utf-8 -*-
# pylint: disable=C0301, C0411, R1702, C0302, R0912, R0915, W1203, R0904, W0622
# pylint: disable=R1728
"""The ReActMemory class for memory management in agents."""

import logging
import os
import json
from datetime import datetime
import uuid
import asyncio
from typing import (
    Any,
    Callable,
    Iterable,
    List,
    Literal,
    Optional,
    Sequence,
    Union,
    cast,
)
import copy
import re
from agentscope.formatter import DashScopeChatFormatter
from agentscope.model import DashScopeChatModel
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.agent import AgentBase
from agentscope.memory import MemoryBase
from agentscope.message import Msg
from agentscope.token import TokenCounterBase, OpenAITokenCounter
from agentscope.tool import ToolResponse, TextBlock
from ._mem_record import (  # pylint: disable=relative-beyond-top-level
    MemRecord,
    serialize_list,
    deserialize_memrecord_list,
    deserialize_msg_list,
)
from .vector_factories.base import VectorStoreBase
from ._react_memory_utils import (  # pylint: disable=relative-beyond-top-level
    create_recursive_text_splitter,
    time_order_check,
    format_msgs,
    count_words,
    no_user_msg,
    DEFAULT_MAX_CHAT_HISTORY_LEN,
    DEFAULT_RETURN_CHAT_HISTORY_LEN,
    MAX_CHUNK_SIZE,
    OVERLAP_SIZE,
    ALLOWED_MAX_TOOL_RESULT_LEN,
    DEFAULT_MAX_MEMORY_LEN,
    MAX_CHAT_MODEL_TOKEN_SIZE,
)


class ReActMemory(MemoryBase):
    """
    ReActMemory is a memory manager.
    """

    def __init__(
        self,
        model_config_name: str = "gpt-4o-2024-11-20",
        embedding_model: Union[str, Callable] = "text-embedding-v4",
        retrieve_type: Optional[
            Literal["source", "processed", "auto"]
        ] = "auto",
        vector_store: Optional[VectorStoreBase] = None,
        max_chat_len: int = DEFAULT_MAX_CHAT_HISTORY_LEN,
        max_memory_len: int = DEFAULT_MAX_MEMORY_LEN,
        update_memory_prompt: Optional[str] = "PLEASE_INPUT_YOUR_PROMPT",
        summary_working_log_prompt: Optional[str] = "PLEASE_INPUT_YOUR_PROMPT",
        summary_working_log_w_query_prompt: Optional[
            str
        ] = "PLEASE_INPUT_YOUR_PROMPT",
        global_update_allowed: Optional[bool] = False,
        process_w_llm: Optional[bool] = False,
        mount_dir: Optional[str] = "./",
        compressed_ratio: float = 0.5,
        token_counter: Optional[TokenCounterBase] = OpenAITokenCounter,
    ) -> None:
        """
        Initialize the ReActMemory.

        Args:
            model_config_name (str):
                the config name of the model to use for updating memory
            embedding_model (Union[str, Callable]):
                the embedding model to use for embedding the memories
                if str: the config name of the model to use for embedding
                if Callable: the embedding model function
            retrieve_type (Literal["source", "processed", "auto"]):
                the type of retrieval to perform when calling `get_memory`:
                "source" to retrieve the exact chat history (`_chat_history`),
                "processed" to retrieve the processed memories (`_memory`),
                "auto" to use the retrieval type according to `max_word_size`,
                or None to use the instance's default
            vector_store (Optional[VectorStoreBase]):
                the vector database to use for storing and retrieving memories
            max_chat_len (int):
                when using "auto" retrieval type, if the estimated word size
                of the chat history is greater than this value, the processed
                memories (`_memory`) will be retrieved instead of the chat
                history (`_chat_history`).
            max_memory_len (int):
                the maximum length of `_memory` to keep. If exceeded,
                ReActMemory will call `summarize_global` to compress the
                memory.
            update_memory_prompt (Optional[str]):
                the prompt for llm to update memory according to the chat
                history and new chat message
            summary_working_log_prompt (Optional[str]):
                the prompt for llm to summarize the working log.
        """
        super().__init__()

        self._chat_history = []
        self._memory = []

        # prepare embedding model if needed
        self.embedding_model = DashScopeTextEmbedding(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name=embedding_model,
        )

        self.model = DashScopeChatModel(
            api_key=os.environ.get("DASHSCOPE_API_KEY"),
            model_name=model_config_name,
            stream=True,
        )
        self.memory_retrieve_type = retrieve_type
        self.vector_store = vector_store
        self.tmp_tool_use_log = []
        self.cur_chat_len, self.cur_memory_len = 0, 0
        self.max_chat_len, self.max_memory_len = max_chat_len, max_memory_len
        self.update_memory_prompt = update_memory_prompt
        self.token_counter = token_counter
        self.text_splitter = create_recursive_text_splitter(
            chunk_size=MAX_CHAT_MODEL_TOKEN_SIZE,
            chunk_overlap=OVERLAP_SIZE,
            length_function=lambda x: count_words(self.token_counter, x),
        )
        self.summary_working_log_prompt = summary_working_log_prompt
        self.summary_working_log_w_query_prompt = (
            summary_working_log_w_query_prompt
        )
        self.global_update_allowed = global_update_allowed
        self.process_w_llm = process_w_llm
        self.mount_dir = mount_dir
        self.formatter = DashScopeChatFormatter()
        self.log_cnt = 0
        self.agent = AgentBase()
        self.compressed_ratio = compressed_ratio

    async def _truncate_text(
        self,
        text: str,
        max_token_length: int = MAX_CHUNK_SIZE,
    ) -> str:
        """Truncate text to fit within max_token_length using binary search.

        Args:
            text (str): The text to truncate
            max_token_length (int): Maximum number of tokens allowed

        Returns:
            str: Truncated text that fits within the token limit
        """
        # Check if text already fits within the limit
        current_tokens = await count_words(self.token_counter, text)
        if current_tokens <= max_token_length:
            return text

        # Use binary search to find the appropriate truncation point
        left, right = 0, len(text)
        best_text = text

        while left < right:
            mid = (left + right + 1) // 2
            truncated = text[:mid]
            token_count = await count_words(self.token_counter, truncated)

            if token_count <= max_token_length:
                best_text = truncated
                left = mid
            else:
                right = mid - 1

        return best_text

    async def get_memory(
        self,
        recent_n: Optional[int] = DEFAULT_RETURN_CHAT_HISTORY_LEN,
        filter_func: Optional[Callable[[int, dict], bool]] = None,
        post_process_func: Optional[Callable[[List[MemRecord]], Any]] = None,
        retrieve_type: Optional[Literal["source", "processed", "auto"]] = None,
    ) -> list:
        """Retrieve memory according to the `retrieve_type`, number of memories
        (`recent_n`) and filter function `filter_func`.

        Args:
            recent_n (Optional[int], default `None`):
                The number of memories to return.
            filter_func (Callable[[int, dict], bool], default to `None`):
                The function to filter memories, which take the index and
                memory unit as input, and return a boolean value.
            post_process_func
                (Callable[[List[MemRecord]], Any], default to `None`):
                The function to process memories before returning the memory
                content, which take a list of memory units as input,
                and return the processed memory.
            retrieve_type
                (Literal["source", "processed", "auto"], default to `None`):
                The type of retrieval to perform.
                "source" to retrieve the exact chat history (`_chat_history`),
                "processed" to retrieve the processed memories (`_memory`),
                "auto" to use the retrieval type according to `max_word_size`,
                or None to use the instance's default.

        Returns:
            list:
                A list of retrieved memories, filtered and processed according
                to `filter_func` and `post_process_func`.
        Raises:
            ValueError: the retrieve_type is invalid
        """
        # Use instance's default retrieve_type if None is provided
        if retrieve_type is None:
            retrieve_type = self.memory_retrieve_type
        if recent_n is None or recent_n <= 0:
            logging.warning(
                "The retrieved number of memories is set to None or "
                "less than or equal to 0, returning "
                f"{DEFAULT_RETURN_CHAT_HISTORY_LEN} memories by default.",
            )
            recent_n = DEFAULT_RETURN_CHAT_HISTORY_LEN
        # At this point, recent_n is guaranteed to be an int
        recent_n = cast(int, recent_n)
        # extract the recent `recent_n` entries in memories
        if retrieve_type == "source":
            memories = self._chat_history
        elif retrieve_type == "processed":
            if self.process_w_llm:
                if len(self.tmp_tool_use_log) > 0:
                    msg_to_add, msg_to_keep = [], []
                    for msg in self.tmp_tool_use_log:
                        content = msg.content
                        if isinstance(content, str):
                            msg_to_add.append(msg)
                        elif isinstance(content, list):
                            tmp_content = [
                                c
                                for c in content
                                if c.get("type", None) != "tool_use"
                            ]
                            content = [
                                c
                                for c in content
                                if c.get("type", None) == "tool_use"
                            ]
                            if len(tmp_content) > 0:
                                msg_to_add.append(
                                    Msg(
                                        name=msg.name,
                                        role=msg.role,
                                        content=tmp_content,
                                    ),
                                )
                            if len(content) > 0:
                                msg_to_keep.append(msg)
                                msg.content = content
                    if len(msg_to_add) > 0:
                        await self.add(msg_to_add)
                    self.tmp_tool_use_log = msg_to_keep
                memories = self._memory
            else:
                memories = self._memory

        elif retrieve_type == "auto":
            if self.cur_chat_len > self.max_chat_len:
                memories = self._memory
                retrieve_type = "processed"
            else:
                memories = self._chat_history
                retrieve_type = "source"
        else:
            raise ValueError(
                f"Invalid retrieve_type: {retrieve_type}. "
                "Must be 'source', 'processed', 'auto', or None.",
            )

        if filter_func is not None:
            filtered_memories = [
                _ for i, _ in enumerate(memories) if filter_func(i, _)
            ]
        else:
            filtered_memories = memories
        if retrieve_type == "processed":
            if not time_order_check(filtered_memories):
                logging.warning(
                    "The memories are not in time order, the memories are: "
                    f"{format_msgs(filtered_memories)}",
                )
        if post_process_func is not None:
            processed_memories = post_process_func(filtered_memories)
        else:
            processed_memories = [
                self._recover_msg(msg) for msg in filtered_memories
            ]
        if recent_n < len(processed_memories):
            logging.warning(
                f"The requested number of memories is less"
                f"than the total number of memories, returning"
                f"the user request and the last {recent_n-1} memories.",
            )
            returned_memories = [processed_memories[0]]  # the user request
            returned_memories.extend(processed_memories[-recent_n + 1 :])
        else:
            returned_memories = processed_memories
            # Different from TemporalMemory, ReActMemory will not raise an
            # error when the requested number of memories is greater than the
            # total number of memories.
        return returned_memories

    def _recover_msg(self, mem_record: Union[MemRecord, Msg]) -> Msg:
        """Recover the Msg from the MemRecord.

        Args:
            mem_record (Union[MemRecord, Msg]):
                the memory unit to recover

        Returns:
            Msg: the recovered Msg
        """
        if isinstance(mem_record, Msg):
            return mem_record
        elif isinstance(mem_record, MemRecord):
            msg = Msg(
                name=mem_record.metadata.get("name", "system"),
                role=mem_record.metadata.get("role", "system"),
                content=[],
            )
            content = mem_record.metadata.get("data", None)
            if isinstance(content, str):
                msg.content.append({"type": "text", "text": content})
            elif isinstance(content, list):
                msg.content.extend(content)
            msg.id = mem_record.id
            return msg
        raise TypeError(f"Expected Msg or MemRecord, got {type(mem_record)}")

    async def direct_add_chat_history(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
    ) -> None:
        """Add the chat messages to the `_chat_history` and
        update the estimated word size.

        Args:
            msgs (Union[Sequence[Msg], Msg, None]):
                the messages to add to the chat history
        """
        print(f"Direct adding chat history: {msgs}")
        if msgs is None:
            return
        if not isinstance(msgs, Sequence):
            msgs = [msgs]
        deep_copied_msgs = copy.deepcopy(msgs)
        self._chat_history.extend(deep_copied_msgs)
        token_counts = await asyncio.gather(
            *[
                count_words(
                    self.token_counter,
                    format_msgs(msg, with_id=False),
                )
                for msg in msgs
            ],
        )
        self.cur_chat_len += sum(token_counts)

    async def _retrieve_concerned_messages(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
    ) -> List[Msg]:
        """Extract concerned messages that needed to be processed when adding
        new memory: the messages with tool use will be processed until the tool
        result is received.
        """
        concerned_messages = []
        for msg in msgs:
            content = msg.content
            if isinstance(content, str):
                concerned_messages.append(msg)
                continue
            if any(
                c.get("type", None) == "tool_use" for c in content
            ) and not any(
                c.get("type", None) == "tool_result" for c in content
            ):
                self.tmp_tool_use_log.append(copy.deepcopy(msg))
            elif len(self.tmp_tool_use_log) > 0 and any(
                c.get("type", None) == "tool_result" for c in content
            ):
                last_tool_use_log = self.tmp_tool_use_log[-1]
                content_to_add = []
                match_flag = False
                for c in content:
                    if c.get("type", None) == "tool_result":
                        tool_id = c["id"]
                        for idx, pre_content in enumerate(
                            last_tool_use_log.content,
                        ):
                            if (
                                pre_content.get("type", None) == "tool_use"
                                and pre_content.get("id", None) == tool_id
                            ):
                                match_flag = True
                                content_to_add.append(pre_content)
                                last_tool_use_log.content.pop(idx)
                                break
                if match_flag:
                    for idx, c in enumerate(last_tool_use_log.content):
                        if c.get("type", None) == "text":
                            content_to_add.append(c)
                            last_tool_use_log.content.pop(idx)
                    if len(last_tool_use_log.content) == 0:
                        self.tmp_tool_use_log.pop()
                    new_msg = Msg(
                        name=last_tool_use_log.name,
                        role=last_tool_use_log.role,
                        content=content_to_add + content,
                    )
                    concerned_messages.append(new_msg)
                else:
                    logging.warning(
                        "The tool id does not match the last tool use log"
                        "content, last_tool_use_log: "
                        f"{last_tool_use_log.content}, new tool result: "
                        f"{content}",
                    )
            else:
                concerned_messages.append(msg)
        return concerned_messages

    async def _long_context_process(
        self,
        messages: List[Msg],
    ) -> List[Msg]:
        # process long-context tool_result
        for msg in messages:
            content = msg.content
            file_list = []
            if not isinstance(content, str):
                for c in content:
                    if c.get("type", None) == "tool_result":
                        cnt = await count_words(
                            self.token_counter,
                            c.get("output", None),
                        )
                        if cnt > ALLOWED_MAX_TOOL_RESULT_LEN:
                            path = (
                                f"tool_result_"
                                f"{c.get('id', uuid.uuid4().hex)}.md"
                            )
                            file_list.append(path)
                            self._save_to_file(path, c.get("output", None))
                            summary = (
                                await (
                                    self._tool_sequential_summarize_w_query(
                                        c.get("output", None),
                                        f"{c.get('name', None)}"
                                        f" for {c.get('input', None)}",
                                    )
                                )
                            )
                            c["output"] = (
                                f"{summary}. The original tool result is "
                                f"saved in {path}."
                            )
            msg_token_count = await count_words(
                self.token_counter,
                format_msgs(msg, with_id=False),
            )
            if msg_token_count > MAX_CHUNK_SIZE:
                logging.warning(
                    f"The message is too long even after storing tool result "
                    f"in files, the message is: {format_msgs(msg)}",
                )
                # deal with any cases that the message is too long
                if isinstance(msg.content, str):
                    new_content = [{"type": "text", "text": msg.content}]
                else:
                    new_content = msg.content
                msg.content = []
                for c in new_content:
                    if c.get("type", None) in ["tool_use", "tool_result"]:
                        msg.content.append(c)
                    else:
                        # summarize the "text" type content,
                        # create a file to save the original content
                        path = f"reasoning_{c.get('id', uuid.uuid4().hex)}.md"
                        file_list.append(path)
                        self._save_to_file(path, c.get("text", None))
                        summary = await self._tool_sequential_summarize(
                            c.get("text", None),
                        )
                        c["text"] = (
                            f"{summary} For more details of the "
                            f"original reasoning process, please refer to "
                            f"{path}."
                        )
                        msg.content.append(c)
                max_retry = 3
                while max_retry > 0:
                    msg_token_count = await count_words(
                        self.token_counter,
                        format_msgs(msg, with_id=False),
                    )
                    if msg_token_count <= MAX_CHUNK_SIZE:
                        break
                    logging.warning(
                        f"The message is too long even after storing the "
                        f"original `text` type content in files, the message "
                        f"is: {format_msgs(msg)}",
                    )
                    summary_msg = self.summarize_single_message(msg)
                    msg.content = summary_msg.content
                    max_retry -= 1
                if max_retry == 0:
                    logging.warning(
                        "Failed to summarize the message after 3 retries, "
                        "direct add the new messages to the memory",
                    )
                    msg_token_count = await count_words(
                        self.token_counter,
                        format_msgs(msg, with_id=False),
                    )
                    if msg_token_count > MAX_CHUNK_SIZE:
                        final_content = await self._truncate_text(
                            msg.content,
                            MAX_CHUNK_SIZE,
                        )
                        msg.content = [
                            {
                                "type": "text",
                                "text": (
                                    f"{final_content} For more details, "
                                    f"please refer to these files: "
                                    f"{file_list}."
                                ),
                            },
                        ]
            if file_list:
                msg.content.append(
                    {
                        "type": "source_file",
                        "source_file": file_list,
                    },
                )
        return messages

    async def _pre_process_default(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
    ) -> List[dict]:
        """A default pre process function: Using llm to extract useful info
        and modify previous memory.

        Args:
            msgs (Union[Sequence[Msg], Msg, None]):
                the messages to preprocess

        Returns:
            List[dict]:
                the actions to modify the memory
        """
        if msgs is None:
            return []
        if not isinstance(msgs, Sequence):
            msgs = [msgs]

        concerned_messages = await self._retrieve_concerned_messages(msgs)
        if len(concerned_messages) == 0:
            return []
        concerned_messages = await self._long_context_process(
            concerned_messages,
        )
        # Retrieve related memories from _vector_store for concerned messages
        related_memories = await self.retrieve_from_vector_store(
            concerned_messages,
            top_k=5,
        )
        if not time_order_check(related_memories):
            logging.warning(
                "The retrieved memories are not in time order, "
                f"the memories are: {format_msgs(related_memories)}",
            )
        lastest_mem_id = self._memory[-1].id if len(self._memory) > 0 else None
        # map the related memories to 1,2,3...
        uuid_id_mapping = {}
        ordered_memories = []
        update_allowed = False
        if self.global_update_allowed:
            for idx, mem in enumerate(related_memories):
                uuid_id_mapping[idx] = mem.id
                ordered_memories.append(
                    {
                        "id": idx,
                        "content": mem.payload["data"],
                        "role": mem.payload.get("role", "assistant"),
                    },
                )
                if mem.id == lastest_mem_id:
                    update_allowed = True
        retrived_memories_formatted = json.dumps(ordered_memories)
        memories_prompt = (
            self.update_memory_prompt.replace(
                "{{database}}",
                retrived_memories_formatted,
            )
            .replace("{{new_chat_message}}", format_msgs(concerned_messages))
            .replace("{{update_allowed}}", f"{update_allowed}")
        )
        print(f"Memories prompt: {memories_prompt}")
        max_retry = 3
        new_actions, return_actions = [], []
        while max_retry > 0:
            raw_response = await self.call_model(memories_prompt)
            if raw_response.content[0]["text"]:
                try:
                    # Check if the text matches the pattern
                    # ```json\n[content]\n```
                    # and remove markers if so
                    cleaned_text = raw_response.text
                    print(f"Cleaned text: {cleaned_text}")
                    json_block_pattern = r"^```json\s*\n(.*?)\n```$"
                    match = re.match(
                        json_block_pattern,
                        cleaned_text,
                        re.DOTALL,
                    )
                    if match:
                        cleaned_text = match.group(1)
                    # Prevent JSON from interpreting Unicode escape sequences
                    cleaned_text = re.sub(r"\\u", r"\\\\u", cleaned_text)
                    # Remove trailing commas before closing brackets/braces
                    cleaned_text = re.sub(r",(\s*[}\]])", r"\1", cleaned_text)
                    new_actions = json.loads(cleaned_text)
                    print(f"New actions: {new_actions}")
                    # check legality of the actions
                    if isinstance(new_actions, dict):
                        new_actions = [new_actions]
                    for action in new_actions:
                        if action["type"] == "ADD":
                            return_actions.append(
                                {
                                    "action_type": "ADD",
                                    "role": action.get("role", "assistant"),
                                    "action_content": action["content"],
                                },
                            )
                        elif action["type"] == "UPDATE":
                            if not update_allowed:
                                raise ValueError(
                                    f"The memory {action['id']} is not allowed"
                                    f"to be updated, the content is: "
                                    f"{action['content']}",
                                )
                            if isinstance(action["id"], str):
                                action["id"] = int(action["id"])
                            if (
                                not uuid_id_mapping.get(
                                    action["id"],
                                    None,
                                )
                                == lastest_mem_id
                            ):
                                raise ValueError(
                                    f"The updated memory {action['id']} is "
                                    f"not the latest memory, the latest "
                                    f"memory is {lastest_mem_id}",
                                )
                            return_actions.append(
                                {
                                    "action_type": "UPDATE",
                                    "role": action.get("role", "assistant"),
                                    "id": uuid_id_mapping[action["id"]],
                                    "action_content": action["content"],
                                },
                            )
                        else:
                            raise ValueError(f"Invalid action type: {action}")
                    if len(return_actions) == 0:
                        for msg in concerned_messages:
                            if any(
                                (
                                    isinstance(c, dict)
                                    and c.get(
                                        "type",
                                        None,
                                    )
                                    == "tool_result"
                                )
                                for c in msg.content
                            ):
                                raise ValueError(
                                    "Tool results are found, "
                                    "but no memory actions are returned, ",
                                )
                    break
                except Exception as e:
                    logging.warning(
                        f"Error dealing with actions: {raw_response.text},"
                        f"error: {e}",
                    )
                    max_retry -= 1
                    continue
            max_retry -= 1
        if max_retry == 0:
            logging.warning(
                "Failed to parse the memory actions after 3 retries, "
                "direct add the new messages to the memory",
            )
            return_actions = []
            for msg in concerned_messages:
                return_actions.append(
                    {
                        "action_type": "ADD",
                        "role": msg.role,
                        "action_content": msg.content,
                    },
                )
            return return_actions
        if len(return_actions) == 0:
            for msg in concerned_messages:
                if any(
                    (
                        isinstance(c, dict)
                        and c.get(
                            "type",
                            None,
                        )
                        == "tool_result"
                    )
                    for c in msg.content
                ):
                    logging.warning(
                        "Tool results are found, "
                        "but no memory actions are returned, "
                        "we will add it to the memory directly",
                    )
                    await self.direct_add_memory(msg)
                    continue
        return return_actions

    async def call_model(self, prompt: str) -> Msg:
        """Call the model to get the response."""
        sys_prompt = await self.formatter.format(
            [
                Msg("system", prompt, "system"),
            ],
        )
        res = await self.model(sys_prompt)
        msg = None
        try:
            if self.model.stream:
                msg = Msg("assistant", [], "assistant")
                async for content_chunk in res:
                    msg.content = content_chunk.content
                    await self.agent.print(msg, False)
                await self.agent.print(msg, True)

            else:
                msg = Msg("assistant", list(res.content), "assistant")
                await self.agent.print(msg, True)

            return msg
        except Exception as e:
            raise ValueError(f"Error calling model: {e}") from e

    async def add(
        self,
        msgs: Union[Sequence[Msg], Msg, None],
        pre_process_func: Optional[Callable[[List[Msg]], List[dict]]] = None,
    ) -> None:
        """Add new memory fragment to the memory.

        Args:
            msgs (Union[Sequence[Msg], Msg, None]):
                Messages to be added.
            pre_process_func (Optional[Callable[[List[Msg]], List[dict]]]):
                A function to preprocess the memory before adding it to memory.
                It takes a list of Msg objects and returns a list of actions
                to modify the memory. By default, the function is
                self._pre_process_default
        """
        if msgs is None:
            return
        await self.direct_add_chat_history(msgs)
        if not self.process_w_llm:
            await self.direct_add_memory(msgs)
            return
        if not isinstance(msgs, Sequence):
            msg_to_record = [msgs]
        else:
            msg_to_record = msgs
        logging.info(f"ADD MEMORY:\n{format_msgs(msg_to_record)}")
        if pre_process_func is not None:
            actions = pre_process_func(msg_to_record)
        else:
            actions = await self._pre_process_default(msg_to_record)
        print(f"Actions: {actions}")
        await self._process_actions(actions)
        logging.info(
            f"CURRENT MEMORY:\n{format_msgs(self._memory)}",
        )
        return

    async def _process_actions(self, actions: List[dict]) -> None:
        """Process the actions to modify the memory.

        Args:
            actions (List[dict]):
                the actions to modify the memory
        """

        for action in actions:
            max_retry = 3
            while max_retry > 0:
                try:
                    action_type = action.get("action_type", None)
                    if action["action_content"] is not None:
                        try:
                            if not isinstance(action["action_content"], str):
                                action["action_content"] = json.dumps(
                                    action["action_content"],
                                )
                            embedding_response = await self.embedding_model(
                                [action["action_content"]],
                                return_embedding_only=True,
                            )
                            embedding = embedding_response.embeddings[0]
                        except Exception as e:
                            raise ValueError(
                                "Error embedding the action content when"
                                f"_process_actions, error: {e}",
                            ) from e
                    else:
                        embedding = None
                    if action_type == "ADD":
                        await self._add_one_msg_to_memory(
                            action["action_content"],
                            action.get("role", "assistant"),
                            embedding,
                            str(uuid.uuid4().hex),
                        )
                    elif action_type == "UPDATE":
                        try:
                            existing_mem = self.vector_store.get(
                                vector_id=action["id"],
                            )
                        except Exception as e:
                            raise ValueError(
                                "Error getting existing memory for update"
                                f"action: {action}. Error: {e}",
                            ) from e
                        old_mem = existing_mem.payload.get("data", None)
                        if existing_mem is not None:
                            metadata = {
                                "data": action["action_content"],
                                "role": action.get("role", "system"),
                                "last_modified_timestamp": (
                                    datetime.now().isoformat()
                                ),
                            }
                            self.vector_store.update(
                                vector=embedding,
                                vector_id=action["id"],
                                payload=metadata,
                            )
                            found_mem_idx = -1
                            for idx, mem in enumerate(self._memory):
                                if mem.id == action["id"]:
                                    self._memory[idx].update_data(
                                        metadata,
                                        embedding,
                                    )
                                    found_mem_idx = idx
                                    break
                            new_token_count = await count_words(
                                self.token_counter,
                                action["action_content"],
                            )
                            old_token_count = await count_words(
                                self.token_counter,
                                old_mem,
                            )
                            self.cur_memory_len += (
                                new_token_count - old_token_count
                            )
                            if found_mem_idx != -1:
                                # maintain the order by last_modified_timestamp
                                self._memory.append(
                                    self._memory.pop(found_mem_idx),
                                )
                            else:
                                logging.warning(
                                    "The memory %s is not found in the"
                                    " self._memory",
                                    action["id"],
                                )
                        logging.info(
                            f"OLD: {old_mem} \n NEW: "
                            f"{action['action_content']}",
                        )
                    break
                except Exception as e:
                    print(
                        f"Got Error when _process_actions: {e}, action is"
                        f"{action}",
                    )
                    max_retry -= 1
                    continue
            if max_retry == 0:
                raise ValueError(
                    f"Error processing actions after three retries: {actions}",
                )
        if self.cur_memory_len > self.max_memory_len:
            await self.summarize_global()

    async def summarize_memory_w_id(
        self,
        ids: list[uuid.UUID],
        mem_type: Literal["source", "processed"] = "processed",
    ) -> None:
        """
        Summarize the memories with the given ids into several new memory
        units. The original memories will be removed and substituted by the new
        memory units at the minimal index among the original memories.
        Args:
            ids (list[uuid.UUID]):
                the ids of the memory to summarize
        """
        mem_to_summarize = []
        if mem_type == "source":
            memories = self._chat_history
        else:
            memories = self._memory
        min_index = len(memories)
        for id in ids:
            for idx, mem in enumerate(memories):
                if mem.id == id:
                    mem_to_summarize.append(mem)
                    min_index = min(min_index, idx)
                    break
        new_mem, to_remove_uuids = await self._summarize_memory_list(
            mem_to_summarize,
            compressed_ratio=1,
            filter_func=None,
        )
        await self.direct_add_memory(
            new_mem,
            min_index,
            check_long_context=False,
        )
        for u in to_remove_uuids:
            await self.direct_delete_memory(u)

    async def summarize_single_message(
        self,
        msg_to_summarize: Msg,
    ) -> Msg:
        """
        Summarize a single message and return a new message.
        Args:
            msg_to_summarize (Msg):
                the message to summarize
        """
        path = f"msg_{uuid.uuid4().hex}.md"
        content = json.dumps(format_msgs(msg_to_summarize, with_id=False))
        self._save_to_file(path, content)
        summary = self._tool_sequential_summarize(content)
        file_list = [path]
        for c in msg_to_summarize.content:
            if c.get("type", None) == "source_file":
                file_list.extend(c.get("source_file", []))
        new_msg = Msg(
            name=msg_to_summarize.name,
            content=[
                {
                    "type": "text",
                    "text": f"{summary} More details are saved"
                    f"in these files: {file_list}.",
                },
            ],
            role=msg_to_summarize.role,
        )
        new_msg.content.append(
            {"type": "source_file", "source_file": file_list},
        )
        return new_msg

    def _summarize_fixed_size_content(
        self,
        mem_to_summarize: List[MemRecord],
    ) -> MemRecord:
        """
        Summarize the content of the memory into new memory content.
        Enable to deal with long context that exceeds the token limit
        of the chat model.
        ! make sure the token size of mem_to_summarize is less than
        MAX_CHAT_MODEL_TOKEN_SIZE
        Args:
            mem_to_summarize (List[MemRecord]):
                the memory to summarize
        """
        file_list = []
        for mem in mem_to_summarize:
            for c in mem.metadata.get("data", ""):
                if isinstance(c, dict):
                    if c.get("type", None) == "source_file":
                        file_list.extend(c.get("source_file", []))
        path = f"tracing_{uuid.uuid4().hex}.md"
        self._save_to_file(path, format_msgs(mem_to_summarize, with_id=False))
        summary = self._tool_sequential_summarize(mem_to_summarize)
        metadata = {
            "data": [
                {"type": "text", "text": summary},
                {"type": "source_file", "source_file": file_list},
            ],
            "role": "system",
            "name": "memory_manager",
        }
        return MemRecord(
            metadata=metadata,
        )

    async def _tool_sequential_summarize(
        self,
        mem_to_summarize: Union[str, List[MemRecord]],
    ) -> str:
        """
        Summarize the content of the memory list sequentially.
        Args:
            mem_to_summarize (List[MemRecord]):
                the memory list to summarize.
        Returns:
            str: the summary of the memory list.
        """
        if isinstance(mem_to_summarize, str):
            chunks = await self.text_splitter(mem_to_summarize)
        else:
            chunks = await self.text_splitter(
                format_msgs(mem_to_summarize),
            )
        previous_summary = ""
        tot_len = str(len(chunks))
        for chunk_idx, chunk in enumerate(chunks):
            summary_prompt = (
                self.summary_working_log_prompt.replace(
                    "{{chunk_idx}}",
                    str(chunk_idx + 1),
                )
                .replace("{{total_chunks}}", tot_len)
                .replace("{{log_excerpt}}", chunk)
                .replace("{{previous_summary}}", previous_summary)
            )
            response = await self.call_model(summary_prompt)
            previous_summary = response.content[0]["text"]
        return previous_summary

    async def _tool_sequential_summarize_w_query(
        self,
        mem_to_summarize: Union[str, List[MemRecord], List[Msg]],
        query: str,
    ) -> str:
        """
        Answer the question based on the content of the memory list
        sequentially.
        Args:
            mem_to_summarize (List[MemRecord]):
                the memory list to summarize.
        Returns:
            str: the summary of the memory list.
        """
        if isinstance(mem_to_summarize, str):
            chunks = await self.text_splitter(mem_to_summarize)
        else:
            chunks = await self.text_splitter(
                format_msgs(mem_to_summarize),
            )
        previous_summary = ""
        tot_len = str(len(chunks))
        for chunk_idx, chunk in enumerate(chunks):
            prompt = (
                self.summary_working_log_w_query_prompt.replace(
                    "{{chunk_idx}}",
                    str(chunk_idx + 1),
                )
                .replace("{{total_chunks}}", tot_len)
                .replace("{{chunk}}", chunk)
                .replace("{{existing_notes}}", previous_summary)
                .replace("{{question}}", query)
            )
            response = await self.call_model(prompt)
            previous_summary = response.content[0]["text"]
        return previous_summary

    def _save_to_file(
        self,
        path_name: str,
        content: Union[str, dict, list],
    ) -> None:
        if isinstance(content, str):
            with open(
                f"{self.mount_dir}/{path_name}",
                "w",
                encoding="utf-8",
            ) as f:
                f.write(content)
        elif isinstance(content, (dict, list)):
            with open(
                f"{self.mount_dir}/{path_name}",
                "w",
                encoding="utf-8",
            ) as f:
                json.dump(content, f, ensure_ascii=False, indent=4)

    async def _summarize_memory_list(
        self,
        mem_list: List[MemRecord],
        compressed_ratio: float = 0.5,
        filter_func: Optional[Callable[[MemRecord], bool]] = no_user_msg,
    ) -> tuple[List[MemRecord], List[uuid.UUID]]:
        """
        Summarize the given memory list. First, split the memory into sections,
        then summarize each section.
        **No limit on the token size of the memory list.**
        Args:
            mem_list (List[MemRecord]):
                the memory list to summarize.
            compressed_ratio (float):
                the ratio of token size of the memory list to be compressed.
            filter_func (Optional[Callable[[MemRecord], bool]]):
                the function to filter the memory. If not provided, all memory
                will be summarized.
        Returns:
            Tuple[List[MemRecord], List[uuid.UUID]]:
                the summarized memory list and the ids of the memory to be
                removed.
        """
        end_idx = len(mem_list)
        token_size_list = await asyncio.gather(
            *[
                count_words(self.token_counter, format_msgs(mem))
                for mem in mem_list
            ],
        )
        total_token_size = sum(token_size_list)
        compressed_token_size = int(total_token_size * compressed_ratio)
        current_token_size = 0
        current_start = 0
        current_end = 0
        to_summarize_mems = []
        new_summary_list = []
        to_remove_uuids = []
        while current_end < end_idx and compressed_token_size > 0:
            if filter_func is not None and not filter_func(
                mem_list[current_end],
            ):
                current_end += 1
                continue
            cur_mem_size = token_size_list[current_end]
            if current_token_size + cur_mem_size > MAX_CHAT_MODEL_TOKEN_SIZE:
                if current_end > current_start:
                    cur_summary = self._summarize_fixed_size_content(
                        to_summarize_mems,
                    )
                    new_summary_list.append(cur_summary)
                    to_remove_uuids.extend(
                        [mem.id for mem in to_summarize_mems],
                    )
                    compressed_token_size -= current_token_size
                    current_start = current_end
                    current_token_size = 0
                else:
                    # Single memory exceeds limit, need truncation handling
                    logging.warning(
                        f"Single memory exceeds "
                        f"MAX_CHAT_MODEL_TOKEN_SIZE: {cur_mem_size}, "
                        "something is wrong.",
                    )
                    summary_msg = await self._long_context_process(
                        [mem_list[current_end].to_msg()],
                    )
                    mem_list[current_end].metadata["data"] = summary_msg[
                        0
                    ].content
                    cur_mem_size = await count_words(
                        self.token_counter,
                        json.dumps(
                            format_msgs(
                                mem_list[current_end],
                                with_id=False,
                            ),
                        ),
                    )
            to_summarize_mems.append(mem_list[current_end])
            current_token_size += cur_mem_size
            current_end += 1

        if (
            current_end > current_start
            and current_token_size > 0
            and (compressed_token_size > 0)
        ):
            new_summary_list.append(
                self._summarize_fixed_size_content(
                    mem_list[current_start:current_end],
                ),
            )
            to_remove_uuids.extend(
                [mem.id for mem in mem_list[current_start:current_end]],
            )
        return new_summary_list, to_remove_uuids

    async def summarize_global(
        self,
        compressed_ratio: Optional[float] = None,
        filter_func: Optional[Callable[[MemRecord], bool]] = None,
    ) -> None:
        """
        Summarize the memory from top to bottom: first, split the memory into
          sections, then summarize each section.
        Args:
            compressed_ratio (float):
                the ratio of the memory to be compressed.
        """
        if compressed_ratio is None:
            compressed_ratio = self.compressed_ratio
        new_summary_list, to_remove_uuids = await self._summarize_memory_list(
            self._memory,
            compressed_ratio,
            filter_func,
        )
        if len(to_remove_uuids) == 0:
            await self.direct_add_memory(
                new_summary_list,
                check_long_context=False,
            )
            return
        max_index = -1
        for idx, mem in enumerate(self._memory):
            if mem.id == to_remove_uuids[-1]:
                max_index = idx + 1
                break
        if max_index != -1:
            await self.direct_add_memory(
                new_summary_list,
                max_index,
                check_long_context=False,
            )
        else:
            await self.direct_add_memory(
                new_summary_list,
                check_long_context=False,
            )
        for u in to_remove_uuids:
            await self.direct_delete_memory(u)
        logging.warning(
            f"After **summarize_global**, the memory is \n"
            f"{format_msgs(self._memory, with_id=True)}",
        )

    async def clear(self) -> None:
        """
        Clear all memory.
        """
        self._chat_history = []
        self._memory = []
        self.cur_chat_len = 0
        self.cur_memory_len = 0
        self.vector_store.reset()

    async def size(self) -> int:
        """Returns the number of memory segments in memory."""
        return len(self._chat_history)

    async def delete(self, index: Union[Iterable, int]) -> None:
        """
        This is not supported.
        """
        raise NotImplementedError(
            """
            `Delete` is not supported in ReActMemory, use
            `direct_delete_memory` or `direct_delete_chat_history` instead.
            """,
        )

    def state_dict(self) -> dict:
        """Convert the current memory into JSON data format."""
        return {
            "memory": [mem.to_dict() for mem in self._memory],
            "chat_history": [msg.to_dict() for msg in self._chat_history],
            "cur_chat_len": self.cur_chat_len,
            "cur_memory_len": self.cur_memory_len,
        }

    def load_state_dict(self, state_dict: dict) -> None:
        """Load the memory from JSON data."""
        self._memory = [
            MemRecord.from_dict(
                mem,
            )
            for mem in state_dict["memory"]
        ]
        self._chat_history = [
            Msg.from_dict(
                msg,
            )
            for msg in state_dict["chat_history"]
        ]
        self.cur_chat_len = state_dict.get("cur_chat_len", 0)
        self.cur_memory_len = state_dict.get("cur_memory_len", 0)

    async def export(
        self,
        file_path: Optional[str] = None,
        to_mem: bool = False,
        retrieve_type: Optional[Literal["source", "processed"]] = "processed",
    ) -> Optional[list]:
        """
        Export memory, depending on how the memory are stored
        Args:
            file_path (Optional[str]):
                file path to save the memory to. The messages will
                be serialized and written to the file.
            to_mem (Optional[str]):
                if True, just return the list of messages in memory
            retrieve_type (Optional[Literal["source", "processed"]]):
                the type of the memories to be retrieved. If it is "source",
                the memories will be retrieved from `self._chat_history`;
                if it is "processed", the memories will be retrieved from
                `self._memory`.
        Notice: this method prevents file_path is None when to_mem
        is False.
        """
        if retrieve_type == "processed":
            memory = self._memory
        else:
            memory = self._chat_history
        if to_mem:
            return memory

        if to_mem is False and file_path is not None:
            with open(file_path, "w", encoding="utf-8") as f:
                if retrieve_type == "processed":
                    f.write(serialize_list(memory))
                else:
                    f.write(serialize_list(memory))
        else:
            raise NotImplementedError(
                "file type only supports "
                "{json, yaml, pkl}, default "
                "is json",
            )
        return None

    async def load(
        self,
        memories: Union[str, list[Msg], Msg, list[MemRecord], MemRecord],
        load_type: Optional[Literal["source", "processed"]] = "processed",
        overwrite: bool = False,
    ) -> None:
        """
        Load memory, depending on how the memory are passed, design to load
        from both file or dict
        Args:
            memories (Union[str, list[Msg], Msg]):
                memories to be loaded.
                If it is in str type, it will be first checked if it is a
                file; otherwise it will be deserialized as messages.
                Otherwise, memories must be either in message type or list
                 of messages.
            load_type (Optional[Literal["source", "processed"]]):
                the type of the memories to be loaded. If it is "source",
                the memories will be loaded to ``self._chat_history``; if it is
                "processed", the memories will be loaded to ``self._memory``.
            overwrite (bool):
                if True, clear the current memory before loading the new ones;
                if False, memories will be appended to the old one at the end.
        """
        if isinstance(memories, str):
            if os.path.isfile(memories):
                with open(memories, "r", encoding="utf-8") as f:
                    memories = f.read()
            try:
                if load_type == "processed":
                    load_memories = deserialize_memrecord_list(memories)
                else:
                    load_memories = deserialize_msg_list(memories)
            except json.JSONDecodeError as e:
                raise json.JSONDecodeError(
                    f"Cannot load [{memories}] via " f"json.loads.",
                    e.doc,
                    e.pos,
                )
        else:
            load_memories = memories
        if isinstance(load_memories, list):
            for unit in load_memories:
                if not isinstance(unit, Msg) and not isinstance(
                    unit,
                    MemRecord,
                ):
                    raise TypeError(
                        f"Expect a list of Msg/MemRecord objects, but get "
                        f"{type(unit)} instead.",
                    )
        elif isinstance(load_memories, (Msg, MemRecord)):
            load_memories = [load_memories]
        else:
            raise TypeError(
                f"The type of memories to be loaded is not supported. "
                f"Expect str, list[Msg], or Msg, but get {type(memories)}.",
            )

        if load_type == "source":
            if overwrite:
                self._chat_history = load_memories
            else:
                self._chat_history.extend(load_memories)
        else:
            if overwrite:
                self._memory = load_memories
            else:
                self._memory.extend(load_memories)

    async def direct_get_memory(self) -> list[MemRecord]:
        """Direct get the original _memory"""
        return self._memory

    async def direct_get_chat_history(self) -> list[Msg]:
        """Direct get the original _chat_history"""
        return self._chat_history

    async def direct_delete_memory(self, index: Union[uuid.UUID, str]) -> None:
        """
        Delete memory in self._memory and self._vector_store, but not
        self._chat_history.
        """
        found = False
        index = str(index)
        for idx, mem in enumerate(self._memory):
            if mem.id == index:
                self._memory.pop(idx)
                found = True
                break
        if not found:
            logging.warning(f"Memory {index} not found to delete")
        self.vector_store.delete(vector_id=index)

    async def direct_delete_chat_history(
        self,
        index: Union[uuid.UUID, str],
    ) -> None:
        """
        Delete chat history in self._chat_history.
        """
        found = False
        index = str(index)
        for idx, msg in enumerate(self._chat_history):
            if msg.id == index:
                self._chat_history.pop(idx)
                found = True
                break
        if not found:
            logging.warning(f"Chat history {index} not found to delete")

    async def direct_update(
        self,
        index: Union[uuid.UUID, str],
        role: str = "assistant",
        new_content: Union[str, List[dict]] = None,
        embedding: Optional[list] = None,
        metadata: Optional[dict] = None,
    ) -> None:
        """
        Update the memory in `self._memory` and `self._vector_store` (but not
        `self._chat_history`).
        Args:
            index (Union[uuid.UUID, str]):
                the index of the memory to update
            role (str):
                the role of the memory
            new_content (str):
                the new content of the memory
            embedding (Optional[list]):
                the embedding of the memory. If not provided, the embedding
                will be generated from the `new_content`.
            metadata (Optional[dict]):
                the metadata of the memory. The `data` and the
                `last_modified_timestamp` will be updated, other fields will be
                  kept as is.
        """
        index = str(index)
        found = False
        if embedding is None:
            embedding_response = await self.embedding_model(
                [new_content],
                return_embedding_only=True,
            )
            embedding = embedding_response.embeddings[0]
        if metadata is None:
            metadata = {
                "data": new_content,
                "role": role,
                "last_modified_timestamp": datetime.now().isoformat(),
            }
        else:
            if "last_modified_timestamp" not in metadata:
                metadata[
                    "last_modified_timestamp"
                ] = datetime.now().isoformat()
            if "data" not in metadata:
                metadata["data"] = new_content
        for idx, mem in enumerate(self._memory):
            if mem.id == index:
                self._memory[idx].update_data(metadata, embedding)
                found = True
                break
        if not found:
            logging.warning(f"Memory {index} not found to update")
        self.vector_store.update(
            vector_id=index,
            vector=embedding,
            payload=metadata,
        )

    async def _add_one_msg_to_memory(
        self,
        content: Union[str, List[dict]],
        role: str = "assistant",
        embedding: Optional[list] = None,
        id: Optional[str] = None,
        index: Optional[int] = None,
    ) -> None:
        if embedding is None:
            embedding_response = await self.embedding_model(
                [json.dumps(content)],
                return_embedding_only=True,
            )
            embedding = embedding_response.embeddings[0]
        if id is None:
            id = uuid.uuid4().hex
        metadata = {
            "data": content,
            "role": role,
            "create_timestamp": datetime.now().isoformat(),
            "last_modified_timestamp": datetime.now().isoformat(),
            "id": id,
        }
        self.vector_store.insert(
            vectors=[embedding],
            ids=[metadata["id"]],
            payloads=[metadata],
        )
        mem_to_add = MemRecord(
            id=metadata["id"],
            metadata=metadata,
            embedding=embedding,
        )
        logging.info(f"{format_msgs(mem_to_add, with_id=False)}")
        if self.log_cnt > 5:
            logging.info(
                f"{format_msgs(self._memory, with_id=True)}",
            )
            self.log_cnt = 0
        else:
            self.log_cnt += 1
        if index is None:
            self._memory.append(mem_to_add)
        else:
            self._memory[index:index] = [mem_to_add]
        token_count = await count_words(
            self.token_counter,
            format_msgs(mem_to_add, with_id=False),
        )
        self.cur_memory_len += token_count

    async def direct_add_memory(
        self,
        msgs: Union[
            Sequence[Msg],
            Sequence[MemRecord],
            Msg,
            MemRecord,
            None,
        ],
        index: Optional[int] = None,
        check_long_context: bool = True,
    ) -> None:
        """
        Add messages to memory.
        Args:
            msgs (Union[Sequence[Msg], Sequence[MemRecord],
                  Msg, MemRecord, None]):
                the messages to add to memory
            index (Optional[int]):
                the index to add the messages to. If not provided, the messages
                will be added to the end of the memory.
            check_long_context (bool):
                if True, check if the message is long context and process it;
                if False, directly add the message to memory.
        """
        if msgs is None:
            return
        if not isinstance(msgs, Sequence):
            msg_to_record = [msgs]
        else:
            msg_to_record = msgs
        for msg in msg_to_record:
            if check_long_context:
                processed_msgs = await self._long_context_process([msg])
                processed_msg = processed_msgs[0]
            else:
                processed_msg = msg
            if isinstance(processed_msg, MemRecord):
                content = processed_msg.metadata.get("data", "")
                role = processed_msg.metadata.get("role", "assistant")
            else:
                content = processed_msg.content
                role = processed_msg.role
            await self._add_one_msg_to_memory(
                content,
                role,
                index=index,
            )

    def _load_file(self, filename: str) -> dict:
        """
        Load the file to memory.
        Args:
            filename (str):
                the filename to load
        """
        with open(filename, "r", encoding="utf-8") as f:
            content = json.load(f)
        return content

    async def retrieve_from_cached_memory(
        self,
        query: str,
        filename: str,
    ) -> str:
        """
        The function is used to search for information in memory based on a
        specified query. The memory storage contains all chat history, such as
        user request, the LLM assistant's response, reasoning process and the
        results of all tool calls.
        You can also specify the filename which to search.

        Args:
            query (str):
                The search query to retrieve information from memory.
            filename (Optional[str], default=None):
                Specific file to search within. If None, searches across all
                memory contents.
        """
        related_memories = self._load_file(filename)
        if not isinstance(related_memories, list):
            related_memories = [related_memories]
        file_list = []
        content_to_analze = []
        for mem in related_memories:
            if isinstance(mem.payload.get("data", None), str):
                content_to_analze.append(mem.payload.get("data", ""))
            else:
                for c in mem.payload.get("data", []):
                    if c.get("type", None) == "source_file":
                        file_list.extend(c.get("source_file", []))
                content_to_analze.append(
                    json.dumps(
                        mem.payload.get("data", []),
                    ),
                )
        for f in file_list:
            content_to_analze.append(self._load_file(f))
        tmp_file = f"tmp_detail_{uuid.uuid4().hex}.md"
        self._save_to_file(
            tmp_file,
            json.dumps(content_to_analze),
        )
        try:
            summary = await self._tool_sequential_summarize_w_query(
                content_to_analze,
                query,
            )
        except Exception as e:
            logging.warning(
                "Error in summarize_with_sequential_notes: %s",
                str(e),
            )
            return ToolResponse(
                content=[
                    TextBlock(
                        type="text",
                        text=f"Error retrieving memory: {str(e)}",
                    ),
                ],
            )
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=summary,
                ),
            ],
        )
