# -*- coding: utf-8 -*-
"""Searcher agent."""

from functools import partial
from typing import Optional, Union, Sequence

from agentscope.message import Msg
from agentscope.agent import AgentBase
from agentscope.service import google_search, bing_search


class SearcherAgent(AgentBase):
    """An agent with search tool."""

    def __init__(
        self,
        name: str,
        model_config_name: str = None,
        result_num: int = 10,
        search_engine_type: str = "google",
        api_key: str = None,
        cse_id: str = None,
    ) -> None:
        """Init a SearcherAgent.

        Args:
            name (`str`): the name of this agent.
            model_config_name (`str`, optional): The name of model
            configuration for this agent. Defaults to None.
            result_num (`int`, optional): The number of return results.
            Defaults to 10.
            search_engine_type (`str`, optional): the search engine to use.
            Defaults to "google".
            api_key (`str`, optional): api key for the search engine. Defaults
            to None.
            cse_id (`str`, optional): cse_id for the search engine. Defaults to
            None.
        """
        super().__init__(
            name=name,
            sys_prompt="You are an AI assistant who optimizes search"
            " keywords. You need to transform users' questions into a series "
            "of efficient search keywords.",
            model_config_name=model_config_name,
            use_memory=False,
        )
        self.result_num = result_num
        if search_engine_type == "google":
            assert (api_key is not None) and (
                cse_id is not None
            ), "google search requires 'api_key' and 'cse_id'"
            self.search = partial(
                google_search,
                api_key=api_key,
                cse_id=cse_id,
            )
        elif search_engine_type == "bing":
            assert api_key is not None, "bing search requires 'api_key'"
            self.search = partial(bing_search, api_key=api_key)

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        prompt = self.model.format(
            Msg(name="system", role="system", content=self.sys_prompt),
            x,
            Msg(
                name="user",
                role="user",
                content="Please convert the question into keywords. The return"
                " format is:\nKeyword1 Keyword2...",
            ),
        )
        query = self.model(prompt).text
        results = self.search(
            question=query,
            num_results=self.result_num,
        ).content
        msg = Msg(
            self.name,
            content=[
                Msg(
                    name=self.name,
                    content=result,
                    role="assistant",
                    url=result["link"],
                    metadata=x.content,
                )
                for result in results
            ],
            role="assistant",
        )
        self.speak(
            Msg(
                name=self.name,
                role="assistant",
                content="Search results:\n"
                f"{[result['link'] for result in results]}",
            ),
        )
        return msg
