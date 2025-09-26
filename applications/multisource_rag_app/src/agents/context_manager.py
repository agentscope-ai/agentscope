# -*- coding: utf-8 -*-
# pylint: disable=E0611
"""
A special class of agent to digest conversation context
"""
import asyncio
import copy
import json
from typing import Any, List

from pydantic import BaseModel, Field

from agentscope.manager import ModelManager
from agentscope.memory import TemporaryMemory
from ..utils.logging import logger

from agentscope.agents import AgentBase
from agentscope.message import Msg
from agentscope.model import DashScopeChatWrapper
from agentscope.parsers import MarkdownJsonDictParser
from .agent_util.distill_message import rule_based_shorten_msg
from .agent_util.query_rewrite import ContextRewriter


class RelatedMessages(BaseModel):
    """
    For related message extraction and analysis
    """

    analysis: str = Field(
        ...,
        description="a brief analysis of the relation of latest "
        "query to the history of the "
        "conversation",
    )
    indices_of_related_messages: List[int] = Field(
        ...,
        description="list of indices for the messages related "
        "to the latest query",
    )


RELATED_MESSAGE_PARSER = MarkdownJsonDictParser(content_hint=RelatedMessages)


class ContextManager(AgentBase):
    """
    Agent to digest conversation context
    """

    def __init__(
        self,
        name: str,
        model_config_name: str,
        sys_prompt: str = "",
        memory_context_length: int = 20,
        **kwargs: Any,
    ):
        super().__init__()
        self.name = name
        self.sys_prompt = (
            sys_prompt
            if sys_prompt
            else "You are a manager to manage the context of the conversation. "
                 "Your duty is to extract the useful information from a long "
                 "conversation that is relevant to the latest query."
                 "If the latest query contains demonstrative pronoun "
                 "(he/she/it/this/that/these/those) or lacks of clear referee, "
                 "try to figure out in the context of the history."
        )

        # 手动初始化 model
        model_manager = kwargs.get("model_manager", ModelManager.get_instance())
        self.model = model_manager.get_model_by_config_name(model_config_name)

        self.mem_context_length = memory_context_length
        self.max_memories = 4
        self.max_len_per_memory = 100
        self.memory = TemporaryMemory()

    def reply(self, x: dict = None) -> Msg:
        return asyncio.get_event_loop().run_until_complete(
            self.async_reply(x),
        )

    async def async_reply(self, x: Msg = None) -> Msg:
        """
        Async version of the reply function
        """
        if x.metadata is None:
            x.metadata = {}
        request_id = x.metadata.setdefault(
            "request_id",
            "context_manager.async_reply.default_request_id",
        )

        x.role = "user"
        x.name = "user"
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:input",
            context={"x": x},
        )

        context_str = self._get_context_str()
        context_str += f"【Latest query】{x.role}: {x.content} \n\n"
        context_str += f"{RELATED_MESSAGE_PARSER.format_instruction}"

        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:prompt",
            context={
                "self.sys_prompt": self.sys_prompt,
                "context_str": context_str,
            },
        )

        if isinstance(self.model, DashScopeChatWrapper):
            prompt = [
                {"role": "system", "content": self.sys_prompt},
                {"role": "user", "content": context_str},
            ]
        else:
            prompt = self.model.format(
                Msg(name="system", role="system", content=self.sys_prompt),
                Msg(name="user", role="user", content=context_str),
            )

        logger.info(
            f"{self.name}.model has async_call? "
            f"{hasattr(self.model, 'async_call')}",
        )
        response = None

        if hasattr(self.model, "async_call"):
            error_time = 0
            max_retries = 1
            while error_time < max_retries:
                try:
                    response = await self.model.async_call(prompt)
                    response = RELATED_MESSAGE_PARSER.parse(response)
                    break
                except:  # noqa: E722 # pylint: disable=W0702
                    error_time += 1
                    if error_time == max_retries:
                        response = None
        else:
            response = self.model(
                prompt,
                parse_func=RELATED_MESSAGE_PARSER.parse,
                fault_handler=lambda _: None,
            )
        if response is None:
            # in case the parsing fail, return empty context
            empty_answer = {
                "analysis": "",
                "summary": "",
                "context": [],
                "indices_of_related_messages": [],
            }
            return Msg(
                name=self.name,
                role="assistant",
                content=empty_answer,
                metadata={"request_id": request_id},
            )

        related_context_idxs = response.parsed["indices_of_related_messages"]
        try:
            conversation_context = self.memory.get_memory(
                recent_n=self.mem_context_length,
            )
            related_context = [
                conversation_context[i].to_dict() for i in related_context_idxs
            ]
        except IndexError:
            related_context = []
        return_content = copy.deepcopy(response.parsed)
        return_content["context"] = related_context
        logger.query_info(
            request_id=request_id,
            location=self.name + ".async_reply:output",
            context={"return_content": return_content},
        )
        return Msg(
            name=self.name,
            role="assistant",
            content=return_content,
            metadata={"request_id": request_id},
        )

    def _get_context_str(self) -> str:
        conversation_context = self.memory.get_memory(
            recent_n=self.mem_context_length,
        )
        context_str = "History of the conversation: \n\n"
        for i, msg in enumerate(conversation_context):
            if (
                len(conversation_context) <= self.max_memories
                or i >= len(conversation_context) - self.max_memories
            ):
                context_str += (
                    f"【Index: {i}】 {msg.name}: "
                    + json.dumps(
                        rule_based_shorten_msg(msg),
                        ensure_ascii=False,
                        default=lambda x: x.to_dict(),
                    )
                    + " \n\n"
                )
        return context_str

    def rewrite_query(self, query: Msg = None) -> Msg:
        """rewrite query based on conversation context"""
        context_str = self._get_context_str()
        new_query = ContextRewriter.rewrite(
            query,
            self.model,
            local_short_history=context_str,
            context_only=True,
        )
        if new_query is None or len(new_query) == 0:
            return query
        else:
            return new_query[0]

    async def async_rewrite_query(self, query: Msg = None) -> Msg:
        """
        rewrite query based on conversation context
        """
        context_str = self._get_context_str()
        new_query = await ContextRewriter.async_rewrite(
            query,
            self.model,
            local_short_history=context_str,
            context_only=True,
        )
        if new_query is None or len(new_query) == 0:
            return query
        else:
            return new_query[0]
