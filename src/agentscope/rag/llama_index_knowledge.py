# -*- coding: utf-8 -*-
"""
This module is an integration of the Llama index RAG
into AgentScope package
"""

import copy
import os.path
from typing import Any, Optional, List, Union
from loguru import logger

try:
    import llama_index
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.base.embeddings.base import (
        BaseEmbedding,
        Embedding,
    )
    from llama_index.core.ingestion import IngestionPipeline
    from llama_index.core.storage.docstore import SimpleDocumentStore
    from llama_index.retrievers.bm25 import BM25Retriever

    from llama_index.core.vector_stores.types import VectorStore
    from llama_index.core.bridge.pydantic import PrivateAttr
    from llama_index.core.node_parser import SentenceSplitter
    from llama_index.core import (
        VectorStoreIndex,
        StorageContext,
        load_index_from_storage,
    )
    from llama_index.core.schema import (
        Document,
        TransformComponent,
        BaseNode,
    )
except ImportError:
    llama_index = None
    BaseRetriever = None
    BaseEmbedding = None
    Embedding = None
    IngestionPipeline = None
    SimpleDocumentStore = None
    BM25Retriever = None
    VectorStore = None
    SentenceSplitter = None
    VectorStoreIndex = None
    StorageContext = None
    load_index_from_storage = None
    PrivateAttr = None
    Document = None
    TransformComponent = None
    BaseNode = None

from agentscope.manager import FileManager, ModelManager
from agentscope.model import ModelWrapperBase
from agentscope.constants import (
    DEFAULT_TOP_K,
    DEFAULT_CHUNK_SIZE,
    DEFAULT_CHUNK_OVERLAP,
)
from agentscope.rag import Knowledge, RetrievedChunk


try:

    class _EmbeddingModel(BaseEmbedding):
        """
        Wrapper for ModelWrapperBase to an embedding model can be used
        in Llama Index pipeline.
        """

        _emb_model_wrapper: ModelWrapperBase = PrivateAttr()

        def __init__(
            self,
            emb_model: ModelWrapperBase,
            embed_batch_size: int = 1,
        ) -> None:
            """
            Dummy wrapper to convert a ModelWrapperBase to llama Index
            embedding model

            Args:
                emb_model (`ModelWrapperBase`):
                    Embedding model in ModelWrapperBase
                embed_batch_size (`int`):
                    Batch size, defaults to 1
            """
            super().__init__(
                model_name="Temporary_embedding_wrapper",
                embed_batch_size=embed_batch_size,
            )
            self._emb_model_wrapper = emb_model

        def _get_query_embedding(self, query: str) -> List[float]:
            """
            Get embedding for query

            Args:
                query (`str`): Query to be embedded

            Returns:
                `List[float]`: Embedding
            """
            # Note: AgentScope embedding model wrapper returns list
            # of embedding
            return list(self._emb_model_wrapper(query).embedding[0])

        def _get_text_embeddings(self, texts: List[str]) -> List[Embedding]:
            """
            Get embedding for list of strings

            Args:
                 texts (` List[str]`): Texts to be embedded

            Returns:
                `List[float]`: List of embeddings
            """
            results = [
                list(self._emb_model_wrapper(t).embedding[0]) for t in texts
            ]
            return results

        def _get_text_embedding(self, text: str) -> Embedding:
            """
            Get embedding for a single string
            Args:
                 text (`str`): Texts to be embedded

            Returns:
                `List[float]`: Embedding
            """
            return list(self._emb_model_wrapper(text).embedding[0])

        # TODO: use proper async methods, but depends on model wrapper
        async def _aget_query_embedding(self, query: str) -> List[float]:
            """The asynchronous version of _get_query_embedding."""
            return self._get_query_embedding(query)

        async def _aget_text_embedding(self, text: str) -> List[float]:
            """Asynchronously get text embedding."""
            return self._get_text_embedding(text)

        async def _aget_text_embeddings(
            self,
            texts: List[str],
        ) -> List[List[float]]:
            """Asynchronously get text embeddings."""
            return self._get_text_embeddings(texts)

except Exception:

    class _EmbeddingModel:  # type: ignore[no-redef]
        """
        A dummy embedding model for passing tests when
        llama-index is not install
        """

        def __init__(self, emb_model: ModelWrapperBase):
            self._emb_model_wrapper = emb_model


