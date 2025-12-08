# -*- coding: utf-8 -*-
"""
.. _rag:

RAG
===========================

AgentScope provides built-in support for Retrieval-Augmented Generation (RAG)
tasks. This tutorial demonstrates

- how to use the RAG module in AgentScope,
- how to use **multimodal** RAG,
- how to integrate the RAG module with the ``ReActAgent`` in
    - **agentic manner** and
    - **generic manner**:

.. list-table:: RAG module integration methods
    :header-rows: 1

    * - Integration Manner
      - Description
      - Advantages
      - Disadvantages
    * - Agentic Manner
      - The RAG module is integrated with the agent as a tool, and the agent can decide when to retrieve knowledge and the queries to be retrieved.
      - - The query rewriting and knowledge retrieval are integrated into the ReAct process, which is more flexible,
        - the agent can rewrite the query based on all the available information,
        - only retrieve knowledge when necessary.
      - High requirements for the LLM's reasoning and tool-use capabilities.
    * - Generic Manner
      - Retrieve knowledge at the beginning of each reply, and attach the retrieved knowledge to the prompt in a user message.
      - - Simple, easy to implement,
        - does not require high reasoning and tool-use capabilities from the LLM.
      - - Still retrieve knowledge even when not necessary, and
        - if the retrieval is imperceptible to the user, the waiting time may be longer.

.. note:: As an open-source project, AgentScope doesn't insist that developers
 use the built-in RAG module. Our target is make the development easier and
 more enjoyable, so integrating other RAG implementations, frameworks, or
 services are welcome and encouraged!

"""
import asyncio
import json
import os

from matplotlib import pyplot as plt

import agentscope
from agentscope.agent import ReActAgent
from agentscope.embedding import (
    DashScopeTextEmbedding,
    DashScopeMultiModalEmbedding,
)
from agentscope.formatter import DashScopeChatFormatter
from agentscope.message import Msg
from agentscope.model import DashScopeChatModel
from agentscope.rag import (
    TextReader,
    SimpleKnowledge,
    QdrantStore,
    Document,
    ImageReader,
)
from agentscope.tool import Toolkit

# %%
# Using RAG Module
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# The RAG module in AgentScope is composed of two core components:
#
# - **Reader**: responsible for reading and chunking the input documents.
# - **Knowledge**: responsible for algorithm implementation of knowledge retrieval and updating.
#
# .. note:: We're integrating more vector databases and readers into AgentScope. Contributions are welcome!
#
# The currently built-in readers include:
#

for _ in agentscope.rag.__all__:
    if _.endswith("Reader"):
        print(f"- {_}")

# %%
# they are responsible for reading data and chunking them into ``Document`` objects.
# The ``Document`` class has the following fields:
#
# - ``metadata``: the metadata of the document, including the content, doc_id, chunk_id, and total_chunks.
# - ``embedding``: the embedding vector of the document, which will be filled when the document is added to or retrieved from the knowledge base.
# - ``score``: the relevance score of the document, which will be filled when the document is retrieved from the knowledge base.
#
# Taking the ``TextReader`` as an example, it can read and chunk documents from text strings.
#


async def example_text_reader(print_docs: bool) -> list[Document]:
    """The example of using TextReader."""
    # Create a text reader with chunk size of 512 characters, split by characters
    reader = TextReader(chunk_size=512, split_by="paragraph")

    # Read documents from a text string
    documents = await reader(
        text=(
            # Fake personal profile for demonstration
            "I'm John Doe, 28 years old.\n"
            "I live in San Francisco. I work at OpenAI as a "
            "software engineer. I love hiking and photography.\n"
            "My father is Michael Doe, a doctor. I'm very proud of him. "
            "My mother is Sarah Doe, a teacher. She is very kind and "
            "always helps me with my studies.\n"
            "I'm now a PhD student at Stanford University, majoring in "
            "Computer Science. My advisor is Prof. Jane Williams, who is "
            "a leading expert in artificial intelligence. I have published "
            "several papers in top conferences, such as NeurIPS and ICML.\n"
            "My best friend is James Smith.\n"
        ),
    )

    if print_docs:
        print("The length of the documents:", len(documents))
        for idx, doc in enumerate(documents):
            print("Document #", idx)
            print("\tScore: ", doc.score)
            print("\tMetadata: ", json.dumps(doc.metadata, indent=2), "\n")

    return documents


