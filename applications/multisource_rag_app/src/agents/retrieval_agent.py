# -*- coding: utf-8 -*-
# pylint: disable=E0611,C0200,R0912,R0915,C0209
"""
This example shows how to build an agent with RAG
with LlamaIndex.

Notice, this is a Beta version of RAG agent.
"""
import asyncio
import os
import re
import statistics
import time
from typing import Optional, Any, Union, Dict, List, Sequence, Callable, Tuple

import numpy as np
from ..utils.constant import INPUT_MAX_TOKEN
from ..utils.logging import logger
from ..app_knowledge.search_knowledge import BingKnowledge

from agentscope.agent import LlamaIndexAgent
from agentscope.manager import ModelManager
from agentscope.message import Msg
from agentscope.rag import Knowledge, RetrievedChunk
from agentscope.utils.token_utils import count_openai_token

from .agent_util.query_rewrite import (
    PromptRewriter,
    KeywordRewriter,
    check_language_type,
)
from .agent_util.reference import analysis_reference_v3, raw_return
from .agent_util.rerank import ds_rerank


class RetrievalAgent(LlamaIndexAgent):
    """
    A LlamaIndex agent build on LlamaIndex.
    """

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model_config_name: str,
        knowledge_list: List[Knowledge] = None,
        knowledge_id_list: List[str] = None,
        similarity_top_k: int = None,
        log_retrieval: bool = True,
        recent_n_mem_for_retrieve: int = 2,
        speak_result: bool = False,
        secondary_model_config_name: str = None,
        return_raw: bool = True,
        **kwargs: Any,
    ) -> None:
        """
        Initialize the RAG LlamaIndexAgent
        Args:
            name (str):
                The name for the agent
            sys_prompt (str):
                System prompt for the RAG agent
            model_config_name (str):
                Language model for the agent
            memory_config (dict):
                Memory configuration
            knowledge_id_list (str):
                Identifier of the knowledge in KnowledgeBank,
            similarity_top_k (int):
                How many chunks/documents to retrieved,
            log_retrieval (bool):
                Whether log the retrieved content,
            recent_n_mem_for_retrieve (int):
                How many memory used to query (default is 1,
                using only the current input to reply)
        """
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model_config_name=model_config_name,
        )
        self.known_agent_list = None
        self.knowledge_list = knowledge_list or []
        self.knowledge_id_list = knowledge_id_list or []
        self.similarity_top_k = similarity_top_k
        self.log_retrieval = log_retrieval
        self.recent_n_mem_for_retrieve = recent_n_mem_for_retrieve
        if self.recent_n_mem_for_retrieve > 2:
            logger.info(
                "recent_n_mem_for_retrieve is greater than 2, "
                "maybe unnecessary",
            )
        self.description = kwargs.get("description", "")
        if secondary_model_config_name:
            model_manager = ModelManager.get_instance()
            self.secondary_model = model_manager.get_model_by_config_name(
                secondary_model_config_name,
            )
        else:
            self.secondary_model = self.model

        # counts for user asking vigorous questions
        self.respond_counter = 0
        # the configs for the link retrival
        self.source_max = 2
        # load prompts for the agent
        self.speak_result = speak_result
        self.check_language_count = 0
        self._set_local_path_to_url(kwargs)

        # for prompt rewrite
        self.rewrite_prompt = kwargs.get("rewrite_prompt", "")
        self.amplified_importance = kwargs.get("amplified_importance", 1.0)
        logger.info(f"{self.name} importance: {self.amplified_importance}")
        # when using search engine, we may want to disable rerank
        self.return_all = kwargs.get("return_all", False)

        # return raw retrieved content?
        self.return_raw = return_raw

    @staticmethod
    def rrf_fuser(
        ranked_lists: List[List[RetrievedChunk]],
        c: int = 60,
    ) -> List[RetrievedChunk]:
        """
        Merge nodes from different retrieve function using rrf fusion
        """
        hash2score = {}
        for ranked_list in ranked_lists:
            for rank, item in enumerate(ranked_list):
                rrf_score = 1 / (rank + 1 + c)
                hash_value = item.hash
                if hash_value in hash2score:
                    hash2score[hash_value]["rrf"] += rrf_score
                else:
                    hash2score[hash_value] = {}
                    hash2score[hash_value]["rrf"] = rrf_score
                    hash2score[hash_value]["node"] = item

        sorted_nodes = sorted(
            hash2score.items(),
            key=lambda x: x[1]["rrf"],
            reverse=True,
        )
        sorted_nodes = [item["node"] for _, item in sorted_nodes]

        return sorted_nodes

    @staticmethod
    def avg_score_fuser(
        chunks: List[RetrievedChunk],
    ) -> List[RetrievedChunk]:
        """
        Merge nodes from same retrieve function (dense or sparse)
        Sort by average score from retrieval
        """
        ranked_nodes = []
        hash2score = {}
        for chunk in chunks:
            hash_value = chunk.hash
            if hash_value not in hash2score:
                hash2score[hash_value] = []
                ranked_nodes.append(chunk)
            hash2score[hash_value].append(chunk.score)

        ranked_nodes = sorted(
            ranked_nodes,
            key=lambda x: statistics.mean(hash2score[x.hash]),
            reverse=True,
        )
        return ranked_nodes

    def merge_retrieved_res(
        self,
        retrieved_res: List[Dict],
        similarity_top_k: int,
    ) -> list:
        """
        Merge nodes from different queries. Two merging cases are considered:
            1. merge nodes retrieved by same retriever
            2. merge nodes from different retriever
        """
        nodes_wrt_retriever = {}
        for nodes_dict in retrieved_res:
            for retriever_name, nodes in nodes_dict.items():
                if retriever_name not in nodes_wrt_retriever:
                    nodes_wrt_retriever[retriever_name] = []
                nodes_wrt_retriever[retriever_name].extend(nodes)

        # merge nodes from same retriever
        rank_nodes_list = []
        for _, nodes in nodes_wrt_retriever.items():
            ranked_nodes = self.avg_score_fuser(nodes)
            rank_nodes_list.append(ranked_nodes)

        # merge nodes from different retriever
        if len(rank_nodes_list) > 1:
            fused_nodes = self.rrf_fuser(rank_nodes_list)
        else:
            fused_nodes = rank_nodes_list[0]

        return fused_nodes[:similarity_top_k]

    def _retrieve(
        self,
        knowledge: Knowledge,
        query: str,
        additional_sparse_retrieve: bool = False,
    ) -> Dict[str, list]:
        """
        Internal retrieval function from a single knowledge
        """
        res = knowledge.retrieve(
            query,
            self.similarity_top_k,
            additional_sparse_retrieve=additional_sparse_retrieve,
        )
        if not isinstance(res, dict):
            res_dict = {}
            res_dict["dense"] = res
            return res_dict
        else:
            return res

    def vdb_retrieve(
        self,
        query_msgs: Union[Msg, List[Msg]],
        additional_sparse_retrieve: bool = False,
    ) -> List[List[RetrievedChunk]]:
        """
        Retrieval from multi-source knowledge and merge with rrf
        """
        if isinstance(query_msgs, Msg):
            query_msgs = [query_msgs]
        if query_msgs is None or len(query_msgs) == 0:
            raise ValueError("Query messages cannot be empty")

        all_queries_msgs = list(query_msgs)

        retrieved_chunks = []
        for knowledge in self.knowledge_list:
            if isinstance(knowledge, BingKnowledge):
                continue
            retrieved_chunks_for_single_knowledge = []
            for query_msg in all_queries_msgs:
                retrieved_chunks_for_single_knowledge.append(
                    self._retrieve(
                        knowledge,
                        query_msg.content,
                        additional_sparse_retrieve,
                    ),
                )
            retrieved_chunks_for_single_knowledge = self.merge_retrieved_res(
                retrieved_chunks_for_single_knowledge,
                self.similarity_top_k,
            )
            retrieved_chunks.append(retrieved_chunks_for_single_knowledge)
        return retrieved_chunks

    def online_retrieve(
        self,
        knowledge: Knowledge,
        keyword_msgs: List[Msg],
        request_id: str,
    ) -> List[RetrievedChunk]:
        """retrieve with online search knowledge"""
        search_nodes = []
        for keyword_msg in keyword_msgs:
            return_nodes = knowledge.retrieve(
                keyword_msg.content,
                online_search_only=True,
            )
            if isinstance(return_nodes, list):
                search_nodes += return_nodes
            elif isinstance(return_nodes, dict):
                search_nodes += return_nodes["search"]
            else:
                raise ValueError
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:online_search",
            context={
                "knowledge_id": knowledge.knowledge_id,
                "keyword_msgs": keyword_msgs,
                "return_nodes_len": len(search_nodes),
            },
        )
        return search_nodes

    def reply(self, x: Msg = None) -> Msg:
        return asyncio.get_event_loop().run_until_complete(
            self.async_reply(x),
        )

    async def async_reply(self, x: Msg = None) -> Msg:
        """
        Reply function of the RAG agent.
        1) rewrite the input query;
        2) retrieve with the rewritten query;
        3) (optional) invokes the language model to produce a response. The
        response is then formatted and added to the dialogue memory.

        Args:
            x (`Msg`, defaults to `None`):
                A `Msg` representing the input to the agent. This
                input is added to the memory if provided. Defaults to
                None.
        Returns:
            A `Msg` representing the message generated by the agent in
            response to the user's input.
        """

        metadata = x.metadata if x.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            f"{self.name}.async_reply.default_request_id",
        )
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:input",
            context={"x": x},
        )

        if len(x.content or "") == 0:
            return Msg(
                name="assistant",
                content="Oops, is the query empty?",
                role="assistant",
            )

        if self.memory:
            # if we do have memory, retrieve the n most recent records
            history = self.memory.get_memory(
                recent_n=self.recent_n_mem_for_retrieve,
            )
            query_history = (
                "\n".join(
                    [(msg.role + ": " + msg.content) for msg in history],
                )
                if isinstance(history, list)
                else str(history)
            )
            # then add the latest query msg to the memory
            self.memory.add(x)
        else:
            # otherwise, the history is empty
            query_history = ""

        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:memory",
            context={"query_history": query_history},
        )

        query_msg = x

        # the list of sources regarding the retrieved doc nodes
        info_dict, scores, unique_nodes = [], [], []
        # translate the chinese to english for matching
        # remove @ mention and agent name
        query_msg.content = self._remove_all_mentions(query_msg.content)

        original_query_language, query_msg = self.check_language_type(
            query_msg,
        )
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:check_language_type",
            context={"original_query_language": original_query_language},
        )

        knowledge_languages = set()
        for knowledge in self.knowledge_list:
            if hasattr(knowledge, "language"):
                knowledge_languages = knowledge_languages.union(
                    knowledge.language,
                )
        knowledge_languages = list(knowledge_languages)
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:check_language_type",
            context={"knowledge_languages": knowledge_languages},
        )

        # === Search section starts ===
        time_start_search = time.perf_counter()
        search_nodes = []
        keyword_msgs = []
        retrieval_msgs = []
        retrieved_chunks = []
        retrieved_node_lists = []
        for knowledge in self.knowledge_list:
            if isinstance(knowledge, BingKnowledge):
                # if the knowledge is using online search
                # prepare key words
                if keyword_msgs is None:
                    keyword_msgs = await KeywordRewriter.async_rewrite(
                        query_msg,
                        self.model,
                        merge_keywords=True,
                        additional_prompt=self.rewrite_prompt,
                    )
                search_nodes += self.online_retrieve(
                    knowledge,
                    keyword_msgs,
                    request_id,
                )
            else:
                # if other knowledge sources, use other rewrite
                # =======  VDB retrieval rewrite =============
                time_start_query_rewrite = time.perf_counter()
                new_query_msgs = (
                    await PromptRewriter.async_rewrite(
                        query_msg,
                        self.model,
                        additional_prompt=self.rewrite_prompt,
                    )
                    # One can uncomment the following to use other rewrites
                    # HyDERewriter.async_rewrite(
                    #     query_msg,
                    #     self.secondary_model,
                    #     additional_languages=knowledge_languages
                    # ) +
                    # await RetrievalRewriter.async_rewrite(
                    #     query_msg,
                    #     self.secondary_model,
                    #     knowledge_list=self.knowledge_list,
                    #     additional_languages=knowledge_languages,
                    # ) +
                    # await PromptContextRewriter.async_rewrite(
                    #     query_msg,
                    #     self.model,
                    #     additional_prompt=self.rewrite_prompt,
                    #     local_short_history=query_history,
                    # )
                )
                logger.query_info(
                    request_id=request_id,
                    location=self.name + ".async_reply:query_rewrite",
                    context={
                        "query_rewrite_time_cost": time.perf_counter()
                        - time_start_query_rewrite,
                        "query_msg": query_msg,
                        "additional_prompt": self.rewrite_prompt,
                        "new_query_msgs": new_query_msgs,
                    },
                )
                retrieval_msgs = [query_msg] + (new_query_msgs or [])

            time_start_retrieve = time.perf_counter()
            retrieved_node_lists = self.vdb_retrieve(
                retrieval_msgs,
                additional_sparse_retrieve=True,
            )
            logger.query_info(
                request_id=request_id,
                location=self.name + ".async_reply:retrieve",
                context={
                    "retrieval_time_cost": time.perf_counter()
                    - time_start_retrieve,
                    "retrieved_node_lists_len": len(retrieved_node_lists),
                },
            )

        # === Reranking section starts ===
        time_start_rerank = time.perf_counter()

        for node_list in retrieved_node_lists:
            retrieved_chunks += node_list
        # add search nodes if any
        retrieved_chunks = search_nodes + retrieved_chunks
        logger.info(
            f"Retrieved nodes:{len(retrieved_chunks)} {len(search_nodes)}",
        )
        if self.return_all:
            pass
        else:
            retrieved_chunks = ds_rerank(
                query_msg.content,
                retrieved_chunks,
                self.similarity_top_k,
            )
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:rerank",
            context={
                "ds_rerank_time_cost": time.perf_counter() - time_start_rerank,
                "query_msgs": retrieval_msgs,
                "retrieved_chunks_num": len(retrieved_chunks),
                "similarity_top_k": self.similarity_top_k,
            },
        )
        # === Reranking section ends ===

        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:keywords_results",
            context={
                "time_cost": time.perf_counter() - time_start_search,
                "online_search_nodes_len": len(search_nodes),
            },
        )

        # ====== Deduplicate & ensure legal context length ======
        max_score, token_count = 0, 0
        exist_nodes = set()
        for i, chunk in enumerate(retrieved_chunks):
            if chunk.hash in exist_nodes:
                continue
            if chunk.score > max_score:
                max_score = chunk.score
            try:
                tokens = count_openai_token(
                    chunk.content,
                    "gpt-4-turbo",
                )
            except:  # noqa: E722 # pylint: disable=W0702
                continue
            if token_count + tokens < INPUT_MAX_TOKEN and not self.return_all:
                token_count += tokens
                exist_nodes.add(chunk.hash)
                info_dict.append(
                    {
                        "Index": i,
                        "Content": chunk.content,
                    },
                )
                scores.append(chunk.score)
                unique_nodes.append(chunk)
            logger.query_info(
                request_id=request_id,
                location=self.name + ".async_reply:check_step",
                context={
                    "max_score": max_score,
                    "INPUT_MAX_TOKEN": INPUT_MAX_TOKEN,
                    "token_count": token_count,
                    "exist_nodes_len": len(exist_nodes),
                },
            )

            if self.log_retrieval:
                log_info = ""
                for info, score in zip(info_dict, scores):
                    log_info += f"-- score:【{score}】\n {info}\n"
                log_msg = Msg(name="system", content=log_info, role="system")
                await self.print(log_msg)

        # ======  If retrieval agent just need to return chunks =====
        if self.return_raw:
            raw_pieces = raw_return(unique_nodes)
            for i in range(len(raw_pieces)):
                raw_pieces[i]["Reference"] = self._map_path_to_url(
                    raw_pieces[i]["Reference"],
                )
            msg = Msg(
                name=self.name,
                content=raw_pieces,
                role="assistant",
                metadata={"language": original_query_language},
            )
            return msg

        # ====== If need to generate LLM-based answers ===
        # check the retrieved result with query
        time_start_check_result = time.perf_counter()
        sources, _, answer = await analysis_reference_v3(
            query_msg,
            self.sys_prompt,
            unique_nodes,
            self.model,
            context_history=query_history or "",
            language=original_query_language,
        )
        msg = Msg(
            name=self.name,
            content=answer,
            role="assistant",
            metadata={"language": original_query_language},
        )
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:analysis",
            context={
                "analysis_time_cost": time.perf_counter()
                - time_start_check_result,
                "query_msg": query_msg,
                "sys_prompt": self.sys_prompt,
                "unique_nodes": unique_nodes,
                "context_history": query_history or "",
                "language": original_query_language,
                "answer": answer,
            },
        )

        # ==== processing sources =====
        time_start_processing_sources = time.perf_counter()
        # deduplicate sources
        sources_set = set(sources)
        sources = []
        for s in sources_set:
            if "标准答案" not in s:
                sources.append(s)
        if len(sources) > self.source_max:
            sources = sources[: self.source_max]
        # attach links to the response if needed
        if len(sources) > 0:
            self._add_source(msg, sources=sources)

        # Print/speak the message in this agent's voice
        if self.speak_result:
            await self.print(msg)

        if self.memory:
            # Record the message in memory
            self.memory.add(msg)
        time_end = time.perf_counter()
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:processing_sources",
            context={
                "source_processing_time_cost": time_end
                - time_start_processing_sources,
                "sources_set": msg.metadata.get("sources", []),
                "respond_count": self.respond_counter,
            },
        )
        return msg

    def set_known_agents(self, agents: List[LlamaIndexAgent]) -> None:
        """
        Let this agent knows all buddies.
        The names of the buddies are used to clean the query.
        """
        self.known_agent_list = agents

    def _remove_all_mentions(self, query: str) -> str:
        pattern = (
            r"@("
            + "|".join(
                re.escape(agent.name) for agent in self.known_agent_list
            )
            + r")\b"
        )
        new_query = re.sub(pattern, "", query)
        return new_query

    def _set_local_path_to_url(self, kwargs: dict) -> None:
        """
        This function preprocessing the local_path to url search pattern.
        For example, if there is a mapping in kwargs and
        mapping["local_pattern"] = "examples/",
        it will be changed into "examples/(.*)" to match the
        subsequence after that.
        """
        kwargs["repo_path"] = os.environ.get("REPO_PATH", "")
        kwargs["text_dir"] = os.environ.get("TEXT_DIR", "")
        if "web_path_mapping" not in kwargs:
            self.web_path_mapping = []
            return
        else:
            self.web_path_mapping = kwargs["web_path_mapping"]

        for mapping in self.web_path_mapping:
            mapping["local_pattern"] = r"{}(.*)".format(
                re.escape(mapping["local_pattern"]),
            )
            if mapping["name"] in kwargs:
                path = kwargs[mapping["name"]].replace("//", "/")
                mapping["local_pattern"] = r"{}(.*)".format(re.escape(path))
        logger.info(f"web mapping: {self.web_path_mapping}")

    def _add_source(self, msg: Msg, sources: List[str]) -> Msg:
        """
        Transfer the LlamaIndex meta data strings to urls
        """
        sources_set = set()
        if len(sources) < 1:
            return msg
        for source in sources:
            if source.startswith("http"):
                sources_set.add(source)

            url = self._map_path_to_url(source)
            if url:
                sources_set.add(url)
        if msg.metadata is None:
            msg.metadata = {}
        if len(sources_set) > 0:
            msg.metadata["sources"] = list(sources_set)
        else:
            msg.metadata["sources"] = []
        return msg

    def _map_path_to_url(self, source: str) -> Any:
        url = None
        if source.startswith("http"):
            return source
        for mapping in self.web_path_mapping:
            # reformat the local path to url when first met
            pattern = mapping["local_pattern"]
            match = re.search(pattern, source)
            if match:
                logger.info(f"matches: {pattern}, {source}, {match.group()}")
                rel_path = match.group(1)
                if "replace_suffix" in mapping:
                    logger.info(f">>>> {rel_path} {match}")
                    rel_path = re.sub(
                        r"{}".format(mapping["replace_suffix"][0]),
                        r"{}".format(mapping["replace_suffix"][1]),
                        rel_path,
                    )
                url = mapping["url_pattern"].format(rel_path)
                # logger.info(f"mapped url: {url}")
                break
        return url

    def check_language_type(self, query_msg: Msg) -> Tuple[str, Msg]:
        """
        Check query language
        """
        if query_msg.metadata and "language" in query_msg.metadata:
            return query_msg.metadata.get("language", " "), query_msg

        self.check_language_count += 1
        query_language, query_msg = check_language_type(
            query_msg,
            self.secondary_model,
        )
        return query_language, query_msg

    def get_cluster_similarites(
        self,
        query_or_embedding: Union[str, Sequence[float]],
        emb_model: Optional[Callable] = None,
        description_weight: float = 0.0,
    ) -> Sequence:
        """
        Get the similarities between query embedding and cluster centroids
        """
        if isinstance(query_or_embedding, str):
            assert emb_model is not None
            query_embedding = emb_model(query_or_embedding).embedding[0]
        else:
            query_embedding = query_or_embedding

        similarities = []
        for knowledge in self.knowledge_list:
            if hasattr(knowledge, "cluster_similarities"):
                similarities += list(
                    knowledge.cluster_similarities(query_embedding),
                )

        if description_weight > 0:
            assert emb_model is not None
            description_embedding = emb_model(self.description).embedding[0]
            query_normalized = query_embedding / np.linalg.norm(
                query_embedding,
            )
            description_normalized = description_embedding / np.linalg.norm(
                description_embedding,
            )
            description_similarity = np.dot(
                description_normalized,
                query_normalized,
            )
            logger.info(
                f"{self.name} query-description similarity: "
                f"{description_similarity}",
            )
            for i in range(len(similarities)):
                similarities[i] = (
                    description_weight * description_similarity
                    + (1 - description_weight) * similarities[i]
                )

            if len(similarities) == 0:
                similarities = [description_similarity]

        for i in range(len(similarities)):
            similarities[i] *= self.amplified_importance

        return similarities
