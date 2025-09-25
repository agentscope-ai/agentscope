# -*- coding: utf-8 -*-
# pylint: disable=E0611
"""This module provides a set of query rewrite functions."""
import time
from abc import ABC, abstractmethod
from typing import Optional, Any, List, Tuple

from llama_index.core.schema import MetadataMode
from utils.logging import logger

from agentscope.message import Msg
from agentscope.model import (
    ModelWrapperBase,
    DashScopeChatWrapper,
    ModelResponse,
)
from agentscope.parsers import MarkdownJsonDictParser
from agentscope.rag.llama_index_knowledge import LlamaIndexKnowledge


LANGUAGE_CHECKING_PARSER = MarkdownJsonDictParser(
    content_hint={"language": "the main language of the query"},
)


def check_language_type(
    query_msg: Msg,
    model: ModelWrapperBase,
) -> Tuple[str, Msg]:
    """
    A function use LLM to check the language in the query.
    Args:
        query_msg (Msg): the query message.
        model (ModelWrapperBase): the model to check language type.
    """
    # check query language
    if query_msg.metadata is None:
        query_msg.metadata = {}
    request_id = query_msg.metadata.get(
        "request_id",
        "query_rewrite.check_language_type.default_request_id",
    )
    time_start = time.perf_counter()
    logger.query_info(
        request_id=request_id,
        location="query_rewrite.check_language_type",
        context={"query_msg": query_msg},
    )

    if "language" in query_msg.metadata:
        return query_msg.metadata.get("language", ""), query_msg

    prompt = (
        "What is the main language of the following query? \n "
        f"Query: {query_msg.content or ' '}\n "
        f"Instruction: {LANGUAGE_CHECKING_PARSER.format_instruction}"
    )

    if isinstance(model, DashScopeChatWrapper):
        # for the performance of QWEN model, we manually compose the query here
        msgs = [
            {
                "role": "system",
                "content": "You are a helpful assistant for detecting "
                "languages.",
            },
            {"role": "user", "content": prompt},
        ]
    else:
        msg = Msg(
            name=query_msg.name,
            role=query_msg.role,
            content=prompt,
        )
        msgs = model.format(msg)
    try:
        response = model(msgs, parse_func=LANGUAGE_CHECKING_PARSER.parse)
        query_language = response.parsed["language"]
    except Exception as e:
        logger.query_error(
            request_id=request_id,
            location=f"fail to check language type: {str(e)}",
        )
        query_language = "Chinese"
        response = ModelResponse(text="fail to check language type.")
    query_msg.metadata["language"] = query_language

    logger.query_info(
        request_id=request_id,
        location="query_rewrite.check_language_type",
        context={
            "time_cost": time.perf_counter() - time_start,
            "msgs": msgs,
            "response": response.text,
        },
    )
    return query_language, query_msg


class QueryRewriter(ABC):
    """Abstract class for query rewrite to unify APIs"""

    @classmethod
    def rewrite(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        **kwargs: Any,
    ) -> Optional[List[Msg]]:
        """general rewrite operation"""
        time_start = time.perf_counter()
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "QueryRewriter.rewrite.default_request_id",
        )
        # prepare
        msgs = cls.prepare_model_call(query_msg, model, **kwargs)

        # call model
        if msgs is not None and len(msgs) > 0:
            response = model(msgs)
        else:
            response = None

        new_query_msgs = cls.postprocess_model_call(
            query_msg,
            response,
            **kwargs,
        )
        new_queries = [q.content for q in new_query_msgs]
        logger.query_info(
            request_id=request_id,
            location="QueryRewriter.rewrite",
            context={
                "time_cost": time.perf_counter() - time_start,
                "msgs": msgs,
                "new_query": new_queries,
            },
        )
        return new_query_msgs

    @classmethod
    async def async_rewrite(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        **kwargs: Any,
    ) -> Optional[List[Msg]]:
        """general rewrite operation using async api"""
        time_start = time.perf_counter()
        # prepare
        msgs = cls.prepare_model_call(query_msg, model, **kwargs)
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "QueryRewriter.rewrite.default_request_id",
        )

        # call model
        if msgs is not None and len(msgs) > 0:
            if hasattr(model, "async_call"):
                response = await model.async_call(msgs)
            else:
                logger.warning(
                    "QueryRewriter.async_rewrite:"
                    "Model does not support async call, "
                    "use sync call instead",
                )
                response = model(msgs)
        else:
            response = None

        new_query_msgs = cls.postprocess_model_call(
            query_msg,
            response,
            **kwargs,
        )
        new_queries = [q.content for q in new_query_msgs]

        logger.query_info(
            request_id=request_id,
            location="QueryRewriter.rewrite",
            context={
                "time_cost": time.perf_counter() - time_start,
                "msgs": msgs,
                "new_query": new_queries,
            },
        )
        return new_query_msgs

    @classmethod
    @abstractmethod
    def prepare_model_call(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        **kwargs: Any,
    ) -> List[dict]:
        """prepare for model calling"""

    @classmethod
    @abstractmethod
    def postprocess_model_call(
        cls,
        query_msg: Msg,
        response: ModelResponse,
        **kwargs: Any,
    ) -> Optional[List[Msg]]:
        """Postprocess the model return"""


