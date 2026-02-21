#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Unified Configuration Management
Centralized configuration system for the financial analysis agent framework
"""

import os
import json
import logging
from typing import Dict, Any, Optional, Union
from dataclasses import dataclass, asdict
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class DatabaseConfig:
    """Database configuration settings."""
    host: str = "localhost"
    port: int = 5432
    name: str = "financial_analysis"
    user: str = "postgres"
    password: str = ""
    pool_size: int = 10
    max_overflow: int = 20


@dataclass
class APIConfig:
    """External API configuration settings."""
    alpha_vantage_key: str = ""
    finnhub_key: str = ""
    quandl_key: str = ""
    fred_key: str = ""
    base_url: str = "https://api.example.com"
    timeout: int = 30
    retry_attempts: int = 3


@dataclass
class ToolConfig:
    """Tool system configuration settings."""
    enabled_tools: list = None
    tool_timeout: int = 60
    max_concurrent_tools: int = 5
    tool_cache_size: int = 100
    fallback_enabled: bool = True
    
    def __post_init__(self):
        if self.enabled_tools is None:
            self.enabled_tools = ["data_fetcher", "analyzer", "validator"]


@dataclass
class AgentConfig:
    """Agent configuration settings."""
    name: str = "FinancialAnalysisAgent"
    version: str = "2.0.0"
    model_name: str = "gpt-4"
    max_tokens: int = 4096
    temperature: float = 0.7
    memory_limit: int = 1000
    log_level: str = "INFO"


@dataclass
class MCPConfig:
    """MCP (Model Context Protocol) configuration settings."""
    enabled: bool = False
    server_url: str = "ws://localhost:8080"
    api_key: str = ""
    timeout: int = 30
    max_connections: int = 10
    heartbeat_interval: int = 60


@dataclass
class A2AConfig:
    """Agent-to-Agent communication configuration settings."""
    enabled: bool = True
    message_timeout: int = 30
    max_message_size: int = 1024 * 1024  # 1MB
    retry_attempts: int = 3
    workflow_timeout: int = 300  # 5 minutes


@dataclass
class LoggingConfig:
    """Logging configuration settings."""
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_path: Optional[str] = None
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5


@dataclass
class SecurityConfig:
    """Security configuration settings."""
    encryption_enabled: bool = False
    api_key_rotation_days: int = 90
    max_login_attempts: int = 5
    session_timeout: int = 3600  # 1 hour
    audit_enabled: bool = True


@dataclass
class UnifiedConfig:
    """Unified configuration container."""
    database: DatabaseConfig = None
    api: APIConfig = None
    tools: ToolConfig = None
    agent: AgentConfig = None
    mcp: MCPConfig = None
    a2a: A2AConfig = None
    logging: LoggingConfig = None
    security: SecurityConfig = None
    
    def __post_init__(self):
        if self.database is None:
            self.database = DatabaseConfig()
        if self.api is None:
            self.api = APIConfig()
        if self.tools is None:
            self.tools = ToolConfig()
        if self.agent is None:
            self.agent = AgentConfig()
        if self.mcp is None:
            self.mcp = MCPConfig()
        if self.a2a is None:
            self.a2a = A2AConfig()
        if self.logging is None:
            self.logging = LoggingConfig()
        if self.security is None:
            self.security = SecurityConfig()


class ConfigManager:
    """Centralized configuration manager."""
    
    def __init__(self, config_dir: str = "config"):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(exist_ok=True)
        self._config: Optional[UnifiedConfig] = None
        self._config_file = self.config_dir / "unified_config.json"
    
    def load_config(self, config_file: Optional[str] = None) -> UnifiedConfig:
        """Load configuration from file or create default."""
        if config_file:
            self._config_file = Path(config_file)
        
        try:
            if self._config_file.exists():
                with open(self._config_file, 'r', encoding='utf-8') as f:
                    config_data = json.load(f)
                self._config = self._dict_to_config(config_data)
                logger.info(f"Loaded configuration from {self._config_file}")
            else:
                self._config = UnifiedConfig()
                logger.info("Created default configuration")
        except Exception as e:
            logger.error(f"Failed to load config: {e}. Using defaults.")
            self._config = UnifiedConfig()
        
        return self._config
    
    def save_config(self, config: Optional[UnifiedConfig] = None) -> bool:
        """Save configuration to file."""
        if config is None:
            config = self._config
        
        if config is None:
            logger.error("No configuration to save")
            return False
        
        try:
            config_dict = self._config_to_dict(config)
            with open(self._config_file, 'w', encoding='utf-8') as f:
                json.dump(config_dict, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved configuration to {self._config_file}")
            return True
        except Exception as e:
            logger.error(f"Failed to save config: {e}")
            return False
    
    def get_config(self) -> UnifiedConfig:
        """Get current configuration."""
        if self._config is None:
            self._config = self.load_config()
        return self._config
    
    def update_config(self, section: str, updates: Dict[str, Any]) -> bool:
        """Update specific configuration section."""
        config = self.get_config()
        
        try:
            if hasattr(config, section):
                section_obj = getattr(config, section)
                for key, value in updates.items():
                    if hasattr(section_obj, key):
                        setattr(section_obj, key, value)
                    else:
                        logger.warning(f"Unknown key {key} in section {section}")
                return True
            else:
                logger.error(f"Unknown configuration section: {section}")
                return False
        except Exception as e:
            logger.error(f"Failed to update config: {e}")
            return False
    
    def validate_config(self, config: Optional[UnifiedConfig] = None) -> bool:
        """Validate configuration integrity."""
        if config is None:
            config = self.get_config()
        
        try:
            # Validate database configuration
            if config.database.port <= 0 or config.database.port > 65535:
                logger.error("Invalid database port")
                return False
            
            # Validate API configuration
            if config.api.timeout <= 0:
                logger.error("Invalid API timeout")
                return False
            
            # Validate tool configuration
            if config.tools.tool_timeout <= 0:
                logger.error("Invalid tool timeout")
                return False
            
            # Validate agent configuration
            if config.agent.max_tokens <= 0:
                logger.error("Invalid max tokens")
                return False
            
            logger.info("Configuration validation passed")
            return True
        except Exception as e:
            logger.error(f"Configuration validation failed: {e}")
            return False
    
    def _config_to_dict(self, config: UnifiedConfig) -> Dict[str, Any]:
        """Convert configuration object to dictionary."""
        return {
            "database": asdict(config.database),
            "api": asdict(config.api),
            "tools": asdict(config.tools),
            "agent": asdict(config.agent),
            "mcp": asdict(config.mcp),
            "a2a": asdict(config.a2a),
            "logging": asdict(config.logging),
            "security": asdict(config.security)
        }
    
    def _dict_to_config(self, config_dict: Dict[str, Any]) -> UnifiedConfig:
        """Convert dictionary to configuration object."""
        config = UnifiedConfig()
        
        if "database" in config_dict:
            config.database = DatabaseConfig(**config_dict["database"])
        if "api" in config_dict:
            config.api = APIConfig(**config_dict["api"])
        if "tools" in config_dict:
            config.tools = ToolConfig(**config_dict["tools"])
        if "agent" in config_dict:
            config.agent = AgentConfig(**config_dict["agent"])
        if "mcp" in config_dict:
            config.mcp = MCPConfig(**config_dict["mcp"])
        if "a2a" in config_dict:
            config.a2a = A2AConfig(**config_dict["a2a"])
        if "logging" in config_dict:
            config.logging = LoggingConfig(**config_dict["logging"])
        if "security" in config_dict:
            config.security = SecurityConfig(**config_dict["security"])
        
        return config


# Global configuration manager instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager(config_dir: str = "config") -> ConfigManager:
    """Get global configuration manager instance."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager(config_dir)
    return _config_manager


