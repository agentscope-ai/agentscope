# RAG in AgentScope

This example includes two scripts to demonstrate how to use Retrieval-Augmented Generation (RAG) in AgentScope:

- the basic usage of RAG module in AgentScope in ``basic_usage.py``
- a simple agentic use case of RAG in ``agentic_usage.py``

We also provide a solution to integrate RAG into the `ReActAgent` class by retrieving relevant documents
at the beginning of each reply in a ``pre_reply`` hook.
Because it's too similar with the long-term memory example, we are considering if integrating the RAG module
into the `ReActAgent` class by supporting a `knowledge` argument in the constructor.
