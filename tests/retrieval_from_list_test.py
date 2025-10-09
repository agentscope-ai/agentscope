# -*- coding: utf-8 -*-
""" Python web search test."""
from datetime import datetime
import unittest
from typing import Any

from agentscope.service import retrieve_from_list, cos_sim
from agentscope.service.service_status import ServiceExecStatus
from agentscope.message import Msg
from agentscope.memory.temporary_memory import TemporaryMemory
from agentscope.model import OpenAIEmbeddingWrapper, ModelResponse


class TestRetrieval(unittest.TestCase):
    """TestMemRetrieval for memory retrieval unit test."""

    def test_retrieval_from_list(self) -> None:
        """test memory retrieval"""

        class DummyModel(OpenAIEmbeddingWrapper):
            """
            Dummy model wrapper for testing
            """

            def __init__(self) -> None:
                pass

            def __call__(self, *args: Any, **kwargs: Any) -> ModelResponse:
                print(*args, **kwargs)
                return ModelResponse(raw={})

        dummy_model = DummyModel()

        query = Msg(
            name="Lora",
            content="test query",
            role="assistant",
            metadata=[0, 1],
        )
        query.timestamp = "2023-12-18 21:40:59"
        m1 = Msg(name="env", content="test", role="assistant", metadata=[1, 0])
        m1.timestamp = "2023-12-18 21:45:59"
        m2 = Msg(
            name="env",
            content="test2",
            role="assistant",
            metadata=[0.5, 0.5],
        )
        m2.timestamp = "2023-12-18 21:50:59"
        memory = TemporaryMemory(embedding_model=dummy_model)
        memory.add(m1)
        memory.add(m2)

        def score_func(m1: Msg, m2: Msg) -> float:
            relevance = cos_sim(m1.metadata, m2.metadata).content
            time_gap = (
                datetime.strptime(m1.timestamp, "%Y-%m-%d %H:%M:%S")
                - datetime.strptime(m2.timestamp, "%Y-%m-%d %H:%M:%S")
            ).total_seconds() / 60
            recency = 0.99**time_gap
            return recency + relevance

        retrieved = retrieve_from_list(
            query,
            list(memory.get_memory()),
            score_func,
            embedding_model=dummy_model,
            preserve_order=False,
        )
        self.assertEqual(retrieved.status, ServiceExecStatus.SUCCESS)
        self.assertEqual(retrieved.content[0][2], m2)
        self.assertEqual(retrieved.content[1][2], m1)

        retrieved = retrieve_from_list(
            query,
            list(memory.get_memory(recent_n=2)),
            score_func,
            top_k=2,
            embedding_model=None,
            preserve_order=True,
        )
        self.assertEqual(retrieved.status, ServiceExecStatus.SUCCESS)
        self.assertEqual(retrieved.content[0][2], m1)


# This allows the tests to be run from the command line
if __name__ == "__main__":
    unittest.main()