class TranslationRewriter(QueryRewriter):
    """
    Translate the query to specified language
    """

    TRANSLATION_TEMPLATE = """
        Translate the content after TEXT to {}. /n
        TEXT: {}
    """

    @classmethod
    def prepare_model_call(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        target_language: str = "English",
        **kwargs: Any,
    ) -> List[dict]:
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "QueryRewriter.rewrite.default_request_id",
        )

        logger.query_info(
            request_id=request_id,
            location="TranslationRewriter.rewrite",
            context={"query_msg": query_msg},
        )

        if isinstance(query_msg, Msg):
            query = query_msg.content or ""
            query_language = metadata.get("language", "")
        else:
            query = query_msg
            query_language = "Chinese"

        if query_language.lower() != target_language.lower():
            if isinstance(model, DashScopeChatWrapper):
                # for the performance of QWEN model, we manually
                # compose the query for better results
                translated = TranslationRewriter.TRANSLATION_TEMPLATE.format(
                    target_language,
                    query,
                )
                msgs = [
                    {
                        "role": "system",
                        "content": "You are a helpful assistant for "
                        "translation.",
                    },
                    {
                        "role": "user",
                        "content": translated,
                    },
                ]
            else:
                msg = Msg(
                    name=query_msg.name,
                    role=query_msg.role,
                    content=TranslationRewriter.TRANSLATION_TEMPLATE.format(
                        target_language,
                        query,
                    ),
                )
                msgs = model.format(msg)
        else:
            msgs = []

        return msgs

    @classmethod
    def postprocess_model_call(
        cls,
        query_msg: Msg,
        response: ModelResponse,
        **kwargs: Any,
    ) -> Any:
        if response is None:
            return None

        new_query = Msg(
            name=query_msg.name,
            role=query_msg.role,
            content=response.text,
        )
        return new_query


RETRIEVAL_REWRITE_PARSER = MarkdownJsonDictParser(
    content_hint={
        "rewritten_queries": [
            "query 1",
            "query 2",
        ],
    },
)


