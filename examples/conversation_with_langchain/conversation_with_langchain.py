# -*- coding: utf-8 -*-
"""A simple example of using langchain to create an assistant agent in
AgentScope."""
import os
from typing import Optional, Union, Sequence

from langchain_openai import OpenAI
from langchain.memory import ConversationBufferMemory
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain

import agentscope
from agentscope.agent import AgentBase
from agentscope.agent import UserAgent
from agentscope.message import Msg


class LangChainAgent(AgentBase):
    """An agent that implemented by langchain."""

    def __init__(self, name: str) -> None:
        """Initialize the agent."""

        # Disable AgentScope memory and use langchain memory instead
        super().__init__(name, use_memory=False)

        # [START] BY LANGCHAIN
        # Create a memory in langchain
        memory = ConversationBufferMemory(memory_key="chat_history")

        # Prepare prompt
        template = """
                You are a helpful assistant, and your goal is to help the user.

                {chat_history}
                Human: {human_input}
                Assistant:"""

        prompt = PromptTemplate(
            input_variables=["chat_history", "human_input"],
            template=template,
        )

        llm = OpenAI(openai_api_key=os.environ["OPENAI_API_KEY"])

        # Prepare a chain and manage the memory by LLMChain in langchain
        self.llm_chain = LLMChain(
            llm=llm,
            prompt=prompt,
            verbose=False,
            memory=memory,
        )
        # [END] BY LANGCHAIN

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        # [START] BY LANGCHAIN

        # Generate response
        response_str = self.llm_chain.predict(human_input=x.content)

        # [END] BY LANGCHAIN

        # Wrap the response in a message object in AgentScope
        return Msg(name=self.name, content=response_str, role="assistant")


# Build a conversation between user and assistant agent

# init AgentScope
agentscope.init(
    project="Conversation with LangChain",
)

# Create an instance of the langchain agent
agent = LangChainAgent(name="Assistant")

# Create a user agent from AgentScope
user = UserAgent("User")

msg = None
while True:
    # User input
    msg = user(msg)
    if msg.content == "exit":
        break
    # Agent speaks
    msg = agent(msg)