def load_config(config_file: Optional[str] = None) -> UnifiedConfig:
    """Load configuration using global manager."""
    return get_config_manager().load_config(config_file)


def save_config(config: Optional[UnifiedConfig] = None) -> bool:
    """Save configuration using global manager."""
    return get_config_manager().save_config(config)


def get_config() -> UnifiedConfig:
    """Get current configuration using global manager."""
    return get_config_manager().get_config()


def update_config_section(section: str, updates: Dict[str, Any]) -> bool:
    """Update configuration section using global manager."""
    return get_config_manager().update_config(section, updates)


# Configuration schema for validation
CONFIG_SCHEMA = {
    "database": {
        "type": "object",
        "properties": {
            "host": {"type": "string"},
            "port": {"type": "number"},
            "name": {"type": "string"},
            "user": {"type": "string"},
            "password": {"type": "string"}
        }
    },
    "api": {
        "type": "object",
        "properties": {
            "alpha_vantage_key": {"type": "string"},
            "finnhub_key": {"type": "string"},
            "base_url": {"type": "string"},
            "timeout": {"type": "number"}
        }
    },
    "tools": {
        "type": "object",
        "properties": {
            "enabled_tools": {"type": "array"},
            "tool_timeout": {"type": "number"},
            "max_concurrent_tools": {"type": "number"}
        }
    },
    "agent": {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "version": {"type": "string"},
            "model_name": {"type": "string"},
            "max_tokens": {"type": "number"}
        }
    }
}


if __name__ == "__main__":
    # Test the unified configuration system
    print("Testing Unified Configuration System")
    
    # Initialize configuration manager
    config_manager = ConfigManager()
    
    # Load configuration
    config = config_manager.load_config()
    print(f"Agent name: {config.agent.name}")
    print(f"Database host: {config.database.host}")
    print(f"Enabled tools: {config.tools.enabled_tools}")
    
    # Validate configuration
    is_valid = config_manager.validate_config()
    print(f"Configuration valid: {is_valid}")
    
    # Update configuration
    success = config_manager.update_config("agent", {"temperature": 0.8})
    print(f"Update successful: {success}")
    
    # Save configuration
    saved = config_manager.save_config()
    print(f"Configuration saved: {saved}")