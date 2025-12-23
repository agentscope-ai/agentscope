# -*- coding: utf-8 -*-
"""Entity extraction for graph knowledge base."""

import asyncio
import json

from ...model import ChatModelBase
from ..._logging import logger
from .._reader import Document
from ._embedding import _clean_llm_json_response
from ._types import Entity


class GraphEntity:
    """Entity extraction functionality.

    This class provides methods for:
    - Extracting entities from documents using LLM
    - Multi-round gleanings for improved recall
    - Entity deduplication and resolution
    """

    # pylint: disable=too-few-public-methods

    llm_model: ChatModelBase | None
    entity_extraction_config: dict

    async def _extract_entities(
        self,
        documents: list[Document],
    ) -> list[Entity]:
        """Extract entities from documents.

        This method supports single-pass extraction and optional gleanings
        (multi-round extraction to improve recall).

        Args:
            documents: List of documents

        Returns:
            List of extracted and resolved entities
        """
        # Single-pass extraction
        entities = await self._extract_entities_single_pass(documents)

        # Optional gleanings rounds
        if self.entity_extraction_config.get("enable_gleanings", False):
            gleanings_rounds = self.entity_extraction_config.get(
                "gleanings_rounds",
                2,
            )
            for round_num in range(gleanings_rounds):
                logger.info(
                    "Gleanings round %s/%s",
                    round_num + 1,
                    gleanings_rounds,
                )
                new_entities = await self._gleanings_pass(documents, entities)
                entities.extend(new_entities)
                logger.info("Found %s additional entities", len(new_entities))

        # Resolve duplicates
        entities = self._resolve_entities(entities)

        return entities

    async def _extract_entities_single_pass(
        self,
        documents: list[Document],
    ) -> list[Entity]:
        """Extract entities in a single pass.

        Uses LLM to extract entities from each document chunk.

        Args:
            documents: List of documents

        Returns:
            List of extracted entities (may contain duplicates)
        """
        if not self.llm_model:
            return []

        # Extract entities from each document concurrently
        max_concurrent = 5  # Limit concurrent LLM calls
        semaphore = asyncio.Semaphore(max_concurrent)

        async def extract_from_doc(doc: Document) -> list[Entity]:
            async with semaphore:
                return await self._extract_entities_from_text(
                    doc.get_text(),
                    max_entities=self.entity_extraction_config.get(
                        "max_entities_per_chunk",
                        10,
                    ),
                )

        tasks = [extract_from_doc(doc) for doc in documents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect entities and handle exceptions
        all_entities: list[Entity] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Entity extraction failed for document %s: %s",
                    documents[i].id,
                    result,
                )
            elif isinstance(result, list):
                all_entities.extend(result)

        return all_entities

    async def _extract_entities_from_text(
        self,
        text: str,
        max_entities: int = 10,
    ) -> list[Entity]:
        """Extract entities from a single text using LLM.

        Args:
            text: Text content
            max_entities: Maximum number of entities to extract

        Returns:
            List of extracted entities
        """
        # Build prompt
        entity_types_str = ", ".join(
            self.entity_extraction_config.get("entity_types", []),
        )

        prompt = f"""Extract key entities from the following text. You must \
return ONLY a valid JSON array with no additional text, explanations, \
or formatting.

Text: {text}

Requirements:
1. Extract maximum {max_entities} most important entities
2. Each entity must have exactly these fields: "name", "type", \
"description"
3. Entity type must be one of: {entity_types_str}
4. Return ONLY the JSON array, no markdown, no code blocks, no \
explanations

Expected format:
[{{"name": "entity name", "type": "PERSON", "description": "brief \
description"}}]

JSON array:"""

        try:
            # Call LLM and get response text
            response_text = await self._call_llm(prompt)

            # Clean JSON from response
            json_text = _clean_llm_json_response(response_text)

            # Parse JSON
            entity_data = json.loads(json_text)

            # Validate and create Entity objects using Pydantic
            entities = []
            for data in entity_data[:max_entities]:
                try:
                    entity = Entity(**data)
                    entities.append(entity)
                except Exception as e:
                    logger.warning(
                        "Invalid entity data: %s, error: %s",
                        data,
                        e,
                    )

            return entities

        except json.JSONDecodeError as e:
            logger.error("Failed to parse LLM response as JSON: %s", e)
            return []
        except Exception as e:
            logger.error("Entity extraction failed: %s", e)
            return []

    async def _gleanings_pass(
        self,
        documents: list[Document],
        existing_entities: list[Entity],
    ) -> list[Entity]:
        """Perform a gleanings pass to find missed entities.

        Gleanings is a technique to improve recall by asking the LLM
        to review the text again with knowledge of already-found entities.

        Args:
            documents: List of documents
            existing_entities: Entities found in previous passes

        Returns:
            List of newly discovered entities
        """
        if not self.llm_model:
            return []

        # Get existing entity names
        existing_names = {entity.name for entity in existing_entities}
        existing_names_str = ", ".join(
            list(existing_names)[:20],
        )  # Limit to 20 for prompt

        # Extract from each document
        max_concurrent = 5
        semaphore = asyncio.Semaphore(max_concurrent)

        async def glean_from_doc(doc: Document) -> list[Entity]:
            async with semaphore:
                prompt = f"""You already extracted these entities: \
{existing_names_str}

Review the text again and find any entities you might have missed. You \
must return ONLY a valid JSON array with no additional text.

Text: {doc.get_text()}

Requirements:
1. Return ONLY new entities not in the list above
2. Each entity must have exactly these fields: "name", "type", \
"description"
3. Return ONLY the JSON array, no markdown, no code blocks, no \
explanations
4. If no new entities, return []

Expected format:
[{{"name": "entity name", "type": "PERSON", "description": "brief \
description"}}]

JSON array:"""

                try:
                    response_text = await self._call_llm(prompt)
                    if not response_text:
                        response_text = "[]"

                    # Clean and parse JSON
                    json_text = _clean_llm_json_response(response_text)
                    entity_data = json.loads(json_text)

                    # Filter out existing entities and validate
                    new_entities = []
                    for data in entity_data:
                        if data.get("name") not in existing_names:
                            try:
                                entity = Entity(**data)
                                new_entities.append(entity)
                            except Exception as e:
                                logger.warning(
                                    "Invalid entity data: %s, error: %s",
                                    data,
                                    e,
                                )

                    return new_entities

                except Exception as e:
                    logger.error("Gleanings extraction failed: %s", e)
                    return []

        tasks = [glean_from_doc(doc) for doc in documents]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect new entities
        new_entities: list[Entity] = []
        for result in results:
            if isinstance(result, Exception):
                logger.error("Gleanings failed: %s", result)
            elif isinstance(result, list):
                new_entities.extend(result)

        return new_entities

    def _resolve_entities(self, entities: list[Entity]) -> list[Entity]:
        """Resolve duplicate entities.

        This method deduplicates entities by name (case-insensitive).
        For duplicates, it keeps the entity with the longer description.

        Args:
            entities: List of entities (may contain duplicates)

        Returns:
            List of unique entities
        """
        entity_map: dict[str, Entity] = {}

        for entity in entities:
            name_lower = entity.name.lower()

            if name_lower not in entity_map:
                entity_map[name_lower] = entity
            else:
                # Keep the one with longer description
                existing = entity_map[name_lower]
                if len(entity.description) > len(existing.description):
                    entity_map[name_lower] = entity

        resolved = list(entity_map.values())
        logger.info(
            "Resolved %s entities to %s unique entities",
            len(entities),
            len(resolved),
        )

        return resolved

    async def _call_llm(self, prompt: str) -> str:
        """Call LLM - placeholder for type checker."""
        raise NotImplementedError("Must be provided by GraphEmbedding")
