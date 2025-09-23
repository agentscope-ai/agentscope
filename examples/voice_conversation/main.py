# -*- coding: utf-8 -*-
"""
An example that demonstrates voice conversation.
"""
import os
from examples.voice_conversation._audio_assistant_agent import (
    AudioAssistantAgent,
)
from examples.voice_conversation._audio_user_agent import AudioUserAgent


async def main() -> None:
    """The main entry point for the realtime voice conversation."""
    user_agent = AudioUserAgent(name="user")
    assistant_agent = AudioAssistantAgent(
        name="assistant",
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
