# -*- coding: utf-8 -*-
"""Relationship extraction for graph knowledge base."""

import asyncio
import json

from ...model import ChatModelBase
from ..._logging import logger
from .._reader import Document
from .._store import GraphStoreBase
from ._embedding import _clean_llm_json_response
from ._types import Entity, Relationship


class GraphRelationship:
    """Relationship extraction functionality.

    This class provides methods for:
    - Extracting relationships between entities
    - Processing entities and relationships together
    - Relationship deduplication
    """

    # pylint: disable=too-few-public-methods

    llm_model: ChatModelBase | None
    graph_store: GraphStoreBase
    enable_relationship_extraction: bool

    async def _process_entities_and_relationships(
        self,
        documents: list[Document],
    ) -> None:
        """Process entities and relationships for documents.

        This is the main entry point for entity/relationship extraction.
        It extracts entities, generates embeddings, extracts relationships,
        and stores everything in the graph database.

        Args:
            documents: List of documents with embeddings
        """
        logger.info("Extracting entities from %s documents", len(documents))

        # Step 1: Extract entities
        entities = await self._extract_entities(documents)

        if not entities:
            logger.warning("No entities extracted from documents")
            return

        logger.info("Extracted %s entities", len(entities))

        # Step 2: Generate entity embeddings
        entities_with_embeddings = await self._embed_entities(entities)

        # Step 3: Store entities in graph database
        for doc in documents:
            # Find entities extracted from this document
            doc_entities = [
                {
                    "name": entity.name,
                    "type": entity.type,
                    "description": entity.description,
                    "embedding": entity.embedding,
                }
                for entity in entities_with_embeddings
                # Note: We need to track which entities came from which
                # document
                # For simplicity, we'll store all entities for now
            ]

            if doc_entities:
                await self.graph_store.add_entities(
                    entities=doc_entities,
                    document_id=doc.id,
                )

        # Step 4: Extract and store relationships if enabled
        if self.enable_relationship_extraction:
            logger.info("Extracting relationships between entities")
            relationships = await self._extract_relationships(
                documents,
                entities_with_embeddings,
            )

            if relationships:
                logger.info("Extracted %s relationships", len(relationships))
                relationships_data = [
                    {
                        "source": rel.source,
                        "target": rel.target,
                        "type": rel.type,
                        "description": rel.description,
                        "strength": rel.strength,
                    }
                    for rel in relationships
                ]
                await self.graph_store.add_relationships(relationships_data)

    async def _extract_relationships(
        self,
        documents: list[Document],
        entities: list[Entity],
    ) -> list[Relationship]:
        """Extract relationships between entities.

        Uses LLM to identify relationships between entities in the documents.

        Args:
            documents: List of documents
            entities: List of extracted entities

        Returns:
            List of relationships
        """
        if not self.llm_model or not entities:
            return []

        # Get entity names for prompt
        entity_names = [entity.name for entity in entities]
        entity_names_str = ", ".join(
            entity_names[:50],
        )

        # Extract relationships from each document concurrently
        max_concurrent = 5
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_from_doc(doc: Document) -> list[Relationship]:
            async with semaphore:
                return await self._extract_relationships_from_text(
                    doc.get_text(),
                    entity_names_str,
                )

        tasks = [extract_from_doc(doc) for doc in documents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect relationships
        all_relationships: list[Relationship] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Relationship extraction failed for document %s: %s",
                    documents[i].id,
                    result,
                )
            elif isinstance(result, list):
                all_relationships.extend(result)

        # Deduplicate relationships
        return self._deduplicate_relationships(all_relationships)

    async def _extract_relationships_from_text(
        self,
        text: str,
        entity_names: str,
    ) -> list[Relationship]:
        """Extract relationships from a single text using LLM.

        Args:
            text: Text content
            entity_names: Comma-separated list of entity names

        Returns:
            List of extracted relationships
        """
        prompt = f"""Extract relationships between entities in the text. \
You must return ONLY a valid JSON array with no additional text.

Text: {text}

Known entities: {entity_names}

Requirements:
1. Extract relationships between entities mentioned in the Known entities \
list
2. Each relationship must have exactly these fields: "source", "target", \
"type", "description"
3. Relationship type should be clear (e.g., WORKS_FOR, LOCATED_IN, \
CREATED, COLLABORATES_WITH)
4. Return ONLY the JSON array, no markdown, no code blocks, no explanations
5. If no relationships, return []

Expected format:
[{{"source": "entity1", "target": "entity2", "type": "WORKS_FOR", \
"description": "entity1 works for entity2"}}]

JSON array:"""

        try:
            response_text = await self._call_llm(prompt)
            if not response_text:
                response_text = "[]"

            # Clean and parse JSON
            json_text = _clean_llm_json_response(response_text)
            rel_data = json.loads(json_text)

            # Validate and create Relationship objects using Pydantic
            relationships = []
            for data in rel_data:
                try:
                    relationship = Relationship(**data)
                    relationships.append(relationship)
                except Exception as e:
                    logger.warning(
                        "Invalid relationship data: %s, error: %s",
                        data,
                        e,
                    )

            return relationships

        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response as JSON: %s", e)
            return []
        except Exception as e:
            logger.error("Relationship extraction failed: %s", e)
            return []

    def _deduplicate_relationships(
        self,
        relationships: list[Relationship],
    ) -> list[Relationship]:
        """Deduplicate relationships.

        Relationships are considered duplicates if they have the same
        source, target, and type (order matters).

        Args:
            relationships: List of relationships (may contain duplicates)

        Returns:
            List of unique relationships
        """
        rel_map: dict[tuple, Relationship] = {}

        for rel in relationships:
            key = (rel.source.lower(), rel.target.lower(), rel.type)

            if key not in rel_map:
                rel_map[key] = rel
            else:
                # Keep the one with longer description
                existing = rel_map[key]
                if len(rel.description) > len(existing.description):
                    rel_map[key] = rel

        deduplicated = list(rel_map.values())
        logger.info(
            "Deduplicated %s relationships to %s unique relationships",
            len(relationships),
            len(deduplicated),
        )

        return deduplicated

    async def _extract_entities(
        self,
        documents: list[Document],
    ) -> list[Entity]:
        """Extract entities - placeholder for type checker."""
        raise NotImplementedError("Must be provided by GraphEntity")

    async def _embed_entities(self, entities: list[Entity]) -> list[Entity]:
        """Embed entities - placeholder for type checker."""
        raise NotImplementedError("Must be provided by GraphEmbedding")

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM - placeholder for type checker."""
        raise NotImplementedError("Must be provided by GraphEmbedding")
