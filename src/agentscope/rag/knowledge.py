# -*- coding: utf-8 -*-
"""
Base class module for retrieval augmented generation (RAG).
To accommodate the RAG process of different packages,
we abstract the RAG process into four stages:
- data loading: loading data into memory for following processing;
- data indexing and storage: document chunking, embedding generation,
and off-load the data into VDB;
- data retrieval: taking a query and return a batch of documents or
document chunks;
- post-processing of the retrieved data: use the retrieved data to
generate an answer.
"""

import importlib
from abc import ABC, abstractmethod
from typing import Any, Optional, Union
from dataclasses import dataclass, asdict
from loguru import logger
from agentscope.model import ModelWrapperBase


@dataclass
class RetrievedChunk:
    """
    Retrieved content with score and meta information

    Attributes:
        score (`float`):
            Similarity score of this retrieved chunk
        content (`Any`):
            The retrieved content
        metadata (`Optional[dict]`):
            The meta data of this retrieved chunk, such as file path
        embedding (`Optional[Any]`)`:
            The embedding of the chunk
        hash (`Optional[str]`):
            The hash of the retrieved content
    """

    score: float = 0.0
    content: Any = None
    metadata: Optional[dict] = None
    embedding: Optional[Any] = None
    hash: Optional[str] = None

    def to_dict(self) -> dict:
        """convert object to dict"""
        return asdict(self)


class Knowledge(ABC):
    """
    Base class for RAG, CANNOT be instantiated directly
    """

    knowledge_type: str = "base_knowledge"
    """
    A string to identify a knowledge base class
    """

    def __init__(
        self,
        knowledge_id: str,
        emb_model: Any = None,
        knowledge_config: Optional[dict] = None,
        model: Optional[ModelWrapperBase] = None,
        **kwargs: Any,
    ) -> None:
        # pylint: disable=unused-argument
        """
        Initialize the knowledge component

        Args:
        knowledge_id (`str`):
            The id of the knowledge unit.
        emb_model (`ModelWrapperBase`):
            The embedding model used for generate embeddings
        knowledge_config (`dict`):
            The configuration to generate or load the index.
        """
        self.knowledge_id = knowledge_id
        self.emb_model = emb_model
        self.knowledge_config = knowledge_config or {}
        self.postprocessing_model = model

    @abstractmethod
    def _init_rag(
        self,
        **kwargs: Any,
    ) -> Any:
        """
        Initiate the RAG module.
        """

    @abstractmethod
    def retrieve(
        self,
        query: Any,
        similarity_top_k: int = None,
        to_list_strs: bool = False,
        **kwargs: Any,
    ) -> list[Union[RetrievedChunk, str]]:
        """
        Retrieve list of content from database (vector stored index) to memory

        Args:
            query (`Any`):
                Query for retrieval
            similarity_top_k (`int`):
                The number of most similar data returned by the
                retriever.
            to_list_strs (`bool`):
                Whether return a list of str

        Returns:
            Return a list with retrieved documents (in strings)
        """

    @classmethod
    def default_config(cls, **kwargs: Any) -> dict:
        """
        Return a default config for a knowledge class.

        Args:
            kwargs (`Any`):
                Parameters for config

        Returns:
            dict: a default config of the knowledge class
        """
        raise NotImplementedError(
            f"{cls.__name__} does not have default_config to be defined.",
        )

    @classmethod
    def build_knowledge_instance(
        cls,
        knowledge_id: str,
        knowledge_config: Optional[dict] = None,
        **kwargs: Any,
    ) -> "Knowledge":
        """
        A constructor to build a knowledge base instance.

        Args:
            knowledge_id (`str`):
                The id of the knowledge instance.
            knowledge_config (`dict`):
                The configuration to the knowledge instance.

        Returns:
            Knowledge: a Knowledge instance
        """
        raise NotImplementedError(
            f"{knowledge_id} of {cls.__name__} does not support "
            "auto build knowledge base instance",
        )

    def post_processing(
        self,
        retrieved_docs: list[str],
        prompt: str,
        **kwargs: Any,
    ) -> Any:
        """
        A default solution for post-processing function, generates answer
        based on the retrieved documents.

        Args:
            retrieved_docs (`list[str]`):
                List of retrieved documents
            prompt (`str`):
                Prompt for LLM generating answer with the retrieved documents

        Returns:
            Any: A synthesized answer from LLM with retrieved documents

        Example:
            self.postprocessing_model(prompt.format(retrieved_docs))
        """
        assert self.postprocessing_model
        prompt = prompt.format("\n".join(retrieved_docs))
        return self.postprocessing_model(prompt, **kwargs).text

    def _prepare_args_from_config(self, config: dict) -> Any:
        """
        Helper function to build objects in RAG classes.

        Args:
            config (`dict`):
                A dictionary containing configurations
        Returns:
            Any: An object that is parsed/built to be an element
            of input to the function of RAG module.
        """
        if not isinstance(config, dict):
            return config

        if "create_object" in config:
            # if a term in args is an object,
            # recursively create object with args from config
            module_name = config.get("module", "")
            class_name = config.get("class", "")
            init_args = config.get("init_args", {})
            try:
                cur_module = importlib.import_module(module_name)
                cur_class = getattr(cur_module, class_name)
                init_args = self._prepare_args_from_config(init_args)
                logger.info(
                    f"load and build object: {class_name}",
                )
                return cur_class(**init_args)
            except ImportError as exc_inner:
                logger.error(
                    f"Fail to load class {class_name} "
                    f"from module {module_name}",
                )
                raise ImportError(
                    f"Fail to load class {class_name} "
                    f"from module {module_name}",
                ) from exc_inner
        else:
            prepared_args = {}
            for key, value in config.items():
                if isinstance(value, list):
                    prepared_args[key] = []
                    for c in value:
                        prepared_args[key].append(
                            self._prepare_args_from_config(c),
                        )
                elif isinstance(value, dict):
                    prepared_args[key] = self._prepare_args_from_config(value)
                else:
                    prepared_args[key] = value
            return prepared_args
