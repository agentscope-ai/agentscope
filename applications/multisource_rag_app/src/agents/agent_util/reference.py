# -*- coding: utf-8 -*-
# pylint: disable=R0912
"""
Generate answer with reference
"""
import json
from typing import List, Tuple, Sequence

from loguru import logger

from agentscope.message import Msg
from agentscope.model import (
    ModelWrapperBase,
    DashScopeChatWrapper,
)
from agentscope.parsers import MarkdownJsonDictParser
from agentscope.rag import RetrievedChunk


CHECKING_PROMPT_3 = (
    "You are a helpful assistant who will exam if the retrieved content has "
    "relevant information to the query."
    "There will be potentially related conversation history may help you "
    "better understand the query."
    "1. You need to check the retrieved content one by one, provide concise "
    "analysis for each retrieved content, and"
    "decide what are the most relevant content.\n"
    "2. If some of the retrieved content is relevant, return the list of "
    "indices of those relevant content, "
    "sorted in descending order of relevance. If none of the retrieved "
    "content is relevant to the query, "
    "return the analysis and an empty list of indices.\n"
    "3. Try your best to answer the question based on the retrieved content "
    "and your analysis. NEVER mention the indices"
    "of the retrieved content because they are not visible to users.\n"
)

CHECKING_PARSER_3 = MarkdownJsonDictParser(
    content_hint={
        "Analysis": "Analysis of why this content is the most relevant "
        "retrieved content.",
        "Indices": [
            "index of the most relevant retrieved content",
            "index of the 2nd most relevant retrieved content",
            "index of the 3rd most relevant retrieved content",
        ],
        "Answer": "Answer to the query based on the retrieved content and your"
        " analysis. NO NEWLINE in the answer! NO line breaking in the "
        "answer!",
    },
)

LANGUAGE_GENERATION_PROMPT = """You MUST generate the Answer in {}. """


async def analysis_reference_v3(
    query_msg: Msg,
    sys_prompt: str,
    nodes: List[RetrievedChunk],
    model: ModelWrapperBase,
    context_history: str,
    language: str = "Chinese",
) -> Tuple[List[str], str, str]:
    """
    Generate answer with reference
    """
    # check if the query is related to some specific part
    info_pieces = [
        {
            "Index": i,
            "Content": node.content,
            "Reference": node.metadata.get("file_path", ""),
        }
        for i, node in enumerate(nodes)
    ]
    if len(context_history) == 0:
        context_history = "EMPTY"
    combine = (
        f"Immediate conversation history: {context_history} \n\n"
        + sys_prompt
        + "\n"
        + "Retrieved content:\n"
        + f"{json.dumps(info_pieces, indent=4, ensure_ascii=False)}\n\n"
        f"Query: {query_msg.content}"
    )
    if isinstance(model, DashScopeChatWrapper):
        checking_prompt = [
            {
                "role": "system",
                "content": CHECKING_PROMPT_3
                + CHECKING_PARSER_3.format_instruction
                + LANGUAGE_GENERATION_PROMPT.format(
                    language,
                ),
            },
            {
                "role": "user",
                "content": combine,
            },
        ]
    else:
        checking_prompt = model.format(
            Msg(
                name="system",
                role="system",
                content=CHECKING_PROMPT_3
                + CHECKING_PARSER_3.format_instruction
                + LANGUAGE_GENERATION_PROMPT.format(
                    language,
                ),
            ),
            Msg(name="user", role="user", content=combine),
        )
    logger.info(f"final prompt:\n {checking_prompt}")
    if hasattr(model, "async_call"):
        checking_response = await model.async_call(checking_prompt)
        try:
            checking_response = CHECKING_PARSER_3.parse(checking_response)
        except Exception as e:
            logger.warning(
                "Fail to parse, try to continue as only "
                f"having Answer field: {e}",
            )
            checking_response.parsed = {
                "Analysis": "",
                "Indices": [],
                "Answer": checking_response.text,
            }
    else:
        try:
            checking_response = model(
                checking_prompt,
                parse_func=CHECKING_PARSER_3.parse,
            )
        except Exception as e:
            checking_response = model(checking_prompt)
            logger.warning(
                "Fail to parse, try to continue as only having "
                f"Answer field: {e}",
            )
            checking_response.parsed = {
                "Analysis": "",
                "Indices": [],
                "Answer": checking_response.text,
            }

    logger.info(
        f"[Decide whether to add source?] \n {checking_response.parsed}",
    )
    return_references = []
    analysis = (
        "** Fail to parse the analysis from previous response. "
        "Ignore this block **"
    )
    answer = "** Fail to answer ** "
    if "Indices" in checking_response.parsed and isinstance(
        checking_response.parsed["Indices"],
        list,
    ):
        for i in checking_response.parsed["Indices"]:
            try:
                if isinstance(i, str):
                    idx = int(i)
                    return_references.append(
                        nodes[idx].metadata.get("file_path", ""),
                    )
                elif isinstance(i, int):
                    return_references.append(
                        nodes[i].metadata.get("file_path", ""),
                    )
            except IndexError as e:
                logger.warning(f"{e}: index error: {i} our of range")
    # sometimes it happens that LLM gives "indices" instead of "Indices"
    if "indices" in checking_response.parsed and isinstance(
        checking_response.parsed["indices"],
        list,
    ):
        for i in checking_response.parsed["indices"]:
            try:
                if isinstance(i, str):
                    idx = int(i)
                    return_references.append(
                        nodes[idx].metadata.get("file_path", ""),
                    )
                elif isinstance(i, int):
                    return_references.append(
                        nodes[i].metadata.get("file_path", ""),
                    )
            except IndexError as e:  # noqa: E722
                logger.warning(f"{e}: index error: {i} our of range")
    if "Analysis" in checking_response.parsed:
        analysis = checking_response.parsed["Analysis"]
    if "Answer" in checking_response.parsed:
        answer = checking_response.parsed["Answer"]

    return return_references, analysis, answer


def raw_return(
    nodes: List[RetrievedChunk],
) -> Sequence:
    """
    return raw content in llamaindex nodes
    """
    info_pieces = [
        {
            "Index": i,
            "Content": node.content,
            "Reference": node.metadata.get("file_path", ""),
        }
        for i, node in enumerate(nodes)
    ]
    return info_pieces