class RetrievalRewriter(QueryRewriter):
    """
    Rewrite with some retrieved content from a knowledge base
    """

    @classmethod
    def prepare_model_call(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        max_rewrite: int = 2,
        retrieval_per_knowledge: int = 2,
        knowledge_list: List[LlamaIndexKnowledge] = None,
        **kwargs: Any,
    ) -> List[dict]:
        """
        Original rewrite query
        """
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "RetrievalRewriter.rewrite.default_request_id",
        )
        logger.query_info(
            request_id=request_id,
            location="RetrievalRewriter.rewrite",
            context={"query_msg": query_msg},
        )

        assert knowledge_list is not None
        retrieved_nodes = []
        for knowledge in knowledge_list:
            retrieved_nodes.extend(
                knowledge.retrieve(query_msg.content, retrieval_per_knowledge),
            )

        retrieval_rewrite_prompt = (
            "You are given an initial query and some retrieved content based "
            "on that query. "
            "Your task is to rewrite the original query to make it more "
            "specific and accurate, utilizing the provided retrieved content. "
            "The goal is to ensure the rewritten query can yield more precise "
            "and relevant results in future searches."
            "Follow these steps:\n1. Break down the query into its core "
            "components or questions."
            "Look for conjunctions, punctuation, and other indicators of "
            "multiple parts.\n"
            "2. Add missing details by referring to the additional contents, "
            "and rephrase each identified component into a simpler, "
            "standalone question."
        )

        # ==== rewrite based on retrieval ====
        retrieved_docs_to_string = ""
        for node in retrieved_nodes:
            doc_context = node.get_content(MetadataMode.NONE) + "\n"
            retrieved_docs_to_string += doc_context

        origin_language = metadata.get("language", None)
        if origin_language is None:
            origin_language, query_msg = check_language_type(query_msg, model)

        query_related_prompt = (
            f"# Additional Contents:\n {retrieved_docs_to_string} \n\n\n"
            f"# Query: {query_msg.content}\n"
            f"# Number of queries: {max_rewrite}\n"
            f"# Languages of rewritten queries: {origin_language}\n"
            f"# Answer formate: {RETRIEVAL_REWRITE_PARSER.format_instruction}"
        )

        if isinstance(model, DashScopeChatWrapper):
            msgs = [
                {"role": "system", "content": retrieval_rewrite_prompt},
                {"role": "user", "content": query_related_prompt},
            ]
        else:
            msgs = model.format(
                Msg(
                    name="system",
                    role="system",
                    content=retrieval_rewrite_prompt,
                ),
                Msg(name="user", content=query_related_prompt, role="system"),
            )

        return msgs

    @classmethod
    def postprocess_model_call(
        cls,
        query_msg: Msg,
        response: ModelResponse,
        **kwargs: Any,
    ) -> Any:
        new_query_msgs = []
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "RetrievalRewriter.async_rewrite.default_request_id",
        )
        try:
            response = RETRIEVAL_REWRITE_PARSER.parse(response)
            new_queries = response.parsed["rewritten_queries"]
        except Exception as e:
            new_queries = []
            logger.query_error(
                request_id=request_id,
                location="RetrievalRewriter.async_rewrite",
                context={"error": str(e)},
            )

        origin_language = metadata.get("language", None)

        new_query_languages = [
            origin_language for _ in range(len(new_queries))
        ]

        logger.query_info(
            request_id=request_id,
            location="RetrievalRewriter.async_rewrite",
            context={
                "new_queries": new_queries,
                "new_query_languages": new_query_languages,
            },
        )

        for new_query, query_language in zip(new_queries, new_query_languages):
            new_query_msgs.append(
                Msg(
                    name=query_msg.name,
                    role=query_msg.role,
                    content=new_query,
                    metadata={"language": query_language},
                ),
            )

        return new_query_msgs


class HyDERewriter(QueryRewriter):
    """HyDE rewrite"""

    @classmethod
    def prepare_model_call(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        **kwargs: Any,
    ) -> List[dict]:
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "HyDERewriter.async_rewrite.default_request_id",
        )
        logger.query_info(
            request_id=request_id,
            location="HyDERewriter.rewrite",
            context={"query_msg": query_msg},
        )

        sys_prompt = (
            "You are a helpful assistant. Pretending you know the answer of "
            "the question and "
            "try your best to write answers that answers the question. The "
            "answers should be at most "
            "80 words (最多80个字) and in the specified language."
        )
        origin_language = metadata.get("language", None)
        if origin_language is None:
            origin_language, query_msg = check_language_type(query_msg, model)

        if isinstance(model, DashScopeChatWrapper):
            msgs = [
                {"role": "system", "content": sys_prompt},
                {
                    "role": "user",
                    "content": (
                        f"# Question:\n {query_msg.content}\n"
                        + f"# Targeting language: {origin_language}\n"
                    ),
                },
            ]
        else:
            msgs = model.format(
                Msg(
                    role="system",
                    name="system",
                    content=sys_prompt,
                ),
                Msg(
                    role="user",
                    name="user",
                    content=f"Question: {query_msg.content}",
                ),
            )
        return msgs

    @classmethod
    def postprocess_model_call(
        cls,
        query_msg: Msg,
        response: ModelResponse,
        **kwargs: Any,
    ) -> Optional[List[Msg]]:
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        language = metadata.get("language", None)
        request_id = metadata.get(
            "request_id",
            "HyDERewriter.async_rewrite.default_request_id",
        )
        hyde_queries = []
        try:
            answer = response.text
            hyde_queries.append(
                Msg(
                    role="user",
                    name="user",
                    content=query_msg.content + "\n" + answer,
                    metadata={"language": language},
                ),
            )
        except Exception as e:
            hyde_queries.append(query_msg)
            logger.query_error(
                request_id=request_id,
                location="HyDERewriter.rewrite",
                context={"error": str(e)},
            )
        return hyde_queries


