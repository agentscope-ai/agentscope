# -*- coding: utf-8 -*-
"""model dao"""
from typing import List, Optional
from app.dao.base_dao import BaseDAO
from app.models.model import ModelEntity
from sqlmodel import select


class ModelDAO(BaseDAO[ModelEntity]):
    """model dao"""

    _model_class = ModelEntity

    def get_by_model_id(
        self,
        model_id: str,
        provider: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> Optional[ModelEntity]:
        """
        Get model info by model_id (and optionally provider,
        workspace_id)
        """
        statement = select(self._model_class).where(
            self._model_class.model_id == model_id,
        )
        if provider:
            statement = statement.where(self._model_class.provider == provider)
        if workspace_id:
            statement = statement.where(
                self._model_class.workspace_id == workspace_id,
            )
        result = self.session.exec(statement)
        return result.first()

    def get_by_provider(
        self,
        provider: str,
        workspace_id: Optional[str] = None,
    ) -> List[ModelEntity]:
        """Get all model for a given provider (and optionally workspace_id)"""
        statement = select(self._model_class).where(
            self._model_class.provider == provider,
        )
        if workspace_id:
            statement = statement.where(
                self._model_class.workspace_id == workspace_id,
            )
        result = self.session.exec(statement)
        return result.all()

    def get_by_workspace(self, workspace_id: str) -> List[ModelEntity]:
        """Get all model for a given workspace_id"""
        statement = select(self._model_class).where(
            self._model_class.workspace_id == workspace_id,
        )
        result = self.session.exec(statement)
        return result.all()

    def get_by_type(
        self,
        model_type: str,
        workspace_id: Optional[str] = None,
    ) -> List[ModelEntity]:
        """Get all model by type (and optionally workspace_id)"""
        statement = select(self._model_class).where(
            self._model_class.type == model_type,
        )
        if workspace_id:
            # compatible list/tuple
            if isinstance(workspace_id, (list, tuple)):
                statement = statement.where(
                    self._model_class.workspace_id.in_(workspace_id),
                )
            else:
                statement = statement.where(
                    self._model_class.workspace_id == workspace_id,
                )
        with self.session.no_autoflush:
            result = self.session.exec(statement)
        return result.all()
