# -*- coding: utf-8 -*-
"""Groq-powered agent example.

Demonstrates four capabilities of GroqChatModel in AgentScope:
  1. Basic single-turn chat
  2. Streaming output
  3. Tool use with a ReAct agent
  4. Structured (JSON) output

Set your Groq API key before running:
    export GROQ_API_KEY="your-key-here"

Install the groq SDK if you haven't already:
    pip install "agentscope[groq]"
"""
import asyncio
import json
import os

from pydantic import BaseModel, Field

from agentscope.agent import ReActAgent, UserAgent
from agentscope.formatter import GroqChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.message import Msg
from agentscope.model import GroqChatModel
from agentscope.tool import (
    Toolkit,
    execute_shell_command,
    execute_python_code,
    view_text_file,
)

# ---------------------------------------------------------------------------
# Pydantic schema used in the structured-output demo
# ---------------------------------------------------------------------------

GROQ_API_KEY = os.environ.get("GROQ_API_KEY")


class PersonInfo(BaseModel):
    """Structured output schema for a person's profile."""

    name: str = Field(description="Full name of the person")
    birth_year: int = Field(description="Year the person was born")
    nationality: str = Field(description="Nationality of the person")
    key_contributions: list[str] = Field(
        description="Up to three key contributions or achievements",
    )


# ---------------------------------------------------------------------------
# 1. Basic single-turn chat
# ---------------------------------------------------------------------------


async def demo_basic_chat() -> None:
    """Send a single message and print the text reply."""
    print("\n" + "=" * 60)
    print("DEMO 1 — Basic single-turn chat")
    print("=" * 60)

    model = GroqChatModel(
        model_name="llama-3.3-70b-versatile",
        api_key=GROQ_API_KEY,
        stream=False,
    )

    response = await model(
        [{"role": "user", "content": "In one sentence, what is AgentScope?"}],
    )

    print("Response:", response.content[0]["text"])
    if response.usage:
        print(
            f"Tokens used — input: {response.usage.input_tokens}, "
            f"output: {response.usage.output_tokens}",
        )


# ---------------------------------------------------------------------------
# 2. Streaming output
# ---------------------------------------------------------------------------


async def demo_streaming() -> None:
    """Stream a reply token-by-token and print each chunk on the same line."""
    print("\n" + "=" * 60)
    print("DEMO 2 — Streaming output")
    print("=" * 60)

    model = GroqChatModel(
        model_name="llama-3.3-70b-versatile",
        api_key=GROQ_API_KEY,
        stream=True,
    )

    stream = await model(
        [
            {
                "role": "user",
                "content": "Count from 1 to 5, one number per line.",
            },
        ],
    )

    print("Streaming response:")
    last_len = 0
    async for chunk in stream:
        if chunk.content:
            text = chunk.content[0].get("text", "")
            # Print only the new characters since the last chunk
            print(text[last_len:], end="", flush=True)
            last_len = len(text)
    print()  # newline after stream ends


# ---------------------------------------------------------------------------
# 3. ReAct agent with tool use (interactive chatbot)
# ---------------------------------------------------------------------------


async def demo_react_agent() -> None:
    """Run an interactive ReAct agent loop backed by Groq."""
    print("\n" + "=" * 60)
    print("DEMO 3 — ReAct agent with tools  (type 'exit' to quit)")
    print("=" * 60)

    toolkit = Toolkit()
    toolkit.register_tool_function(execute_shell_command)
    toolkit.register_tool_function(execute_python_code)
    toolkit.register_tool_function(view_text_file)

    agent = ReActAgent(
        name="Groq",
        sys_prompt="You are a helpful AI assistant powered by Groq.",
        model=GroqChatModel(
            model_name="llama-3.3-70b-versatile",
            api_key=GROQ_API_KEY,
            stream=True,
        ),
        formatter=GroqChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    user = UserAgent("User")

    msg = None
    while True:
        msg = await user(msg)
        if msg.get_text_content().strip().lower() == "exit":
            print("Exiting agent loop.")
            break
        msg = await agent(msg)


# ---------------------------------------------------------------------------
# 4. Structured output
# ---------------------------------------------------------------------------


async def demo_structured_output() -> None:
    """Ask the agent to return a structured JSON profile."""
    print("\n" + "=" * 60)
    print("DEMO 4 — Structured output")
    print("=" * 60)

    toolkit = Toolkit()
    agent = ReActAgent(
        name="Groq",
        sys_prompt="You are a helpful AI assistant powered by Groq.",
        model=GroqChatModel(
            model_name="llama-3.3-70b-versatile",
            api_key=GROQ_API_KEY,
            stream=False,
        ),
        formatter=GroqChatFormatter(),
        toolkit=toolkit,
        memory=InMemoryMemory(),
    )

    msg = Msg("user", "Give me a profile of Marie Curie.", "user")
    response = await agent(msg, structured_model=PersonInfo)

    print("Structured output (metadata):")
    print(json.dumps(response.metadata, indent=2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def main() -> None:
    """Run all four demos in sequence."""
    if not GROQ_API_KEY:
        print(
            "Warning: GROQ_API_KEY is not set. "
            "Demos will fail when contacting the Groq API.",
        )

    await demo_basic_chat()
    await demo_streaming()
    await demo_structured_output()

    # Interactive demo last — runs until the user types 'exit'
    await demo_react_agent()


if __name__ == "__main__":
    asyncio.run(main())
