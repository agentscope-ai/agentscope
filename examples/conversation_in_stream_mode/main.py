# -*- coding: utf-8 -*-
"""Main script for running the streaming agent."""
from typing import Optional, Union, Sequence
import agentscope
from agentscope.agent import AgentBase, UserAgent
from agentscope.message import Msg

###############################################################
#            Set your own model configuration                 #
###############################################################

YOUR_SELECTED_MODEL_CONFIG_NAME = "stream_ds"

agentscope.init(
    model_configs=[
        {
            "config_name": "stream_openai",
            "model_type": "openai_chat",
            "model_name": "gpt-4",
            "api_key": "{YOUR_API_KEY}",
            "stream": True,
        },
        {
            "config_name": "stream_ds",
            "model_type": "dashscope_chat",
            "model_name": "qwen-max",
            "api_key": "{YOUR_API_KEY}",
            "stream": True,
        },
        {
            "config_name": "stream_ollama",
            "model_type": "ollama_chat",
            "model_name": "llama2",
            "stream": True,
        },
        {
            "config_name": "stream_zhipuai",
            "model_type": "zhipuai_chat",
            "model_name": "glm-4",
            "stream": True,
            "api_key": "{YOUR_API_KEY}",
        },
        {
            "config_name": "stream_litellm",
            "model_type": "litellm_chat",
            "model_name": "gpt-4",
            "stream": True,
        },
    ],
    save_api_invoke=True,
    # If AgentScope Studio is running locally
    # studio_url="http://127.0.0.1:5000",
    project="Conversation in Stream Mode",
)


###############################################################


# Step1: Create a new agent that prints in streaming mode
class StreamingAgent(AgentBase):
    """An agent that speaks streaming messages."""

    def __init__(
        self,
        name: str,
        sys_prompt: str,
        model_config_name: str,
    ) -> None:
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model_config_name=model_config_name,
        )

        self.memory.add(Msg(self.name, self.sys_prompt, "system"))

    def reply(self, x: Optional[Union[Msg, Sequence[Msg]]] = None) -> Msg:
        self.memory.add(x)

        prompt = self.model.format(self.memory.get_memory())

        res = self.model(prompt)

        # Print the streaming message in the terminal and Studio
        # (if registered)

        # The stream filed is a generator that yields the streaming text
        self.speak(res.stream)

        # The text field of the response will be filled with the full response
        # text after the streaming is finished
        msg_returned = Msg(self.name, res.text, "assistant")

        self.memory.add(msg_returned)

        return msg_returned


# Step2: Initialize the agent and start the conversation
agent = StreamingAgent(
    "assistant",
    sys_prompt="You're a helpful assistant",
    model_config_name=YOUR_SELECTED_MODEL_CONFIG_NAME,
)
user = UserAgent("user")

msg = None
while True:
    msg = user(msg)
    if msg.content == "exit":
        break
    msg = agent(msg)
