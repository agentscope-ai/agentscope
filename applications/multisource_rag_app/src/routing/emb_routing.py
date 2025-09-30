# -*- coding: utf-8 -*-
"""
Embedding-based routing function
"""
from typing import List, Optional

from loguru import logger

from agentscope.agent import AgentBase
from agentscope.agent import LlamaIndexAgent
from agentscope.manager import ModelManager
from agentscope.message import Msg


def cluster_similarity_planning(
    query: Msg,
    rag_agents: List[LlamaIndexAgent],
    backup_agent: Optional[AgentBase] = None,
    top_k_clusters: int = 5,
    max_agents: int = 2,
) -> List[AgentBase]:
    """
    Routing based on embedding similarity
    Args:
        query: (`Msg`):
            Input query for routing.
        rag_agents (`List[LlamaIndexAgent]`):
            List of available retrieval agent.
        backup_agent (`AgentBase`, optional):
            Default agent to select when non of the retrieval agent
            is appropriate.
        top_k_clusters (`int`):
            The number of closest cluster to consider.
        max_agents (`int`):
            The maximum number of retrieval agent to select.

    Returns:
        `List[AgentBase]`: selected agent.
    """
    logger.info("Planning based on cluster similarity starts")

    # At least one of the cluster should have similiarity at least
    # IRRELEVANT_MAX_THRESHOLD
    # otherwise, only backup agent will be used
    IRRELEVANT_MAX_THRESHOLD = 0.35
    # only clusters with at least MIN_THRESHOLD will be considered
    MIN_THRESHOLD = 0.3

    # STEP 1: retrieve cluster-level similarities from rag agent
    model_manager = ModelManager.get_instance()
    emb_model = model_manager.get_model_by_config_name("qwen_emb_config")
    query_embedding = emb_model(query.content).embedding[0]
    agent_similarities = []
    for agent in rag_agents:
        cluster_similarities = agent.get_cluster_similarites(
            query_embedding,
            emb_model,
            0.3,
        )
        agent_similarities += [(agent.name, s) for s in cluster_similarities]

    agent_similarities = sorted(
        agent_similarities,
        key=lambda x: x[1],
        reverse=True,
    )
    # return [backup_agent]

    # STEP 2: check whether the query is irrelevant with LLM
    if agent_similarities[0][1] < IRRELEVANT_MAX_THRESHOLD:
        logger.info(f"low similarity: {agent_similarities[:5]}")
        logger.info(f"handling irrelevant query: {query.content}")
        return [backup_agent]

    # STEP 3: return agent with clusters of top_k_clusters similarities
    call_agent_names = set()
    for name, score in agent_similarities[:top_k_clusters]:
        if len(call_agent_names) < max_agents and score > MIN_THRESHOLD:
            call_agent_names.add(name)

    rag_agent_mapping = {agent.name: agent for agent in rag_agents}
    call_agents = [rag_agent_mapping[name] for name in call_agent_names]
    logger.info(
        f"Planning based on cluster similarity ends, call {call_agent_names}",
    )
    logger.info(f"top k clusters: {agent_similarities[:top_k_clusters]}")

    return call_agents
