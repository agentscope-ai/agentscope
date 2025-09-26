# -*- coding: utf-8 -*-
"""Model related service"""
from typing import Optional, List
from app.dao.model_dao import ModelDAO
from app.models.model import ModelEntity
from .base_service import BaseService


class ModelService(BaseService[ModelDAO]):
    """Model related services"""

    _dao_cls = ModelDAO

    def add_model(
        self,
        provider: str,
        model_id: Optional[str],
        model_name: str,
        tags: Optional[str],
        type_: Optional[str],
        workspace_id: Optional[str],
        creator: Optional[str],
    ) -> bool:
        """Add model"""
        model_entity = ModelEntity(
            provider=provider,
            model_id=model_id,
            name=model_name,
            tags=tags,
            type=type_,
            workspace_id=workspace_id,
            creator=creator,
            modifier=creator,
        )
        self.create(model_entity)
        return True

    def update_model(
        self,
        provider: str,
        model_id: str,
        model_name: Optional[str] = None,
        tags: Optional[str] = None,
        icon: Optional[str] = None,
        enable: Optional[bool] = None,
        modifier: Optional[str] = None,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Update model"""
        model = self.dao.get_by_model_id(
            model_id=model_id,
            provider=provider,
            workspace_id=workspace_id,
        )
        if not model:
            raise ValueError("Model not found")
        if model_name is not None:
            model.name = model_name
        if tags is not None:
            model.tags = tags
        if icon is not None:
            model.icon = icon
        if enable is not None:
            model.enable = enable
        if modifier is not None:
            model.modifier = modifier
        self.update(model.id, model)  # type: ignore
        return True

    def delete_model(
        self,
        provider: str,
        model_id: str,
        workspace_id: Optional[str] = None,
    ) -> bool:
        """Delete model"""
        model = self.dao.get_by_model_id(
            model_id=model_id,
            provider=provider,
            workspace_id=workspace_id,
        )
        if not model:
            raise ValueError("Model not found")
        self.delete(model.id)  # type: ignore
        return True

    def list_models(
        self,
        provider: str,
        workspace_id: Optional[str] = None,
    ) -> List[ModelEntity]:
        """List model for a provider"""
        models = self.dao.get_by_provider(
            provider=provider,
            workspace_id=workspace_id,
        )
        return models

    def get_model(
        self,
        provider: str,
        model_id: str,
        workspace_id: Optional[str] = None,
    ) -> Optional[ModelEntity]:
        """Get model by model_id"""
        model = self.dao.get_by_model_id(
            model_id=model_id,
            provider=provider,
            workspace_id=workspace_id,
        )
        return model

    def get_parameter_rules(
        self,
        provider: str,  # pylint: disable=unused-argument
        model_id: str,
    ) -> List[dict]:
        """Get model parameter rules"""
        # For demonstration, only gpt-4 and gpt-3.5-turbo have rules. Extend
        # as needed.
        rules = {
            "gpt-4": [
                {
                    "key": "temperature",
                    "name": "温度",
                    "description": "控制模型输出的随机性",
                    "type": "double",
                    "default_value": 0.7,
                    "min": 0.0,
                    "max": 2.0,
                    "required": False,
                },
                {
                    "key": "max_tokens",
                    "name": "最大令牌数",
                    "description": "生成文本的最大长度",
                    "type": "integer",
                    "default_value": 1000,
                    "min": 1,
                    "max": 4096,
                    "required": False,
                },
            ],
            "gpt-3.5-turbo": [
                {
                    "key": "temperature",
                    "name": "温度",
                    "description": "控制模型输出的随机性",
                    "type": "double",
                    "default_value": 0.7,
                    "min": 0.0,
                    "max": 2.0,
                    "required": False,
                },
                {
                    "key": "max_tokens",
                    "name": "最大令牌数",
                    "description": "生成文本的最大长度",
                    "type": "integer",
                    "default_value": 1000,
                    "min": 1,
                    "max": 4096,
                    "required": False,
                },
            ],
        }
        return rules.get(model_id, [])

    def list_models_by_type(
        self,
        model_type: str,
        workspace_id: Optional[str] = None,
    ) -> List[ModelEntity]:
        """List model by type"""
        models = self.dao.get_by_type(
            model_type=model_type,
            workspace_id=workspace_id,
        )

        # 将supported_model_types字符串转换为列表
        for model in models:
            if model.tags:
                model.tags = model.tags.split(",")
            else:
                model.tags = []

        return models
