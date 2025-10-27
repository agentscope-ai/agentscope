# -*- coding: utf-8 -*-
"""The main entry point of the Qwen Deep Research agent example."""
import asyncio

from qwen_deep_research_agent import QwenDeepResearchAgent
from agentscope import logger
from agentscope.message import Msg


async def main() -> None:
    """The main entry point for the Qwen Deep Research agent example."""
    # Create DeepResearch Agent
    researcher = QwenDeepResearchAgent(
        name="Researcher Qwen",
        verbose=True,
    )

    # Step 1: Model follow-up question for confirmation
    # The model analyzes the user's question
    # and asks follow-up questions to clarify the research direction.
    user_msg = Msg(
        name="User",
        content="Research the applications of artificial intelligence in "
        "education",
        role="user",
    )

    clarification = await researcher(user_msg)
    print(f"\n{clarification.name}: {clarification.content}\n")

    # Step 2: Deep research
    # Based on the content of the follow-up question in Step 1,
    # the model executes the complete research process.
    user_response = Msg(
        name="User",
        content="I am mainly interested in personalized learning and "
        "intelligent assessment.",
        role="user",
    )

    research_result = await researcher(user_response)
    print(f"\n{research_result.name}: {research_result.content}\n")

    print("\n✅ Research complete!\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.exception(e)
