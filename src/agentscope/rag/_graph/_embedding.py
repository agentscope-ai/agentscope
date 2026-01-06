# -*- coding: utf-8 -*-
"""Embedding and LLM call utilities for graph knowledge base."""

from ...embedding import EmbeddingModelBase
from ...model import ChatModelBase
from ..._logging import logger
from .._reader import Document
from ._types import Entity


def _clean_llm_json_response(response_text: str) -> str:
    """Clean LLM JSON response by removing markdown code block markers.

    Args:
        response_text: Raw LLM response text

    Returns:
        Cleaned JSON string
    """
    json_text = response_text.strip()
    if json_text.startswith("```json"):
        json_text = json_text[7:]
    if json_text.startswith("```"):
        json_text = json_text[3:]
    if json_text.endswith("```"):
        json_text = json_text[:-3]
    return json_text.strip()


class GraphEmbedding:
    """Embedding and LLM call utilities.

    This class provides methods for:
    - Generating embeddings for documents, queries, and entities
    - Calling LLM for text generation tasks
    """

    # pylint: disable=too-few-public-methods

    embedding_model: "EmbeddingModelBase"
    llm_model: "ChatModelBase | None"
    entity_extraction_config: dict

    async def _embed_documents(
        self,
        documents: list[Document],
    ) -> list[Document]:
        """Generate embeddings for documents.

        This method generates vector embeddings ONLY for document content,
        following the "content embedding" principle.

        Args:
            documents: List of documents without embeddings

        Returns:
            List of documents with embeddings
        """
        # Extract text content
        texts = [doc.get_text() for doc in documents]

        # Generate embeddings in batches
        response = await self.embedding_model(texts)

        # Attach embeddings to documents
        for doc, embedding in zip(documents, response.embeddings):
            doc.embedding = embedding

        return documents

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM and return text response.

        Args:
            prompt: Prompt text

        Returns:
            Response text content

        Raises:
            ValueError: If llm_model is None
        """
        if self.llm_model is None:
            raise ValueError("llm_model is required for this operation")

        # Call LLM with proper message format
        response = await self.llm_model(
            messages=[{"role": "user", "content": prompt}],
            stream=False,
        )

        # Extract text from response
        if response.content:
            text = response.content[0].get("text", "")
            return str(text) if text is not None else ""
        return ""

    async def _embed_query(self, query: str) -> list[float]:
        """Generate embedding for query string.

        Args:
            query: Query string

        Returns:
            Query embedding vector
        """
        response = await self.embedding_model([query])
        return response.embeddings[0]

    async def _embed_entities(self, entities: list[Entity]) -> list[Entity]:
        """Generate embeddings for entities.

        Following the "content embedding" principle, entity embeddings
        contain ONLY entity content (name + type + description), NOT
        relationship information.

        Args:
            entities: List of entities without embeddings

        Returns:
            List of entities with embeddings
        """
        if not self.entity_extraction_config.get(
            "generate_entity_embeddings",
            True,
        ):
            return entities

        # Create text representation: "name (type): description"
        texts = [
            f"{entity.name} ({entity.type}): {entity.description}"
            for entity in entities
        ]

        # Generate embeddings
        response = await self.embedding_model(texts)

        # Attach embeddings to entities
        for entity, embedding in zip(entities, response.embeddings):
            entity.embedding = embedding

        return entities
