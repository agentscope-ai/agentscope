# -*- coding: utf-8 -*-
"""The agentic usage example for RAG in AgentScope, where the agent is
equipped with RAG tools to answer questions based on a knowledge base.

The example is more challenging for the agent, requiring the agent to
adjust the retrieval parameters to get relevant results.
"""
import asyncio
import os

from agentscope.agent import ReActAgent, UserAgent
from agentscope.embedding import DashScopeTextEmbedding
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg, TextBlock
from agentscope.model import DashScopeChatModel
from agentscope.rag import SimpleKnowledge, QdrantStore, TextReader
from agentscope.tool import Toolkit, ToolResponse

# Create a knowledge base instance
knowledge = SimpleKnowledge(
    embedding_store=QdrantStore(
        location=":memory:",
        collection_name="test_collection",
        dimensions=1024,  # The dimension of the embedding vectors
    ),
    embedding_model=DashScopeTextEmbedding(
        api_key=os.environ["DASHSCOPE_API_KEY"],
        model_name="text-embedding-v4",
    ),
)


# An agent-oriented tool function to retrieve knowledge from the knowledge base
# We expose the `limit` and `score_threshold` parameters to the agent
# for more flexible control
async def retrieve_knowledge(
    query: str,
    limit: int = 3,
    score_threshold: float | None = 0.8,
) -> ToolResponse:
    """Retrieve relevant documents from the knowledge base, which is relevant
    to John Doe's profile. Note the `query` parameter is
    very important for the retrieval quality, and you can try many different
    queries to get the best results. Adjust the `limit` and `score_threshold`
    parameters to get more or fewer results.

    Args:
        query (`str`):
            The query string, which should be specific and concise. For
            example, you should provide the specific name instead of
            "you", "my", "he", "she", etc.
        limit (`int`, defaults to 3):
            The number of relevant documents to retrieve.
        score_threshold (`float`, defaults to 0.8):
            A threshold in [0, 1] and only the relevance score above this
            threshold will be returned. Reduce this value to get more results.
    """
    # Retrieve relevant documents based on the given query
    docs = await knowledge.retrieve(
        query=query,
        limit=limit,
        score_threshold=score_threshold,
    )
    if len(docs):
        return ToolResponse(
            content=[
                TextBlock(
                    type="text",
                    text=f"Score: {_.score}, "
                    f"Content: {_.metadata.content['text']}",
                )
                for _ in docs
            ],
        )
    return ToolResponse(
        content=[
            TextBlock(
                type="text",
                text="No relevant documents found. TRY to reduce the "
                "`score_threshold` parameter to get "
                "more results.",
            ),
        ],
    )


async def main() -> None:
    """The main entry of the agent usage example for RAG in AgentScope."""

    # Store some things into the knowledge base for demonstration
    # In practice, the VDB store would be pre-filled with relevant data
    reader = TextReader(chunk_size=1024, split_by="sentence")
    documents = await reader(
        text=(
            # Fake personal profile for demonstration
            "I'm John Doe, 28 years old. My best friend is James "
            "Smith. I live in San Francisco. I work at OpenAI as a "
            "software engineer. I love hiking and photography. "
            "My father is Michael Doe, a doctor. I'm very proud of him. "
            "My mother is Sarah Doe, a teacher. She is very kind and "
            "always helps me with my studies.\n"
            "I'm now a PhD student at Stanford University, majoring in "
            "Computer Science. My advisor is Prof. Jane Williams, who is "
            "a leading expert in artificial intelligence. I have published "
            "several papers in top conferences, such as NeurIPS and ICML. "
        ),
    )
    await knowledge.add_documents(documents)

    # Create a toolkit and register the RAG tool function
    toolkit = Toolkit()
    toolkit.register_tool_function(retrieve_knowledge)

    # Create an agent and a user
    agent = ReActAgent(
        name="Friday",
        sys_prompt=(
            "You're a helpful assistant named Friday. "
            "You're equipped with a 'retrieve_knowledge' tool to help you "
            "know about the user named John Doe. "
            "NOTE to adjust the `score_threshold` parameters when you cannot "
            "get relevant results. "
        ),
        toolkit=toolkit,
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen3-max-preview",
        ),
        formatter=DashScopeChatFormatter(),
    )
    user = UserAgent(name="User")

    # A simple conversation loop beginning with a preset question
    msg = Msg(
        "user",
        "I'm John Doe. Do you know my father?",
        "user",
    )
    while True:
        msg = await agent(msg)
        msg = await user(msg)
        if msg.get_text_content() == "exit":
            break


asyncio.run(main())
