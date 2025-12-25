# -*- coding: utf-8 -*-
"""
An example that demonstrates voice conversation.
"""
import os
from agentscope.agent import UserAgent
from examples.voice_conversation._audio_assistant_agent import (
    AudioAssistantAgent,
)


async def main() -> None:
    """The main entry point for the realtime voice conversation."""

    user_agent = UserAgent(name="user")
    user_agent.enable_audio_input()

    assistant_agent = AudioAssistantAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday",
        voice="Cherry",
        model="qwen3-omni-flash-realtime",
        api_key=os.getenv("DASHSCOPE_API_KEY"),
    )

    while True:
        msg = await user_agent()
        if msg.get_text_content() == "exit":
            break
        await assistant_agent(msg)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
