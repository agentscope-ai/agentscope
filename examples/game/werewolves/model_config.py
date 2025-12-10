# -*- coding: utf-8 -*-
"""Model configuration management for werewolf game."""
import os
from typing import Optional


class ModelConfig:
    """Unified model configuration management."""
    
    # Default model configurations
    DEFAULT_MODEL_NAME = "qwen3-next-80b-a3b-instruct"
    
    # Environment variable names
    API_KEY_ENV = "DASHSCOPE_API_KEY"
    MODEL_NAME_ENV = "DASHSCOPE_MODEL_NAME"
    
    @classmethod
    def get_api_key(cls) -> Optional[str]:
        """Get API key from environment variable."""
        return os.environ.get(cls.API_KEY_ENV)
    
    @classmethod
    def get_model_name(cls) -> str:
        """Get model name from environment variable or use default."""
        return os.environ.get(cls.MODEL_NAME_ENV, cls.DEFAULT_MODEL_NAME)
    
    @classmethod
    def validate_config(cls) -> bool:
        """Validate that required configuration is available."""
        api_key = cls.get_api_key()
        if not api_key:
            print(f"Warning: {cls.API_KEY_ENV} not set in environment variables")
            return False
        return True
    
    @classmethod
    def get_model_config(cls) -> dict:
        """Get complete model configuration as dictionary."""
        return {
            "api_key": cls.get_api_key(),
            "model_name": cls.get_model_name(),
        }
    
    @classmethod
    def print_config(cls) -> None:
        """Print current model configuration."""
        config = cls.get_model_config()
        print("=== Model Configuration ===")
        print(f"Model Name: {config['model_name']}")
        print(f"API Key: {'Set' if config['api_key'] else 'Not Set'}")
        print("========================")


# Convenience functions
def get_model_name() -> str:
    """Get the configured model name."""
    return ModelConfig.get_model_name()


def get_api_key() -> Optional[str]:
    """Get the configured API key."""
    return ModelConfig.get_api_key()


def validate_model_config() -> bool:
    """Validate model configuration."""
    return ModelConfig.validate_config()