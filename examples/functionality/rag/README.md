# RAG in AgentScope

This example includes three scripts to demonstrate how to use Retrieval-Augmented Generation (RAG) in AgentScope:

- the basic usage of RAG module in AgentScope in ``basic_usage.py``,
- a simple agentic use case of RAG in ``agentic_usage.py``, and
- integrate RAG into ``ReActAgent`` class by retrieving input message(s) at the beginning of each reply in ``react_agent_integration.py``.

> The agentic usage and static integration has their own advantages and limitations.
>  - The agentic usage requires more powerful LLMs to manage the retrieval process, but it's more flexible and the agent can adjust the retrieval strategy dynamically
>  - The static integration is more straightforward and easier to implement, but it's less flexible and the input message maybe not specific enough, leading to less relevant retrieval results.

