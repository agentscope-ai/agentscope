# -*- coding: utf-8 -*-
"""
Memory module for conversation
"""

import json
import os
from typing import Iterable, Sequence
from typing import Optional
from typing import Union
from typing import Callable

from loguru import logger

from ._memory_base import MemoryBase
from ..manager import ModelManager
from ..serialize import serialize, deserialize
from ..service.retrieval.retrieval_from_list import retrieve_from_list
from ..service.retrieval.similarity import Embedding
from ..message import Msg
from ..rpc import AsyncResult


class TemporaryMemory(MemoryBase):
    """
    In-memory memory module, not writing to hard disk
    """

    def __init__(
        self,
        embedding_model: Union[str, Callable] = None,
    ) -> None:
        """
        Temporary memory module for conversation.

        Args:
            embedding_model (Union[str, Callable])
                if the temporary memory needs to be embedded,
                then either pass the name of embedding model or
                the embedding model itself.
        """
        super().__init__()

        self._content = []

        # prepare embedding model if needed
        if isinstance(embedding_model, str):
            model_manager = ModelManager.get_instance()
            self.embedding_model = model_manager.get_model_by_config_name(
                embedding_model,
            )
        else:
            self.embedding_model = embedding_model

    def add(
        self,
        memories: Union[Sequence[Msg], Msg, None],
        embed: bool = False,
    ) -> None:
        """
        Adding new memory fragment, depending on how the memory are stored
        Args:
            memories (`Union[Sequence[Msg], Msg, None]`):
                Memories to be added.
            embed (`bool`):
                Whether to generate embedding for the new added memories
        """
        if memories is None:
            return

        if not isinstance(memories, Sequence):
            record_memories = [memories]
        else:
            record_memories = memories

        # FIXME: a single message may be inserted multiple times
        # Assert the message types
        memories_idx = set(_.id for _ in self._content if hasattr(_, "id"))
        for memory_unit in record_memories:
            # in case this is a PlaceholderMessage, try to update
            # the values first
            if isinstance(memory_unit, AsyncResult):
                memory_unit = memory_unit.result()

            if not isinstance(memory_unit, Msg):
                raise ValueError(
                    f"Cannot add {type(memory_unit)} to memory, "
                    f"must be a Msg object.",
                )

            # Add to memory if it's new
            if memory_unit.id not in memories_idx:
                if embed:
                    if self.embedding_model:
                        # TODO: embed only content or its string representation
                        memory_unit.embedding = self.embedding_model(
                            [memory_unit],
                            return_embedding_only=True,
                        )
                    else:
                        raise RuntimeError("Embedding model is not provided.")
                self._content.append(memory_unit)

    def delete(self, index: Union[Iterable, int]) -> None:
        """
        Delete memory fragment, depending on how the memory are stored
        and matched
        Args:
            index (Union[Iterable, int]):
                indices of the memory fragments to delete
        """
        if self.size() == 0:
            logger.warning(
                "The memory is empty, and the delete operation is "
                "skipping.",
            )
            return

        if isinstance(index, int):
            index = [index]

        if isinstance(index, list):
            index = set(index)

            invalid_index = [_ for _ in index if _ >= self.size() or _ < 0]
            if len(invalid_index) > 0:
                logger.warning(
                    f"Skip delete operation for the invalid "
                    f"index {invalid_index}",
                )

            self._content = [
                _ for i, _ in enumerate(self._content) if i not in index
            ]
        else:
            raise NotImplementedError(
                "index type only supports {None, int, list}",
            )

    def export(
        self,
        file_path: Optional[str] = None,
        to_mem: bool = False,
    ) -> Optional[list]:
        """
        Export memory, depending on how the memory are stored
        Args:
            file_path (Optional[str]):
                file path to save the memory to. The messages will
                be serialized and written to the file.
            to_mem (Optional[str]):
                if True, just return the list of messages in memory
        Notice: this method prevents file_path is None when to_mem
        is False.
        """
        if to_mem:
            return self._content

        if to_mem is False and file_path is not None:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(serialize(self._content))
        else:
            raise NotImplementedError(
                "file type only supports "
                "{json, yaml, pkl}, default is json",
            )
        return None

    def load(
        self,
        memories: Union[str, list[Msg], Msg],
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
            overwrite (bool):
                if True, clear the current memory before loading the new ones;
                if False, memories will be appended to the old one at the end.
        """
        if isinstance(memories, str):
            if os.path.isfile(memories):
                with open(memories, "r", encoding="utf-8") as f:
                    load_memories = deserialize(f.read())
            else:
                try:
                    load_memories = deserialize(memories)
                    if not isinstance(load_memories, dict) and not isinstance(
                        load_memories,
                        list,
                    ):
                        logger.warning(
                            "The memory loaded by json.loads is "
                            "neither a dict nor a list, which may "
                            "cause unpredictable errors.",
                        )
                except json.JSONDecodeError as e:
                    raise json.JSONDecodeError(
                        f"Cannot load [{memories}] via " f"json.loads.",
                        e.doc,
                        e.pos,
                    )
        elif isinstance(memories, list):
            for unit in memories:
                if not isinstance(unit, Msg):
                    raise TypeError(
                        f"Expect a list of Msg objects, but get {type(unit)} "
                        f"instead.",
                    )
            load_memories = memories
        elif isinstance(memories, Msg):
            load_memories = [memories]
        else:
            raise TypeError(
                f"The type of memories to be loaded is not supported. "
                f"Expect str, list[Msg], or Msg, but get {type(memories)}.",
            )

        # overwrite the original memories after loading the new ones
        if overwrite:
            self.clear()

        self.add(load_memories)

    def clear(self) -> None:
        """Clean memory, depending on how the memory are stored"""
        self._content = []

    def size(self) -> int:
        """Returns the number of memory segments in memory."""
        return len(self._content)

    def retrieve_by_embedding(
        self,
        query: Union[str, Embedding],
        metric: Callable[[Embedding, Embedding], float],
        top_k: int = 1,
        preserve_order: bool = True,
        embedding_model: Callable[[Union[str, dict]], Embedding] = None,
    ) -> list[dict]:
        """Retrieve memory by their embeddings.

        Args:
            query (`Union[str, Embedding]`):
                Query string or embedding.
            metric (`Callable[[Embedding, Embedding], float]`):
                A metric to compute the relevance between embeddings of query
                and memory. In default, higher relevance means better match,
                and you can set `reverse` to `True` to reverse the order.
            top_k (`int`, defaults to `1`):
                The number of memory units to retrieve.
            preserve_order (`bool`, defaults to `True`):
                Whether to preserve the original order of the retrieved memory
                units.
            embedding_model (`Callable[[Union[str, dict]], Embedding]`, \
                defaults to `None`):
                A callable object to embed the memory unit. If not provided, it
                will use the default embedding model.

        Returns:
            `list[dict]`: a list of retrieved memory units in
            specific order.
        """

        retrieved_items = retrieve_from_list(
            query,
            self.get_embeddings(embedding_model or self.embedding_model),
            metric,
            top_k,
            self.embedding_model,
            preserve_order,
        ).content

        # obtain the corresponding memory item
        response = []
        for score, index, _ in retrieved_items:
            response.append(
                {
                    "score": score,
                    "index": index,
                    "memory": self._content[index],
                },
            )

        return response

    def get_embeddings(
        self,
        embedding_model: Callable[[Union[str, dict]], Embedding] = None,
    ) -> list:
        """Get embeddings of all memory units. If `embedding_model` is
        provided, the memory units that doesn't have `embedding` attribute
        will be embedded. Otherwise, its embedding will be `None`.

        Args:
            embedding_model
                (`Callable[[Union[str, dict]], Embedding]`, defaults to
                `None`):
                Embedding model or embedding vector.

        Returns:
            `list[Union[Embedding, None]]`: List of embeddings or None.
        """
        embeddings = []
        for memory_unit in self._content:
            if memory_unit.embedding is None and embedding_model is not None:
                # embedding
                # TODO: embed only content or its string representation
                memory_unit.embedding = embedding_model(memory_unit)
            embeddings.append(memory_unit.embedding)
        return embeddings

    def get_memory(
        self,
        recent_n: Optional[int] = None,
        filter_func: Optional[Callable[[int, dict], bool]] = None,
    ) -> list:
        """Retrieve memory.

        Args:
            recent_n (`Optional[int]`, default `None`):
                The last number of memories to return.
            filter_func
                (`Callable[[int, dict], bool]`, default to `None`):
                The function to filter memories, which take the index and
                memory unit as input, and return a boolean value.
        """
        # extract the recent `recent_n` entries in memories
        if recent_n is None:
            memories = self._content
        else:
            if recent_n > self.size():
                logger.warning(
                    "The retrieved number of memories {} is "
                    "greater than the total number of memories {"
                    "}",
                    recent_n,
                    self.size(),
                )
            memories = self._content[-recent_n:]

        # filter the memories
        if filter_func is not None:
            memories = [_ for i, _ in enumerate(memories) if filter_func(i, _)]

        return memories
