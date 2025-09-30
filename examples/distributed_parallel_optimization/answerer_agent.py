# -*- coding: utf-8 -*-
"""Answerer Agent."""
from typing import Optional, Union, Sequence

from agentscope.message import Msg
from agentscope.agent import AgentBase
from agentscope.service import load_web


class AnswererAgent(AgentBase):
    """An agent with web digest tool."""

    def __init__(
        self,
        name: str,
        model_config_name: str = None,
    ) -> None:
        super().__init__(
            name=name,
            sys_prompt="You are an AI assistant. You need to find answers to "
            "user questions based on specified web content.",
            model_config_name=model_config_name,
            use_memory=False,
        )

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        response = load_web(
            url=x.url,
            keep_raw=False,
            html_selected_tags=["p", "div", "h1", "li"],
            timeout=5,
        ).content
        if (
            "html_to_text" not in response
            or len(response["html_to_text"]) == 0
        ):
            return Msg(
                self.name,
                content=f"Unable to load web page [{x.url}].",
                role="assistant",
                url=x.url,
            )
        # prepare prompt
        prompt = self.model.format(
            Msg(name="system", role="system", content=self.sys_prompt),
            Msg(
                name="user",
                role="user",
                content=f"Please answer my question based on the content of"
                " the following web page:\n\n"
                f"{response['html_to_text']}"
                f"\n\nBased on the above web page,"
                f" please answer my question\n{x.metadata}",
            ),
        )
        # call llm and generate response
        response = self.model(prompt).text
        msg = Msg(self.name, content=response, role="assistant", url=x.url)

        self.speak(msg)

        return msg