class LlamaIndexKnowledge(Knowledge):
    """
    This class is a wrapper with the llama index RAG.
    """

    knowledge_type: str = "llamaindex_knowledge"

    def __init__(
        self,
        knowledge_id: str,
        emb_model: Union[ModelWrapperBase, BaseEmbedding, None] = None,
        knowledge_config: Optional[dict] = None,
        model: Optional[ModelWrapperBase] = None,
        persist_root: Optional[str] = None,
        additional_sparse_retrieval: Optional[bool] = False,
        overwrite_index: Optional[bool] = False,
        showprogress: Optional[bool] = True,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the knowledge component based on the
        llama-index framework: https://github.com/run-llama/llama_index

        Notes:
            In LlamaIndex, one of the most important concepts is index,
            which is a data structure composed of Document objects, designed to
            enable querying by an LLM. The core workflow of initializing RAG is
            to convert data to index, and retrieve information from index.
            For example:
            1) preprocessing documents with data loaders
            2) generate embedding by configuring pipeline with embedding model
            3) store the embedding-content to vector database
                the default dir is " ~/.cache/agentscope/{knowledge_id}"

        Args:
            knowledge_id (`str`):
                The id of the RAG knowledge unit.
            emb_model (`ModelWrapperBase`):
                The embedding model used for generate embeddings
            knowledge_config (`dict`):
                The configuration for llama-index to
                generate or load the index.
            model (`ModelWrapperBase`):
                The language model used for final synthesis
            persist_root (`str`):
                The root directory for index persisting
            overwrite_index (`Optional[bool]`):
                Whether to overwrite the index while refreshing
            showprogress (`Optional[bool]`):
                Whether to show the indexing progress
        """
        super().__init__(
            knowledge_id=knowledge_id,
            emb_model=emb_model,
            knowledge_config=knowledge_config,
            model=model,
            **kwargs,
        )
        if llama_index is None:
            raise ImportError(
                "LlamaIndexKnowledge require llama-index installed. "
                "Try a stable llama-index version, such as "
                "`pip install llama-index==0.10.30`",
            )

        if persist_root is None:
            persist_root = FileManager.get_instance().cache_dir or "./"
        self.persist_dir = os.path.join(persist_root, knowledge_id)
        logger.info(f"** persist_dir: {self.persist_dir}")
        self.emb_model = emb_model
        self.overwrite_index = overwrite_index
        self.showprogress = showprogress
        self.index = None

        # if use mix retrieval with bm25 in addition to dense retrieval
        self.additional_sparse_retrieval = additional_sparse_retrieval
        self.bm25_retriever = None

        # ensure the emb_model is compatible with LlamaIndex
        if isinstance(emb_model, ModelWrapperBase):
            self.emb_model = _EmbeddingModel(emb_model)
        elif isinstance(self.emb_model, BaseEmbedding):
            pass
        else:
            raise TypeError(
                f"Embedding model does not support {type(self.emb_model)}.",
            )
        # then we can initialize the RAG
        self._init_rag()

    def _init_rag(self, **kwargs: Any) -> None:
        """
        Initialize the RAG. This includes:
            * if the persist_dir exists, load the persisted index
            * if not, convert the data to index
            * if needed, update the index
            * set the retriever to retrieve information from index

        Notes:
            * the index is persisted in the self.persist_dir
            * the refresh_index method is placed here for testing, it can be
                called externally. For example, updated the index periodically
                by calling rag.refresh_index() during the execution of the
                agent.
        """
        if not os.path.exists(self.persist_dir):
            os.makedirs(self.persist_dir, exist_ok=True)
        try:
            self._load_index()
        except Exception as e:
            logger.warning(
                f"index loading error: {str(e)}, recomputing index...",
            )
            self._data_to_index()
        self._get_retriever()
        logger.info(
            f"RAG with knowledge ids: {self.knowledge_id} "
            f"initialization completed!\n",
        )

    def _load_index(self) -> None:
        """
        Load the persisted index from persist_dir.
        """
        # load the storage_context
        storage_context = StorageContext.from_defaults(
            persist_dir=self.persist_dir,
        )
        # construct index from
        self.index = load_index_from_storage(
            storage_context=storage_context,
            embed_model=self.emb_model,
        )
        logger.info(f"index loaded from {self.persist_dir}")

    def _data_to_index(
        self,
        vector_store: Optional[VectorStore] = None,
    ) -> List[BaseNode]:
        """
        Convert the data to index by configs. This includes:
            * load the d_data_to_ata to documents by using information
              from configs
            * set the transformations associated with documents
            * convert the documents to nodes
            * convert the nodes to index

        Notes:
            As each selected file type may need to use a different loader
            and transformations, knowledge_config is a list of configs.

        Args:
            vector_store (`Optional[VectorStore]`):
                Vector store in LlamaIndex

        Returns:
           ` List[BaseNode]`: list of processed nodes
        """
        nodes = []
        # load data to documents and set transformations
        # using information in knowledge_config
        for config in self.knowledge_config.get("data_processing"):
            documents = self._data_to_docs(config=config)
            transformations = self._set_transformations(config=config).get(
                "transformations",
            )
            nodes_docs = self._docs_to_nodes(
                documents=documents,
                transformations=transformations,
            )
            nodes = nodes + nodes_docs
        # convert nodes to index
        if vector_store is None:
            self.index = VectorStoreIndex(
                nodes=nodes,
                embed_model=self.emb_model,
            )
            logger.info("index calculation completed.")
            # persist the calculated index
            self.index.storage_context.persist(persist_dir=self.persist_dir)
            logger.info("index persisted.")
        else:
            docstore = SimpleDocumentStore()
            docstore.add_documents(nodes)
            storage_context = StorageContext.from_defaults(
                docstore=docstore,
                vector_store=vector_store,
            )
            self.index = VectorStoreIndex(
                nodes,
                storage_context=storage_context,
                embed_model=self.emb_model,
            )
            logger.info("[Update Mode] Added documents to VDB")
            storage_context.docstore.persist(
                os.path.join(self.persist_dir, "docstore.json"),
            )

        return nodes

    def _data_to_docs(
        self,
        query: Optional[str] = None,
        config: dict = None,
    ) -> Any:
        """
        This method set the loader as needed, or just use the
        default setting. Then use the loader to load data from
        dir to documents.

        Notes:
            We can use simple directory loader (SimpleDirectoryReader)
            to load general documents, including Markdown, PDFs,
            Word documents, PowerPoint decks, images, audio and video.
            Or use SQL loader (DatabaseReader) to load database.

        Args:
            query (`Optional[str]`):
                Optional, used when the data is in a database.
            config (`dict`):
                Optional, used when the loader config is in a config file.
        Returns:
            `Any`: loaded documents
        """
        loader = self._set_loader(config=config).get("loader")
        # let the doc_id be the filename for each document
        loader.filename_as_id = True
        if query is None:
            documents = loader.load_data()
        else:
            # this is for querying a database,
            # does not work for loading a document directory
            documents = loader.load_data(query)
        logger.info(f"loaded {len(documents)} documents")
        return documents

    def _docs_to_nodes(
        self,
        documents: List[Document],
        transformations: Optional[list[Optional[TransformComponent]]] = None,
    ) -> Any:
        """
        Convert the loaded documents to nodes using transformations.

        Args:
            documents (`List[Document]`):
                Documents to be processed, usually expected to be in
                 llama index Documents.
            transformations (`Optional[list[TransformComponent]]`):
                Optional, specifies the transformations (operators) to
                process documents (e.g., split the documents into smaller
                chunks)
        Return:
            `Any`: Return the index of the processed document
        """
        # nodes, or called chunks, is a presentation of the documents
        # we build nodes by using the IngestionPipeline
        # for each document with corresponding transformations
        pipeline = IngestionPipeline(
            transformations=transformations,
        )
        # stack up the nodes from the pipeline
        nodes = pipeline.run(
            documents=documents,
            show_progress=self.showprogress,
        )
        logger.info("nodes generated.")
        return nodes

    def _set_loader(self, config: dict) -> Any:
        """
        Set the loader as needed, or just use the default setting.

        Args:
            config (`dict`): A dictionary containing configurations
        """
        if "load_data" in config:
            # we prepare the loader from the configs
            loader = self._prepare_args_from_config(
                config=config.get("load_data", {}),
            )
        else:
            # we prepare the loader by default
            try:
                from llama_index.core import SimpleDirectoryReader
            except ImportError as exc_inner:
                raise ImportError(
                    " LlamaIndexAgent requires llama-index to be install."
                    "Please run `pip install llama-index`",
                ) from exc_inner
            loader = {
                "loader": SimpleDirectoryReader(
                    input_dir="set_default_data_path",
                ),
            }
        logger.info("loaders are ready.")
        return loader

    def _set_transformations(self, config: dict) -> Any:
        """
        Set the transformations as needed, or just use the default setting.

        Args:
            config (`dict`): A dictionary containing configurations.
        """
        if "store_and_index" in config:
            temp = self._prepare_args_from_config(
                config=config.get("store_and_index", {}),
            )
            transformations = temp.get("transformations")
        else:
            transformations = [
                SentenceSplitter(
                    chunk_size=self.knowledge_config.get(
                        "chunk_size",
                        DEFAULT_CHUNK_SIZE,
                    ),
                    chunk_overlap=self.knowledge_config.get(
                        "chunk_overlap",
                        DEFAULT_CHUNK_OVERLAP,
                    ),
                ),
            ]
        # adding embedding model as the last step of transformation
        # https://docs.llamaindex.ai/en/stable/module_guides/loading/ingestion_pipeline/root.html
        transformations.append(self.emb_model)
        logger.info("transformations are ready.")
        # as the last step, we need to repackage the transformations in dict
        transformations = {"transformations": transformations}
        return transformations

    def _get_retriever(
        self,
        similarity_top_k: int = None,
        **kwargs: Any,
    ) -> BaseRetriever:
        """
        Set the retriever as needed, or just use the default setting.

        Args:
            retriever (`Optional[BaseRetriever]`):
                Passing a retriever in LlamaIndexKnowledge
            rag_config (`dict`):
                RAG configuration, including similarity top k index.
        """
        # set the retriever
        logger.info(
            f"similarity_top_k" f"={similarity_top_k or DEFAULT_TOP_K}",
        )
        retriever = self.index.as_retriever(
            embed_model=self.emb_model,
            similarity_top_k=similarity_top_k or DEFAULT_TOP_K,
            **kwargs,
        )

        if not self.bm25_retriever:
            self.bm25_retriever = BM25Retriever.from_defaults(
                nodes=self.index.docstore.docs.values(),
                similarity_top_k=similarity_top_k,
            )
        else:
            self.bm25_retriever.similarity_top_k = similarity_top_k

        logger.info("retriever is ready.")

        return retriever

    def retrieve(
        self,
        query: str,
        similarity_top_k: int = None,
        to_list_strs: bool = False,
        retriever: Optional[BaseRetriever] = None,
        **kwargs: Any,
    ) -> list[Union[RetrievedChunk, str]]:
        """
        This is a basic retrieve function for knowledge.
        It will build a retriever on the fly and return the
        result of the query.
        Args:
            query (`str`):
                Query is expected to be a question in string
            similarity_top_k (`int`):
                The number of most similar data returned by the
                retriever.
            to_list_strs (`bool`):
                Whether returns the list of strings;
                if False, return list of RetrievedChunk
            retriever (`BaseRetriever`):
                For advanced usage, user can pass their own retriever.
        Return:
            `list[Union[RetrievedChunk, str]]`: List of retrieved content

        More advanced query processing can refer to
        https://docs.llamaindex.ai/en/stable/examples/query_transformations/query_transform_cookbook.html
        """
        if retriever is None:
            retriever = self._get_retriever(similarity_top_k)
        dense_retrieved = retriever.retrieve(str(query))
        retrieved_res = []

        for node in dense_retrieved:
            retrieved_res.append(
                RetrievedChunk(
                    score=node.score,
                    content=node.get_content(),
                    metadata=node.metadata,
                    embedding=node.embedding,
                    hash=node.node.hash,
                ),
            )

        if self.additional_sparse_retrieval and self.bm25_retriever:
            bm25_retrieved = self.bm25_retriever.retrieve(str(query))
            sparse_retrieved = [x for x in bm25_retrieved if x.score > 0]
            bm25_scores = [x.score for x in bm25_retrieved]
            logger.info(f"bm25 scores {bm25_scores}")
            for node in sparse_retrieved:
                retrieved_res.append(
                    RetrievedChunk(
                        score=node.score,
                        content=node.get_content(),
                        metadata=node.metadata,
                        embedding=node.embedding,
                        hash=node.node.hash,
                    ),
                )

        if to_list_strs:
            results = []
            for chunk in retrieved_res:
                results.append(str(chunk.content))
            return results

        return retrieved_res

    def refresh_index(self) -> None:
        """
        Refresh the index when needed.
        """
        for config in self.knowledge_config.get("data_processing"):
            documents = self._data_to_docs(config=config)
            # store and indexing for each file type
            transformations = self._set_transformations(config=config).get(
                "transformations",
            )
            self._insert_docs_to_index(
                documents=documents,
                transformations=transformations,
            )

    def _insert_docs_to_index(
        self,
        documents: List[Document],
        transformations: TransformComponent,
    ) -> None:
        """
        Add documents to the index. Given a list of documents, we first test if
        the doc_id is already in the index. If not, we add the doc to the
        list. If yes, and the over-write flag is enabled,
        we delete the old doc and add the new doc to the list.
        Lastly, we generate nodes for all documents on the list, and insert
        the nodes to the index.

        Args:
            documents (`List[Document]`):
                List of documents to be added.
            transformations (`TransformComponent`):
                Transformations that onvert the documents into nodes.
        """
        # this is the pipeline that generate the nodes
        pipeline = IngestionPipeline(
            transformations=transformations,
        )
        # we need to generate nodes from this list of documents
        insert_docs_list = []
        for doc in documents:
            if doc.doc_id not in self.index.ref_doc_info.keys():
                # if the doc_id is not in the index, we add it to the list
                insert_docs_list.append(doc)
                logger.info(
                    f"add new documents to index, " f"doc_id={doc.doc_id}",
                )
            else:
                if self.overwrite_index:
                    # if we enable overwrite index, we delete the old doc
                    self.index.delete_ref_doc(
                        ref_doc_id=doc.doc_id,
                        delete_from_docstore=True,
                    )
                    # then add the same doc to the list
                    insert_docs_list.append(doc)
                    logger.info(
                        f"replace document in index, " f"doc_id={doc.doc_id}",
                    )
        logger.info("documents scan completed.")
        # we generate nodes for documents on the list
        nodes = pipeline.run(
            documents=insert_docs_list,
            show_progress=True,
        )
        logger.info("nodes generated.")
        # insert the new nodes to index
        self.index.insert_nodes(nodes=nodes)
        logger.info("nodes inserted to index.")
        # persist the updated index
        self.index.storage_context.persist(persist_dir=self.persist_dir)

    def _delete_docs_from_index(
        self,
        documents: List[Document],
    ) -> None:
        """
        Delete the nodes that are associated with a list of documents.

        Args:
            documents (`List[Document]`): List of documents to be deleted.
        """
        doc_id_list = [doc.doc_id for doc in documents]
        for key in self.index.ref_doc_info.keys():
            if key in doc_id_list:
                self.index.delete_ref_doc(
                    ref_doc_id=key,
                    delete_from_docstore=True,
                )
                logger.info(f"docs deleted from index, doc_id={key}")
        # persist the updated index
        self.index.storage_context.persist(persist_dir=self.persist_dir)
        logger.info("nodes delete completed.")

    @classmethod
    def default_config(
        cls,
        knowledge_id: str,
        data_dirs_and_types: dict[str, list[str]] = None,
        knowledge_config: Optional[dict] = None,
    ) -> dict:
        """
        Generate default config for loading data from directories and using the
        default operations to preprocess the data for RAG usage.
        Args:
            knowledge_id (`str`):
                User-defined unique id for the knowledge
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
                Examples can refer to../examples/conversation_with_RAG_agents/

        Returns:
            `dict`: A default config of LlamaIndexKnowledge
        """
        data_dirs_and_types = (
            data_dirs_and_types if data_dirs_and_types else {}
        )

        default_knowledge_config = {
            "knowledge_id": "",
            "data_processing": [],
        }
        default_loader_config = {
            "load_data": {
                "loader": {
                    "create_object": True,
                    "module": "llama_index.core",
                    "class": "SimpleDirectoryReader",
                    "init_args": {},
                },
            },
        }
        default_init_config = {
            "input_dir": "",
            "recursive": True,
            "required_exts": [],
        }
        # generate default knowledge config
        default_knowledge_config["knowledge_id"] = knowledge_id
        for data_dir, types in data_dirs_and_types.items():
            loader_config = copy.deepcopy(default_loader_config)
            loader_init = copy.deepcopy(default_init_config)
            loader_init["input_dir"] = data_dir
            loader_init["required_exts"] = types
            loader_config["load_data"]["loader"]["init_args"] = loader_init
            default_knowledge_config["data_processing"].append(loader_config)

        if knowledge_config is None:
            return default_knowledge_config
        else:
            default_knowledge_config.update(knowledge_config)
            return default_knowledge_config

    @classmethod
    def build_knowledge_instance(
        cls,
        knowledge_id: str,
        knowledge_config: Optional[dict] = None,
        data_dirs_and_types: dict[str, list[str]] = None,
        emb_model_config_name: Optional[str] = None,
        model_config_name: Optional[str] = None,
        **kwargs: Any,
    ) -> Knowledge:
        """
        Building an instance of the LlamaIndex knowledge

        Args:
            knowledge_id (`str`):
                User-defined unique id for the knowledge
            knowledge_config (`optional[dict]`):
                Complete indexing configuration, used for more advanced
                applications. Users can customize
                - loader,
                - transformations,
                - ...
                Examples can refer to../examples/conversation_with_RAG_agents/
            data_dirs_and_types (`dict[str, list[str]]`):
                Dictionary of data paths (keys) to the data types
                (file extensions) for knowledge
                (e.g., [".md", ".py", ".html"])
            emb_model_config_name (`Optional[str]`):
                Name of the embedding model.
                This should be specified here or in the knowledge_config dict.
                If specified both here and in the knowledge_config,
                the input parameter takes a higher priority than the
                one knowledge_config.
            model_config_name (`Optional[str]`):
                Name of the language model.
                Optional, can be None and not specified in knowledge_config.
                If specified both here and in the knowledge_config,
                the input parameter takes a higher priority than the
                one knowledge_config.

        Returns:
            `Knowledge`: A knowledge instance

            A simple example of importing data to Knowledge object:

            .. code-block:: python

                knowledge_bank.add_data_as_knowledge(
                    knowledge_id="agentscope_tutorial_rag",
                    emb_model_config_name="qwen_emb_config",
                    data_dirs_and_types={
                        "../../docs/sphinx_doc/en/source/tutorial": [".md"],
                    },
                    persist_dir="./rag_storage/tutorial_assist",
                )

        """
        model_manager = ModelManager.get_instance()
        if emb_model_config_name is None and (
            knowledge_config is None
            or "emb_model_config_name" not in knowledge_config
        ):
            raise ValueError(
                "Must specify embedding model by providing value to"
                "'emb_model_config_name' key in  in knowledge config"
                "of LlamaIndexKnowledge. For example"
                """
                {
                    "knowledge_id": "xxx_rag",
                    "knowledge_type": "llamaindex_knowledge",
                    "emb_model_config_name": "qwen_emb_config",
                    ....
                }
                """,
            )
        if emb_model_config_name is None:
            emb_model_config_name = knowledge_config.get(
                "emb_model_config_name",
            )
        # model_name is optional
        if knowledge_config is not None and model_config_name is None:
            model_config_name = knowledge_config.get("model_config_name")
        knowledge_config = cls.default_config(
            knowledge_id=knowledge_id,
            data_dirs_and_types=data_dirs_and_types,
            knowledge_config=knowledge_config,
        )
        return cls(
            knowledge_id=knowledge_id,
            emb_model=model_manager.get_model_by_config_name(
                emb_model_config_name,
            ),
            knowledge_config=knowledge_config,
            model=model_manager.get_model_by_config_name(model_config_name)
            if model_config_name
            else None,
            **kwargs,
        )
