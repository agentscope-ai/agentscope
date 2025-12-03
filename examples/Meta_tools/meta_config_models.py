"""
Pydantic models for Meta Tools configuration validation.

This module defines the data models used for validating Meta_tool_config.json
and ensuring type safety during configuration loading.
"""

from typing import Dict, List
from pydantic import BaseModel, Field, field_validator, model_validator, RootModel


class CategoryConfig(BaseModel):
    """Configuration model for a single tool category.
    
    Attributes:
        description (`str`):
            A comprehensive description of what this category manages
            and its functional scope.
        tool_usage_notes (`str`):
            Special usage notes and considerations for tools in this
            category. Can be empty string if no special notes are needed.
        tools (`List[str]`):
            List of tool names that belong to this category.
            Each tool name must be a non-empty string.
    """
    
    description: str = Field(
        ...,
        min_length=10,
        description="Comprehensive description of the category's purpose and scope"
    )
    
    tool_usage_notes: str = Field(
        default="",
        description="Special usage notes for tools in this category"
    )
    
    tools: List[str] = Field(
        ...,
        min_items=1,
        description="List of tool names belonging to this category"
    )
    
    @field_validator('tools')
    @classmethod
    def validate_tools(cls, v):
        """Validate that all tool names are non-empty strings."""
        if not v:
            raise ValueError("Category must have at least one tool")
        
        for tool_name in v:
            if not isinstance(tool_name, str) or not tool_name.strip():
                raise ValueError(f"Tool name must be a non-empty string, got: {tool_name}")
        
        return v
    
    @field_validator('description')
    @classmethod
    def validate_description(cls, v):
        """Validate that description is meaningful."""
        if not v.strip():
            raise ValueError("Description cannot be empty")
        return v.strip()


class MetaToolConfig(RootModel[Dict[str, CategoryConfig]]):
    """Root configuration model for Meta Tools system.
    
    This model represents the entire Meta_tool_config.json structure
    and validates that all categories are properly configured.
    
    The root structure is a dictionary mapping category names to their configurations.
    Category names must be non-empty strings.
    """
    
    root: Dict[str, CategoryConfig] = Field(
        ...,
        description="Dictionary of category configurations"
    )
    
    @model_validator(mode='after')
    def validate_not_empty(self) -> 'MetaToolConfig':
        """Validate that configuration has at least one category."""
        if not self.root:
            raise ValueError("Configuration must have at least one category")
        
        # Validate category names are non-empty
        for category_name in self.root.keys():
            if not category_name or not category_name.strip():
                raise ValueError(f"Category name cannot be empty or whitespace")
        
        return self
    
    # Dictionary-like access methods for better user experience
    # These allow using MetaToolConfig as if it were a dict without conversion
    # Example: config["category_name"] instead of config.root["category_name"]
    
    def __iter__(self):
        """Iterate over category names: for name in config: ..."""
        return iter(self.root)
    
    def __getitem__(self, key: str) -> CategoryConfig:
        """Access category by name: config["category_name"]"""
        return self.root[key]
    
    def items(self):
        """Get (name, config) pairs: for name, cfg in config.items(): ..."""
        return self.root.items()
    
    def keys(self):
        """Get category names: list(config.keys())"""
        return self.root.keys()
    
    def values(self):
        """Get category configs: list(config.values())"""
        return self.root.values()
    
    def __len__(self) -> int:
        """Get number of categories: len(config)"""
        return len(self.root)
    
    @classmethod
    def from_json_file(cls, file_path: str) -> "MetaToolConfig":
        """Load and validate configuration from JSON file.
        
        Args:
            file_path (`str`): Path to the Meta_tool_config.json file.
            
        Returns:
            `MetaToolConfig`: Validated configuration instance.
            
        Raises:
            ValidationError: If the configuration is invalid.
            FileNotFoundError: If the configuration file doesn't exist.
            JSONDecodeError: If the JSON is malformed.
        """
        import json
        
        with open(file_path, "r", encoding="utf-8") as f:
            config_data = json.load(f)
        
        return cls(config_data)
    
    @classmethod
    def from_dict(cls, config_dict: Dict) -> "MetaToolConfig":
        """Create configuration from dictionary.
        
        Args:
            config_dict (`Dict`): Configuration dictionary.
            
        Returns:
            `MetaToolConfig`: Validated configuration instance.
        """
        return cls(config_dict)
    
    def to_dict(self) -> Dict[str, Dict]:
        """Convert to plain dictionary format.
        
        Returns:
            `Dict[str, Dict]`: Plain dictionary representation.
        """
        return {
            category_name: category_config.model_dump()
            for category_name, category_config in self.root.items()
        }
