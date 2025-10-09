# -*- coding: utf-8 -*-
"""A simple example for conversation between user and assistant agent."""
import agentscope
from agentscope.agent import (
    DialogAgent,
    UserAgent,
)
from agentscope.pipelines import sequential_pipeline


def main() -> None:
    """A basic conversation demo"""

    agentscope.init(
        model_configs=[
            {
                "model_type": "openai_chat",
                "config_name": "gpt-3.5-turbo",
                "model_name": "gpt-3.5-turbo",
                "api_key": "xxx",  # Load from env if not provided
                "organization": "xxx",  # Load from env if not provided
                "generate_args": {
                    "temperature": 0.5,
                },
            },
            {
                "model_type": "post_api_chat",
                "config_name": "my_post_api",
                "api_url": "https://xxx",
                "headers": {},
            },
        ],
        project="Multi-Agent Conversation",
        save_api_invoke=True,
    )

    # Init two agent
    dialog_agent = DialogAgent(
        name="Assistant",
        sys_prompt="You're a helpful assistant.",
        model_config_name="gpt-3.5-turbo",  # replace by your model config name
    )
    user_agent = UserAgent()

    # start the conversation between user and assistant
    x = None
    while x is None or x.content != "exit":
        x = sequential_pipeline([dialog_agent, user_agent], x)


if __name__ == "__main__":
    main()
