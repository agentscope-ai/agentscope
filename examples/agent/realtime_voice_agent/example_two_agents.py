# -*- coding: utf-8 -*-
"""Two-agent conversation example using VoiceMsgHub.

Demonstrates VoiceMsgHub usage, similar to MsgHub:
- Agents don't need to know about MsgStream existence
- Participants are managed through VoiceMsgHub
"""

import asyncio
import os


from agentscope.agent.realtime_voice_agent import (
    DashScopeVoiceModel,
    VoiceAgent,
    VoiceMsgHub,
)


async def main() -> None:
    """Run the two-agent voice conversation demo.

    This function demonstrates a conversation between two AI agents using
    VoiceMsgHub for participant management. Each agent has a distinct
    personality and voice, and they engage in a multi-turn dialogue.

    Environment:
        Requires DASHSCOPE_API_KEY environment variable to be set.

    Raises:
        `KeyboardInterrupt`:
            When user presses Ctrl+C to interrupt.
        `Exception`:
            Any other errors during the conversation.
    """

    # Create models (turn_detection=False for agent-agent dialogue)
    model_alice = DashScopeVoiceModel(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        voice="Cherry",
        enable_turn_detection=False,
    )
    model_bob = DashScopeVoiceModel(
        api_key=os.getenv("DASHSCOPE_API_KEY"),
        voice="Ethan",
        enable_turn_detection=False,
    )

    # Create agents (no need to pass msg_stream)
    alice = VoiceAgent(
        name="Alice",
        model=model_alice,
        sys_prompt="You are Alice, lively and cheerful. Keep each response "
        "under two sentences.",
    )

    bob = VoiceAgent(
        name="Bob",
        model=model_bob,
        sys_prompt="You are Bob, humorous and witty. Keep each response "
        "under two sentences.",
    )

    print("\n" + "=" * 60)
    print("Two-Agent Conversation Example Using VoiceMsgHub")
    print("=" * 60 + "\n")

    # Use VoiceMsgHub to manage participants
    async with VoiceMsgHub(participants=[alice, bob]):
        try:
            # Bob starts listening first
            bob_task = asyncio.create_task(
                bob.listen_and_reply_loop(max_turns=3),
            )
            await bob.wait_listening()
            print("Bob is now listening...\n")

            # Alice initiates the conversation with text
            await alice.say("Hello! The weather is so nice today!")

            # Alice also starts listening
            alice_task = asyncio.create_task(
                alice.listen_and_reply_loop(max_turns=2),
            )

            # Wait for conversation to complete
            await asyncio.gather(bob_task, alice_task)

        except KeyboardInterrupt:
            print("\nUser interrupted")
        except Exception as e:
            print(f"\nError: {e}")
            import traceback

            traceback.print_exc()

    print("\n" + "=" * 60)
    print("Conversation ended")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
