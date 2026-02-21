# -*- coding: utf-8 -*-
"""Model selector for UniversalAgent."""

from typing import Dict, List, Optional, Tuple
from enum import Enum
from pydantic import BaseModel

from .config import UniversalAgentConfig, ModelConfig


class ModelPreference(str, Enum):
    """Model preference criteria."""
    CAPABILITY = "capability"
    COST = "cost"
    SPEED = "speed"
    QUALITY = "quality"
    RELIABILITY = "reliability"


class ModelScore(BaseModel):
    """Model scoring result."""
    model_name: str
    score: float
    capabilities_match: float
    cost_efficiency: float
    speed_score: float
    quality_score: float
    availability: bool


class ModelSelector:
    """Selects the best model for a given task."""
    
    def __init__(self, config: UniversalAgentConfig):
        self.config = config
        self.model_capabilities = {
            "openai": {
                "text": 1.0,
                "vision": 1.0,
                "tools": 1.0,
                "coding": 0.95,
                "multimodal": 0.9,
                "speed": 0.8,
                "cost": 0.6,
                "quality": 0.95,
                "reliability": 0.9
            },
            "anthropic": {
                "text": 1.0,
                "vision": 0.9,
                "tools": 0.95,
                "coding": 0.98,
                "multimodal": 0.8,
                "speed": 0.7,
                "cost": 0.5,
                "quality": 0.97,
                "reliability": 0.95
            },
            "gemini": {
                "text": 0.95,
                "vision": 1.0,
                "tools": 0.9,
                "coding": 0.85,
                "multimodal": 1.0,
                "speed": 0.9,
                "cost": 0.8,
                "quality": 0.9,
                "reliability": 0.85
            },
            "dashscope": {
                "text": 0.95,
                "vision": 0.9,
                "tools": 0.95,
                "coding": 0.8,
                "multimodal": 0.85,
                "speed": 0.95,
                "cost": 0.9,
                "quality": 0.85,
                "reliability": 0.8
            },
            "ollama": {
                "text": 0.8,
                "vision": 0.7,
                "tools": 0.6,
                "coding": 0.75,
                "multimodal": 0.5,
                "speed": 0.6,
                "cost": 1.0,
                "quality": 0.7,
                "reliability": 0.75
            }
        }
        
        self.task_model_preferences = {
            "coding": ["anthropic", "openai", "gemini", "dashscope", "ollama"],
            "analysis": ["anthropic", "openai", "gemini", "dashscope"],
            "research": ["openai", "anthropic", "gemini", "dashscope"],
            "conversation": ["anthropic", "openai", "gemini", "dashscope"],
            "multimodal": ["gemini", "openai", "anthropic", "dashscope"],
            "planning": ["anthropic", "openai", "gemini", "dashscope"],
            "file_operation": ["openai", "anthropic", "gemini", "dashscope"],
            "web_search": ["openai", "anthropic", "gemini", "dashscope"],
            "data_processing": ["openai", "anthropic", "gemini", "dashscope"]
        }
    
    def select_model(
        self, 
        task_type: str,
        required_capabilities: List[str],
        modality: str,
        complexity: str,
        preference: ModelPreference = ModelPreference.CAPABILITY
    ) -> Tuple[str, ModelScore]:
        """Select the best model for the given task."""
        
        # Get available models
        available_models = [model.name for model in self.config.models]
        
        # Score each model
        model_scores = []
        for model_name in available_models:
            score = self._score_model(
                model_name, task_type, required_capabilities, 
                modality, complexity, preference
            )
            model_scores.append(score)
        
        # Sort by score and return the best
        model_scores.sort(key=lambda x: x.score, reverse=True)
        
        if model_scores:
            return model_scores[0].model_name, model_scores[0]
        else:
            # Fallback to default model
            return self.config.default_model, ModelScore(
                model_name=self.config.default_model,
                score=0.5,
                capabilities_match=0.5,
                cost_efficiency=0.5,
                speed_score=0.5,
                quality_score=0.5,
                availability=True
            )
    
    def _score_model(
        self,
        model_name: str,
        task_type: str,
        required_capabilities: List[str],
        modality: str,
        complexity: str,
        preference: ModelPreference
    ) -> ModelScore:
        """Score a model for the given requirements."""
        
        # Get model capabilities
        model_cap = self.model_capabilities.get(model_name, {})
        model_config = self.config.get_model_config(model_name)
        
        if not model_config:
            return ModelScore(
                model_name=model_name,
                score=0.0,
                capabilities_match=0.0,
                cost_efficiency=0.0,
                speed_score=0.0,
                quality_score=0.0,
                availability=False
            )
        
        # Check capability match
        capabilities_match = self._calculate_capabilities_match(
            model_config.capabilities, required_capabilities, modality
        )
        
        # Calculate individual scores
        cost_efficiency = model_cap.get("cost", 0.5)
        speed_score = model_cap.get("speed", 0.5)
        quality_score = model_cap.get("quality", 0.5)
        
        # Get task preference ranking
        task_preference = self.task_model_preferences.get(task_type, list(self.model_capabilities.keys()))
        preference_score = self._calculate_preference_score(model_name, task_preference)
        
        # Adjust scores based on complexity
        complexity_multiplier = self._get_complexity_multiplier(complexity)
        
        # Calculate final score based on preference
        if preference == ModelPreference.CAPABILITY:
            base_score = capabilities_match * 0.5 + preference_score * 0.3 + quality_score * 0.2
        elif preference == ModelPreference.COST:
            base_score = cost_efficiency * 0.4 + capabilities_match * 0.3 + speed_score * 0.3
        elif preference == ModelPreference.SPEED:
            base_score = speed_score * 0.4 + capabilities_match * 0.3 + cost_efficiency * 0.3
        elif preference == ModelPreference.QUALITY:
            base_score = quality_score * 0.5 + capabilities_match * 0.3 + preference_score * 0.2
        else:  # RELIABILITY
            reliability = model_cap.get("reliability", 0.5)
            base_score = reliability * 0.4 + capabilities_match * 0.3 + quality_score * 0.3
        
        # Apply complexity multiplier
        final_score = base_score * complexity_multiplier
        
        return ModelScore(
            model_name=model_name,
            score=final_score,
            capabilities_match=capabilities_match,
            cost_efficiency=cost_efficiency,
            speed_score=speed_score,
            quality_score=quality_score,
            availability=True
        )
    
    def _calculate_capabilities_match(
        self, 
        model_capabilities: List[str], 
        required_capabilities: List[str],
        modality: str
    ) -> float:
        """Calculate how well the model capabilities match requirements."""
        
        # Basic capabilities mapping
        capability_map = {
            "text": ["text"],
            "vision": ["vision", "image"],
            "tools": ["tools"],
            "coding": ["coding", "tools"],
            "multimodal": ["multimodal", "vision", "image", "text", "audio"],
            "tts": ["tts", "audio"],
            "stt": ["stt", "audio"]
        }
        
        match_score = 0.0
        total_required = len(required_capabilities)
        
        if total_required == 0:
            return 1.0
        
        for req_cap in required_capabilities:
            # Get the capabilities needed for this requirement
            needed_caps = capability_map.get(req_cap, [req_cap])
            
            # Check if model has any of the needed capabilities
            has_capability = any(
                cap in model_capabilities or needed_cap in model_capabilities
                for needed_cap in needed_caps
            )
            
            if has_capability:
                match_score += 1.0
        
        # Adjust for modality requirements
        if modality == "multimodal":
            if "multimodal" in model_capabilities:
                match_score += 0.5
            elif "vision" in model_capabilities:
                match_score += 0.3
        
        # Normalize to 0-1 range
        base_score = match_score / total_required
        return min(base_score, 1.0)
    
    def _calculate_preference_score(self, model_name: str, preference_order: List[str]) -> float:
        """Calculate preference score based on task-specific preference order."""
        try:
            position = preference_order.index(model_name)
            # Higher score for higher preference (lower index)
            return 1.0 - (position / len(preference_order))
        except ValueError:
            # Model not in preference list
            return 0.3
    
    def _get_complexity_multiplier(self, complexity: str) -> float:
        """Get complexity-based score multiplier."""
        multipliers = {
            "simple": 1.0,
            "medium": 0.95,
            "complex": 0.9
        }
        return multipliers.get(complexity, 1.0)
    
    def get_fallback_models(self, primary_model: str, task_type: str) -> List[str]:
        """Get fallback models for the given primary model and task."""
        # Get configured fallback order
        fallback_order = self.config.model_fallback_order.copy()
        
        # Remove primary model from fallback list
        if primary_model in fallback_order:
            fallback_order.remove(primary_model)
        
        # Filter to available models
        available_models = [model.name for model in self.config.models]
        fallback_models = [model for model in fallback_order if model in available_models]
        
        # If no task-specific preferences, return general fallback
        if task_type not in self.task_model_preferences:
            return fallback_models
        
        # Get task-specific preferences
        task_preferences = self.task_model_preferences[task_type]
        
        # Combine task preferences with general fallback
        combined = []
        for model in task_preferences:
            if model in fallback_models and model not in combined:
                combined.append(model)
        
        # Add remaining fallback models
        for model in fallback_models:
            if model not in combined:
                combined.append(model)
        
        return combined
    
    def recommend_models(
        self,
        task_type: str,
        required_capabilities: List[str],
        modality: str,
        complexity: str,
        top_k: int = 3
    ) -> List[ModelScore]:
        """Recommend top k models for the task."""
        
        # Get available models
        available_models = [model.name for model in self.config.models]
        
        # Score all models
        model_scores = []
        for model_name in available_models:
            score = self._score_model(
                model_name, task_type, required_capabilities,
                modality, complexity, ModelPreference.CAPABILITY
            )
            model_scores.append(score)
        
        # Sort by score and return top k
        model_scores.sort(key=lambda x: x.score, reverse=True)
        return model_scores[:top_k]