class PromptRewriter(QueryRewriter):
    """
    Rewrite query following the prompt
    """

    @classmethod
    def prepare_model_call(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        additional_prompt: str = "",
        **kwargs: Any,
    ) -> List[dict]:
        """
        Original rewrite query
        """
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "PromptRewriter.rewrite.default_request_id",
        )
        logger.query_info(
            request_id=request_id,
            location="PromptRewriter.rewrite",
            context={"query_msg": query_msg},
        )

        prompt_rewrite_prompt = (
            "You are given a QUERY and some ADDITIONAL REQUIREMENT to rewrite "
            "that query."
            "Your task is to rewrite the query so that the rewritten query "
            "taking the ADDITIONAL REQUIREMENT into consideration."
        )

        # ==== rewrite based on prompt ====
        origin_language = metadata.get("language", None)
        if origin_language is None:
            origin_language, query_msg = check_language_type(query_msg, model)

        query_related_prompt = (
            f"# ADDITIONAL REQUIREMENT:\n {additional_prompt} \n\n\n"
            f"# QUERY: {query_msg.content}\n"
            f"# Languages of rewritten queries: {origin_language}\n"
            f"# Answer formate: {RETRIEVAL_REWRITE_PARSER.format_instruction}"
        )

        if isinstance(model, DashScopeChatWrapper):
            msgs = [
                {"role": "system", "content": prompt_rewrite_prompt},
                {"role": "user", "content": query_related_prompt},
            ]
        else:
            msgs = model.format(
                Msg(
                    name="system",
                    role="system",
                    content=prompt_rewrite_prompt,
                ),
                Msg(name="user", content=query_related_prompt, role="system"),
            )
        return msgs

    @classmethod
    def postprocess_model_call(
        cls,
        query_msg: Msg,
        response: ModelResponse,
        **kwargs: Any,
    ) -> Optional[List[Msg]]:
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "PromptRewriter.rewrite.default_request_id",
        )
        origin_language = metadata.get("language", None)
        try:
            response = RETRIEVAL_REWRITE_PARSER.parse(response)
        except Exception as e:
            response.parsed = {"rewritten_queries": []}
            logger.query_error(
                request_id=request_id,
                location="PromptRewriter.async_rewrite:output",
                context={"error": str(e)},
            )
        new_queries = response.parsed["rewritten_queries"]

        new_query_languages = [
            origin_language for _ in range(len(new_queries))
        ]

        new_query_msgs = []
        for new_query, query_language in zip(new_queries, new_query_languages):
            new_query_msgs.append(
                Msg(
                    name=query_msg.name,
                    role=query_msg.role,
                    content=new_query,
                    metadata={
                        "request_id": request_id,
                        "language": query_language,
                    },
                ),
            )
        return new_query_msgs


KEYWORD_REWRITE_PARSER = MarkdownJsonDictParser(
    content_hint={
        "keywords": [
            "keyword 1",
            "keyword 2",
        ],
    },
)

KEYWORD_BLACK_LIST = {"推荐", "最好的"}
supply_words = {
    "llm": "大语言模型",
    "tts": "音频生成",
    "语音合成": "音频生成",
    "txt2img": "图像生成",
    "推荐": "最新 最受欢迎",
}