docs = asyncio.run(example_text_reader(print_docs=True))

# %%
# Note there doesn't exist a universally best chunk size and splitting method, especially for PDF files, we highly
# encourage developers to implement or contribute their own readers according to their specific scenarios.
# To create a custom reader, you only need to inherit the ``ReaderBase`` class and implement the ``__call__`` method.
#
# After chunking the documents, we can create a knowledge base to store the documents and perform retrieval.
# Such a knowledge base is initialized by providing **an embedding model** and **an embedding store** (also known as a vector database).
# Agentscope provides built-in support for `Qdrant <https://qdrant.tech/>`_ as the embedding store and a simple knowledge base implementation ``SimpleKnowledge``.
# They can be used as follows:
#
# .. note::
#
#  - We're integrating more vector databases into AgentScope. Contributions are welcome!
#  - The Qdrant store supports various storage backends by the ``location`` parameter, including in-memory, local file, and remote server. Refer to the `Qdrant documentation <https://qdrant.tech/>`_ for more details.
#


async def build_knowledge_base() -> SimpleKnowledge:
    """Build a knowledge base with sample documents."""
    # Read documents using the text reader
    documents = await example_text_reader(print_docs=False)

    # Create an in-memory knowledge base instance
    knowledge = SimpleKnowledge(
        # Choose an embedding model to convert text to embedding vectors
        embedding_model=DashScopeTextEmbedding(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="text-embedding-v4",
            dimensions=1024,
        ),
        # Choose Qdrant as the embedding store
        embedding_store=QdrantStore(
            location=":memory:",  # Use in-memory storage for demonstration
            collection_name="test_collection",
            dimensions=1024,  # The dimension of the embedding vectors
        ),
    )

    # Insert documents into the knowledge base
    await knowledge.add_documents(documents)

    # Retrieve relevant documents based on a given query
    docs = await knowledge.retrieve(
        query="Who is John Doe's father?",
        limit=3,
        score_threshold=0.5,
    )

    print("Retrieved Documents:")
    for doc in docs:
        print(doc, "\n")

    return knowledge


knowledge = asyncio.run(build_knowledge_base())

# %%
# The knowledge base class provides two main methods: ``add_documents`` and
# ``retrieve``, which are used to add documents to the knowledge base and
# retrieve relevant documents based on a given query, respectively.
#
# In addition, the knowledge base class also provides a convenient method
# ``retrieve_knowledge``, which wraps the ``retrieve`` method into a tool
# function that can be directly registered in the toolkit of an agent.
#
#
# Customizing RAG Components
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# AgentScope supports and encourages developers to customize their own RAG components, including readers, knowledge bases and embedding stores.
# Specifically, we provide the following base classes for customization:
#
# .. list-table:: RAG Base Classes
#     :header-rows: 1
#
#     * - Base Class
#       - Description
#       - Abstract Methods
#     * - ``ReaderBase``
#       - The base class for all readers.
#       - ``__call__``
#     * - ``VDBStoreBase``
#       - The base class for embedding stores (vector databases).
#       - | ``add``
#         | ``search``
#         | ``get_client`` (optional)
#         | ``delete`` (optional)
#     * - ``KnowledgeBase``
#       - The base class for knowledge bases.
#       - | ``retrieve``
#         | ``add_documents``
#
#
# The `get_client` method in the ``VDBStoreBase`` allows developers to access the full functionality of the underlying vector database.
# So that they can implement more advanced features based on the vector database, e.g. index management, advanced search, etc.
#
# Integrating with ReActAgent
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# Next we demonstrate how to integrate the RAG module with the ``ReActAgent``
# class in AgentScope in agentic and generic manners.
#
# Agentic Manner
# --------------------------------
# In agentic manner, the ReAct agent is empowered with the ability to decide when to retrieve knowledge and the queries to be retrieved.
# It's very easy to integrate the RAG module with the ``ReActAgent`` class in AgentScope, just by registering the ``retrieve_knowledge`` method of the knowledge base as a tool,
# and providing a proper description for the tool.


