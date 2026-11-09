"""KnowledgeBase.search rejects a non-positive ``top_k`` instead of slicing from the end."""

from unittest.mock import MagicMock

import pytest

from agentscope.rag import KnowledgeBase


def _kb() -> KnowledgeBase:
    return KnowledgeBase(
        name='kb',
        description='test',
        embedding_model=MagicMock(),
        vector_store=MagicMock(),
        collection='kb',
    )


@pytest.mark.asyncio
@pytest.mark.parametrize('top_k', [0, -1, -3])
async def test_non_positive_top_k_is_rejected(top_k: int) -> None:
    with pytest.raises(ValueError, match='top_k must be positive'):
        await _kb().search(['question'], top_k=top_k)
