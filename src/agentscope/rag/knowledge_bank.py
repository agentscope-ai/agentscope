# -*- coding: utf-8 -*-
"""
Knowledge bank for making Knowledge objects easier to use
"""
import copy
import json
from typing import Optional, Union, Type, Any
from loguru import logger
from agentscope.agents import AgentBase
from .knowledge import Knowledge

DEFAULT_INDEX_CONFIG = {
    "knowledge_id": "",
    "data_processing": [],
}
DEFAULT_LOADER_CONFIG = {
    "load_data": {
        "loader": {
            "create_object": True,
            "module": "llama_index.core",
            "class": "SimpleDirectoryReader",
            "init_args": {},
        },
    },
}
DEFAULT_INIT_CONFIG = {
    "input_dir": "",
    "recursive": True,
    "required_exts": [],
}


class KnowledgeBank:
    """
    KnowledgeBank enables
    1) provide an easy and fast way to initialize the Knowledge object;
    2) make Knowledge object reusable and sharable for multiple agents.
    """

    def __init__(
        self,
        configs: Union[dict, str, list, None] = None,
        new_knowledge_types: Union[type[Knowledge], list, None] = None,
    ) -> None:
        """
        Initialize the knowledge bank
        Args:
            configs (`Union[dict, str, list, None]`):
                Configurations of knowledge instances so that they can
                be initialized with knowledge bank in a batch,
                can be a file path (str), a single config (dict)
                or a batch of configs (list). If there is any instance of new
                knowledge classes, need to pass those class in
                `new_knowledge_types`.
            new_knowledge_types (`Union[type[Knowledge], list, None]`):
                User-defined new knowledge classes that are
                not in the AgentScope repo.
        """
        if configs is None:
            knowledge_configs = []
        elif isinstance(configs, str):
            logger.info(f"Loading configs from {configs}")
            with open(configs, "r", encoding="utf-8") as fp:
                knowledge_configs = json.loads(fp.read())
            if isinstance(knowledge_configs, dict):
                knowledge_configs = [knowledge_configs]
        elif isinstance(configs, dict):
            knowledge_configs = [configs]
        else:
            knowledge_configs = configs

        self.stored_knowledge: dict[str, Knowledge] = {}
        self.known_knowledge_types: dict[str, type[Knowledge]] = {}

        from .llama_index_knowledge import LlamaIndexKnowledge

        self.register_knowledge_type(LlamaIndexKnowledge)

        if new_knowledge_types is not None:
            if not isinstance(new_knowledge_types, list):
                new_knowledge_types = [new_knowledge_types]
            for new_knowledge_type in new_knowledge_types:
                self.register_knowledge_type(new_knowledge_type)

        self._init_knowledge(knowledge_configs)

    def _init_knowledge(self, knowledge_configs: list) -> None:
        """
        Initialize the knowledge instance with configs

        Args:
            knowledge_configs (`list`):
                Configurations of knowledge instances
        """
        for config in knowledge_configs:
            self.add_data_as_knowledge(
                knowledge_id=config["knowledge_id"],
                knowledge_type=config.get(
                    "knowledge_type",
                    "llamaindex_knowledge",
                ),
                knowledge_config=config,
            )
        logger.info("knowledge bank initialization completed.\n ")

    def register_knowledge_type(
        self,
        knowledge_base_class: Type[Knowledge],
        exist_ok: bool = True,
    ) -> None:
        """
        Add a new knowledge base class to the knowledge bank

        Args:
            knowledge_base_class (`Type[Knowledge]`):
                The knowledge class to be registered, which must inherit
                from `Knowledge`.
            exist_ok (`bool`):
                Whether to overwrite the existing knowledge base class
                with the same name.
        """
        if not issubclass(knowledge_base_class, Knowledge):
            raise TypeError(
                "The new knowledge base class should inherit from "
                f"Knowledge, but got {knowledge_base_class}.",
            )

        if not hasattr(knowledge_base_class, "knowledge_type"):
            raise ValueError(
                f"The knowledge base class `{knowledge_base_class}` should "
                f"have a `knowledge_type` attribute.",
            )

        knowledge_type = knowledge_base_class.knowledge_type
        if knowledge_type in self.known_knowledge_types:
            if exist_ok:
                logger.warning(
                    f'Knowledge class"{knowledge_type}" '
                    "already exists, overwrite it.",
                )
                self.known_knowledge_types[
                    knowledge_type
                ] = knowledge_base_class
            else:
                raise ValueError(
                    f'Knowledge class "{knowledge_type}" already exists, '
                    "please set `exist_ok=True` to overwrite it.",
                )
        else:
            self.known_knowledge_types[knowledge_type] = knowledge_base_class

    def add_data_as_knowledge(
        self,
        knowledge_id: str,
        knowledge_type: str,
        knowledge_config: Optional[dict] = None,
        **kwargs: Any,
    ) -> None:
        """
        Transform data in a directory to be ready to work with RAG.

        Args:
            knowledge_id (`str`):
                User-defined unique id for the knowledge
            knowledge_type (`str`):
                Type of the knowledge to register, e.g., "llamaindex_knowledge"
            knowledge_config (`dict`):
                For LlamaIndexKnowledge (knowledge_type="llamaindex_knowledge")
                the following are required:
                emb_model_config_name (`str`):
                    Name of the embedding model config
                model_config_name (`Optional[str]`):
                    Name of the LLM config for potential post-processing or
                    query rewrite
                data_dirs_and_types (`dict[str, list[str]]`):
                    Dictionary of data paths (keys) to the data types
                    (file extensions) for knowledgebase
                    (e.g., [".md", ".py", ".html"])
                knowledge_config (`optional[dict]`):
                    Complete indexing configuration, used for more advanced
                    applications. Users can customize
                    - loader,
                    - transformations,
                    - ...
                    Examples can refer to
                    ../examples/conversation_with_RAG_agents/
            kwargs (`Any`):
                Additional keyword arguments to initialize knowledge.
        """
        if knowledge_id in self.stored_knowledge:
            raise ValueError(f"knowledge_id {knowledge_id} already exists.")

        self.stored_knowledge[knowledge_id] = self.known_knowledge_types[
            knowledge_type
        ].build_knowledge_instance(
            knowledge_id=knowledge_id,
            knowledge_config=knowledge_config,
            **kwargs,
        )
        logger.info(f"data loaded for knowledge_id = {knowledge_id}.")

    def get_knowledge(
        self,
        knowledge_id: str,
        duplicate: bool = False,
    ) -> Knowledge:
        """
        Get a Knowledge object from the knowledge bank.

        Args:
            knowledge_id (`str`):
                Unique id for the Knowledge object
            duplicate (`bool`):
                Whether return a copy of the Knowledge object.
        Returns:
            Knowledge:
                The Knowledge object defined with Llama-index
        """
        if knowledge_id not in self.stored_knowledge:
            raise ValueError(
                f"{knowledge_id} does not exist in the knowledge bank.",
            )
        knowledge = self.stored_knowledge[knowledge_id]
        if duplicate:
            knowledge = copy.deepcopy(knowledge)
        logger.info(f"knowledge bank loaded: {knowledge_id}.")
        return knowledge

    def equip(
        self,
        agent: AgentBase,
        knowledge_id_list: list[str] = None,
        duplicate: bool = False,
    ) -> None:
        """
        Equip the agent with the knowledge by knowledge ids.

        Args:
            agent (`AgentBase`):
                The agent to be equipped with knowledge
            knowledge_id_list (`list[str]`):
                The list of knowledge ids to be equipped with the agent
            duplicate (`bool`):
                Whether to deepcopy the knowledge object
        TODO: to accommodate with distributed setting
        """
        logger.info(f"Equipping {agent.id} knowledge {knowledge_id_list}")
        knowledge_id_list = knowledge_id_list or []

        if not hasattr(agent, "knowledge_list"):
            agent.knowledge_list = []
        for kid in knowledge_id_list:
            knowledge = self.get_knowledge(
                knowledge_id=kid,
                duplicate=duplicate,
            )
            agent.knowledge_list.append(knowledge)