class KeywordRewriter(QueryRewriter):
    """
    Rewrite a complete query into keywords for search
    """

    @classmethod
    def prepare_model_call(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        additional_prompt: str = "",
        **kwargs: Any,
    ) -> List[dict]:
        """
        Original rewrite query
        """
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "KeywordRewriter.rewrite.default_request_id",
        )
        logger.query_info(
            request_id=request_id,
            location="KeywordRewriter.rewrite",
            context={"query_msg": query_msg},
        )

        retrieval_rewrite_prompt = (
            "You are given a QUERY."
            "Your task is to extract the keywords from the query that can be"
            " used to for searching with search engine and bm25."
            "Nouns (名词) are preferred keywords, try to avoid verbs."
            "REMOVE the adjective word, such as the `the best`! "
            "Return AT MOST THREE keywords. Return AT MOST 3 keywords."
        )

        # ==== rewrite based on prompt ====
        origin_language = metadata.get("language", None)
        if origin_language is None:
            origin_language, query_msg = check_language_type(query_msg, model)

        query_related_prompt = (
            f"# ADDITIONAL REQUIREMENT:\n {additional_prompt} \n\n\n"
            f"# QUERY: {query_msg.content}\n"
            f"# Languages of keywords: {origin_language}\n"
            f"# Answer format: {KEYWORD_REWRITE_PARSER.format_instruction}"
        )
        if isinstance(model, DashScopeChatWrapper):
            msgs = [
                {"role": "system", "content": retrieval_rewrite_prompt},
                {"role": "user", "content": query_related_prompt},
            ]
        else:
            msgs = model.format(
                Msg(
                    name="system",
                    role="system",
                    content=retrieval_rewrite_prompt,
                ),
                Msg(name="user", content=query_related_prompt, role="system"),
            )
        return msgs

    @classmethod
    def postprocess_model_call(
        cls,
        query_msg: Msg,
        response: ModelResponse,
        merge_keywords: bool = True,
        **kwargs: Any,
    ) -> Optional[List[Msg]]:
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "KeywordRewriter.rewrite.default_request_id",
        )
        origin_language = metadata.get("language", None)

        try:
            response = KEYWORD_REWRITE_PARSER.parse(response)
        except Exception as e:
            response.parsed = {"keywords": [query_msg.content]}
            logger.query_error(
                request_id=request_id,
                location="KeywordRewriter.async_rewrite",
                context={"error": str(e)},
            )
        try:
            keywords = response.parsed["keywords"]
            for key, value in supply_words.items():
                if key in str(query_msg.content).lower():
                    keywords.append(value)
            if merge_keywords:
                new_queries = [" ".join(response.parsed["keywords"])]
            else:
                queries = response.parsed["keywords"]
                new_queries = [
                    q for q in queries if q not in KEYWORD_BLACK_LIST
                ]
        except Exception as e:
            new_queries = [query_msg.content]
            logger.query_error(
                request_id=request_id,
                location="KeywordRewriter.async_rewrite",
                context={"error": str(e)},
            )

        new_query_languages = [
            origin_language for _ in range(len(new_queries))
        ]

        new_query_msgs = []
        for new_query, query_language in zip(new_queries, new_query_languages):
            new_query_msgs.append(
                Msg(
                    name=query_msg.name,
                    role=query_msg.role,
                    content=new_query,
                    metadata={"language": query_language},
                ),
            )
        return new_query_msgs


