# -*- coding: utf-8 -*-
"""User-Agent conversation example using VoiceMsgHub.

Demonstrates how VoiceMsgHub simplifies setting up conversations between
users and agents.
"""

import asyncio
import os


from agentscope.agent.realtime_voice_agent import (
    DashScopeVoiceModel,
    VoiceAgent,
    RealtimeVoiceInput,
    VoiceMsgHub,
)


async def main() -> None:
    """Run the main user-agent voice conversation demo.

    This function sets up a real-time voice conversation between a user
    and an AI agent using VoiceMsgHub to manage the participants and
    message flow.

    The conversation supports:
    - Automatic voice activity detection (VAD)
    - Speech interruption (user can interrupt agent's response)
    - Continuous dialogue until interrupted

    Raises:
        `KeyboardInterrupt`:
            When user presses Ctrl+C to exit.
        `Exception`:
            Any other errors during conversation.
    """

    # Create model with automatic VAD enabled
    model = DashScopeVoiceModel(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        voice="Cherry",
        enable_turn_detection=True,
    )

    # Create VoiceInput and Agent (no need to pass msg_stream)
    voice_input = RealtimeVoiceInput()
    agent = VoiceAgent(
        name="assistant",
        model=model,
        sys_prompt="You are a friendly assistant. Please answer questions "
        "concisely.",
    )

    print("\n" + "=" * 60)
    print("User-Agent Conversation Example Using VoiceMsgHub")
    print("=" * 60 + "\n")

    # Use VoiceMsgHub to manage participants
    async with VoiceMsgHub(participants=[voice_input, agent]):
        try:
            # Start voice input
            await voice_input.start()

            print("Real-time voice conversation started")
            print(
                "- Speak directly, the model will automatically detect "
                "speech end",
            )
            print(
                "- Supports interruption: Speaking will stop agent's audio "
                "output",
            )
            print("- Press Ctrl+C to exit")
            print()

            # Continuous conversation loop
            while True:
                await agent.reply()

        except KeyboardInterrupt:
            print("\n\nUser interrupted")
        except Exception as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()

    print("\nConversation ended")


if __name__ == "__main__":
    asyncio.run(main())