async def example_agentic_manner() -> None:
    """The example of integrating RAG module with ReActAgent in agentic manner."""
    # Create a ReAct agent
    toolkit = Toolkit()

    # Create the ReAct agent with DashScope as the model
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen-max",
        ),
        formatter=DashScopeChatFormatter(),
        toolkit=toolkit,
    )

    print("The first response: ")
    # Ask some questions about Tony Stank
    await agent(
        Msg(
            "user",
            "John Doe is my best friend.",
            "user",
        ),
    )

    # Register the retrieve_knowledge method as a tool function in the toolkit
    toolkit.register_tool_function(
        knowledge.retrieve_knowledge,
        func_description=(  # Provide a clear description for the tool
            "The tool used to retrieve documents relevant to the given query. "
            "Use this tool when you need to find some information about John Doe."
        ),
    )

    print("\n\nThe second response: ")
    # We hope the agent can rewrite the query to be more specific, e.g.
    # "Who is Tony Stank's father?" or "Tony Stank's father"
    await agent(
        Msg(
            "user",
            "Do you know who his father is?",
            "user",
        ),
    )


asyncio.run(example_agentic_manner())

# %%
# In the above example, our question is "Do you know who his father is?".
# We hope the agent can rewrite the query with the historical information, and
# rewrite it to be more specific, e.g. "Who is John Doe's father?" or "John Doe's father".
#
#
# Generic Manner
# --------------------------------
# The ``ReActAgent`` also integrates the RAG module in a generic manner, which
# retrieves knowledge at the beginning of each reply, and attaches the
# retrieved knowledge to the prompt in a user message.
#
# Just set the ``knowledge`` parameter of the ``ReActAgent``, and the agent
# will automatically retrieve knowledge at the beginning of each reply.
#


async def example_generic_manner() -> None:
    """The example of integrating RAG module with ReActAgent in generic manner."""
    # Create a ReAct agent
    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen-max",
        ),
        formatter=DashScopeChatFormatter(),
        #
        knowledge=knowledge,
    )

    await agent(
        Msg(
            "user",
            "Do you know who John Doe's father is?",
            "user",
        ),
    )

    print("Take a look at the agent's memory:")
    content = (await agent.memory.get_memory())[1].content
    print(json.dumps(content, indent=2, ensure_ascii=False))


asyncio.run(example_generic_manner())


# %%
# Multimodal RAG
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# The RAG module in AgentScope supports multimodal RAG natively, as
#
# - AgentScope supports multimodal embedding API, e.g. ``DashScopeMultimodalEmbedding``.
# - The ``Document`` class supports text, image, and other modalities in its ``metadata`` field.
#
# Thus, we can directly use the multimodal reader and embedding model to build
# a multimodal knowledge base as follows.
#
# First we prepare an image with some text about my name.

# Prepare an image with the text "John Doe's father is Michael Doe."
path_image = "./example.jpg"
plt.figure(figsize=(8, 3))
plt.text(
    0.5,
    0.5,
    "My name is Tony Stank",
    ha="center",
    va="center",
    fontsize=30,
)
plt.axis("off")
plt.savefig(path_image, bbox_inches="tight", pad_inches=0.1)
plt.close()

# %%
# Then we can build a multimodal knowledge base with the image document.
# The example is the same as before, just using the ``ImageReader`` and
# ``DashScopeMultiModalEmbedding`` instead of the text counterparts.
#


async def example_multimodal_rag() -> None:
    """The example of using multimodal RAG."""
    # Read the image using the ImageReader
    reader = ImageReader()
    docs = await reader(image_url=path_image)

    # Create a knowledge base with the new image document
    knowledge = SimpleKnowledge(
        embedding_model=DashScopeMultiModalEmbedding(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="multimodal-embedding-v1",
            dimensions=1024,
        ),
        embedding_store=QdrantStore(
            location=":memory:",
            collection_name="test_collection",
            dimensions=1024,
        ),
    )

    await knowledge.add_documents(docs)

    agent = ReActAgent(
        name="Friday",
        sys_prompt="You are a helpful assistant named Friday.",
        model=DashScopeChatModel(
            api_key=os.environ["DASHSCOPE_API_KEY"],
            model_name="qwen-vl-max",
        ),
        formatter=DashScopeChatFormatter(),
        knowledge=knowledge,
    )

    await agent(
        Msg(
            "user",
            "What's my name?",
            "user",
        ),
    )

    # Let's see the last message from the agent
    print("\nThe image is attached in the agent's memory:")
    print((await agent.memory.get_memory())[1])


asyncio.run(example_multimodal_rag())

# %%
# We can see that the agent can answer the question based on the retrieved
# image.