class PromptContextRewriter(QueryRewriter):
    """
    Rewrite the query following provided prompt
    and consider conversation context
    """

    parser = RETRIEVAL_REWRITE_PARSER

    @classmethod
    def _get_prompt(
        cls,
        query_msg: Msg,
        system_prompt: str = None,
        additional_prompt: str = "EMPTY\n",
        local_short_history: Optional[str] = None,
    ) -> str:
        if (
            local_short_history is None
            and query_msg.metadata is not None
            and "context" in query_msg.metadata
            and len(query_msg.metadata["context"]) > 0
        ):
            local_short_history = query_msg.metadata["context"]
        elif local_short_history is None:
            local_short_history = "EMPTY\n"
        # TODO: system_prompt is never used, consider to remove it?
        if system_prompt is None:
            system_prompt = ""

        context_rewrite_prompt = (
            f"{system_prompt}"
            "You are given a QUERY, an ADDITIONAL REQUIREMENT and a piece of "
            "CONTEXT to rewrite that query."
            "If the CONTEXT is related to the current QUERY, rewrite query "
            "taking the ADDITIONAL REQUIREMENT "
            "and CONTEXT into consideration."
            "Especially, if the query has demonstrative pronoun (e.g., "
            "he/she/it) or lacks of "
            "clear referee, rewrite it to be a more self-contained query. "
            "You also need to follow the instruction of ADDITIONAL REQUIREMENT"
            " to generate query."
            "The rewritten query MUST be in the same language as the provide "
            "Query."
            f"# QUERY: {query_msg.content}\n"
            f"# ADDITIONAL REQUIREMENT:\n {additional_prompt} \n"
            f"# CONTEXT: {local_short_history}\n"
            f"# Answer format: {cls.parser.format_instruction}"
        )
        return context_rewrite_prompt

    @staticmethod
    def _get_messages(
        model: ModelWrapperBase,
        context_rewrite_prompt: str,
    ) -> list[dict]:
        # ==== rewrite based on context ====
        if isinstance(model, DashScopeChatWrapper):
            msgs = [
                {"role": "user", "content": context_rewrite_prompt},
            ]
        else:
            msg = Msg(
                name="user",
                role="user",
                content=context_rewrite_prompt,
            )
            msgs = model.format(msg)
        return msgs

    @classmethod
    def prepare_model_call(
        cls,
        query_msg: Msg,
        model: ModelWrapperBase,
        additional_prompt: str = "",
        system_prompt: str = "",
        local_short_history: str = "",
        **kwargs: Any,
    ) -> List[dict]:
        """
        Original rewrite query
        """
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "PromptContextRewriter.rewrite.default_request_id",
        )
        logger.query_info(
            request_id=request_id,
            location="PromptContextRewriter.rewrite",
            context={"query_msg": query_msg},
        )
        context_rewrite_prompt = cls._get_prompt(
            query_msg,
            system_prompt,
            additional_prompt,
            local_short_history,
        )
        msgs = cls._get_messages(
            model,
            context_rewrite_prompt,
        )
        return msgs

    @classmethod
    def postprocess_model_call(
        cls,
        query_msg: Msg,
        response: ModelResponse,
        **kwargs: Any,
    ) -> Optional[List[Msg]]:
        metadata = query_msg.metadata if query_msg.metadata is not None else {}
        request_id = metadata.get(
            "request_id",
            "PromptContextRewriter.rewrite.default_request_id",
        )
        new_query_msgs = []
        try:
            response = cls.parser.parse(response)
        except Exception as e:
            response.parsed = {"rewritten_queries": [response.text]}
            logger.query_error(
                request_id=request_id,
                location="PromptContextRewriter.async_rewrite",
                context={"error": str(e)},
            )

        if (
            "rewritten_queries" in response.parsed
            and len(response.parsed["rewritten_queries"]) > 0
        ):
            new_query = " ".join(response.parsed["rewritten_queries"])
            metadata.setdefault("language", "Chinese")
            new_query_msgs.append(
                Msg(
                    name=query_msg.name,
                    role=query_msg.role,
                    content=new_query,
                    metadata=metadata,
                ),
            )
        if len(new_query_msgs) == 0:
            new_query_msgs = [query_msg]
        return new_query_msgs


CONTEXT_REWRITE_PARSER = MarkdownJsonDictParser(
    content_hint={
        "analysis": "A brief analysis of what information from CONTEXT can"
        "help make the QUERY more clear.",
        "rewritten_queries": ["rewritten query based on CONTEXT"],
    },
)


class ContextRewriter(PromptContextRewriter):
    """
    Rewrite query based on conversation context, e.g., filling missed  pronouns
    """

    parser = CONTEXT_REWRITE_PARSER

    @classmethod
    def _get_prompt(
        cls,
        query_msg: Msg,
        system_prompt: str = "",
        additional_prompt: str = "EMPTY\n",
        local_short_history: Optional[str] = None,
    ) -> str:
        """prepare prompt for the context rewrite"""
        if (
            local_short_history is None
            and query_msg.metadata is not None
            and "context" in query_msg.metadata
            and len(query_msg.metadata["context"]) > 0
        ):
            local_short_history = query_msg.metadata["context"]
        elif local_short_history is None:
            local_short_history = "EMPTY\n"

        context_rewrite_prompt = (
            "You are given a QUERY and a piece of CONTEXT to rewrite "
            "that query.\n"
            "# Requirement\n"
            "1. The CONTEXT may be related to the current QUERY."
            "Especially, if the query has he/she/it/this/that/these/those or "
            "lacks of "
            "clear referee, rewrite it to be a more self-contained query. \n"
            "2. If there is a messages in the history that is very similar to "
            "the current one,"
            "it means the user is NOT satisfied with the previous answer."
            "In this case, you should consider rewrite the query so that it "
            "can lead to generate a DIFFERENT and more comprehensive answer."
            "In this case, DO NOT bring any information from the history to "
            "query rewrite."
            "If the QUERY is complete itself, DO NOT change it."
            "The rewritten query MUST be in the same language as the provide "
            "Query.\n"
            f"# CONTEXT: {local_short_history}\n"
            f"# QUERY: {query_msg.content}\n"
            f"# Answer format: {cls.parser.format_instruction}"
        )
        return context_rewrite_prompt
