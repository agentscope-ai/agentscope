# -*- coding: utf-8 -*-
# mypy: ignore-errors
# pylint: disable=W0107, C0114, C0115
from abc import ABC, abstractmethod
from typing import Any


class VectorStoreBase(ABC):
    @abstractmethod
    def create_col(self, name: Any, vector_size: Any, distance: Any) -> Any:
        """Create a new collection."""
        pass

    @abstractmethod
    def insert(
        self,
        vectors: Any,
        payloads: Any = None,
        ids: Any = None,
    ) -> Any:
        """Insert vectors into a collection."""
        pass

    @abstractmethod
    def search(
        self,
        query: Any,
        vectors: Any,
        limit: int = 5,
        filters: Any = None,
    ) -> Any:
        """Search for similar vectors."""
        pass

    @abstractmethod
    def delete(self, vector_id: Any) -> Any:
        """Delete a vector by ID."""
        pass

    @abstractmethod
    def update(
        self,
        vector_id: Any,
        vector: Any = None,
        payload: Any = None,
    ) -> Any:
        """Update a vector and its payload."""
        pass

    @abstractmethod
    def get(self, vector_id: Any) -> Any:
        """Retrieve a vector by ID."""
        pass

    @abstractmethod
    def list_cols(self) -> None:
        """List all collections."""
        pass

    @abstractmethod
    def delete_col(self) -> None:
        """Delete a collection."""
        pass

    @abstractmethod
    def col_info(self) -> None:
        """Get information about a collection."""
        pass

    @abstractmethod
    def list(self, filters: Any = None, limit: Any = None) -> Any:
        """List all memories."""
        pass

    @abstractmethod
    def reset(self) -> None:
        """Reset by delete the collection and recreate it."""
        pass
