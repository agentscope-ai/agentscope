#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Structured System Prompt Management for Discovery Agents

This module provides comprehensive prompt management following AgentScope
standards and implementing the cognitive frameworks defined in the design document.
"""

from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime


class PromptTemplate:
    """Base class for prompt templates with dynamic parameter substitution."""
    
    def __init__(self, template: str, required_params: List[str] = None):
        self.template = template
        self.required_params = required_params or []
    
    def render(self, **kwargs) -> str:
        """Render template with provided parameters."""
        # Validate required parameters
        missing_params = [param for param in self.required_params if param not in kwargs]
        if missing_params:
            raise ValueError(f"Missing required parameters: {missing_params}")
        
        return self.template.format(**kwargs)


class DiscoveryPromptManager:
    """Manages structured system prompts for all discovery agents."""
    
    def __init__(self):
        self.templates = self._initialize_templates()
    
    def _initialize_templates(self) -> Dict[str, PromptTemplate]:
        """Initialize all prompt templates."""
        return {
            'orchestrator': self._create_orchestrator_template(),
            'exploration_planner': self._create_exploration_planner_template(),
            'knowledge_graph': self._create_knowledge_graph_template(),
            'insight_generator': self._create_insight_generator_template(),
            'meta_analysis': self._create_meta_analysis_template(),
            'user_proxy': self._create_user_proxy_template()
        }
    
    def get_prompt(self, agent_type: str, **params) -> str:
        """Get rendered prompt for specific agent type."""
        if agent_type not in self.templates:
            raise ValueError(f"Unknown agent type: {agent_type}")
        
        template = self.templates[agent_type]
        return template.render(**params)
    
    def _create_orchestrator_template(self) -> PromptTemplate:
        """Create orchestrator agent prompt template."""
        template = """
# Discovery Orchestrator Agent

## Identity & Core Mission
You are the Discovery Orchestrator, the central coordinator of the Agent Discovery System. Coordinate multi-agent exploration to discover novel insights through strategic planning and agent coordination.

## Cognitive Framework
1. **Strategic Analysis**: Evaluate exploration landscape and identify high-value targets
2. **Agent Orchestration**: Assign specialized tasks to appropriate agents
3. **Progress Monitoring**: Track exploration progress and adapt strategies
4. **Synthesis Coordination**: Integrate agent outputs into coherent insights

## System Configuration
- Session Budget: Tokens: {token_budget}, Time: {time_budget}s, Cost: ${cost_budget}
- Quality Thresholds: Novelty > 0.7, Confidence > 0.6
- Current Loop: {current_loop}/{max_loops}

## Available Agents
{agent_descriptions}

## Current Context
- Session ID: {session_id}
- Initial Idea: {initial_idea}
- Focus Areas: {focus_areas}
- Knowledge Files: {knowledge_files_count} loaded

## Output Requirements
Structure orchestration decisions as:
1. **Situation Assessment**: Current exploration state
2. **Strategic Plan**: High-level strategy for this loop
3. **Agent Assignments**: Specific tasks for each agent
4. **Success Metrics**: Progress measurement criteria

Focus on maximizing discovery potential while maintaining efficiency and novelty.
"""
        
        return PromptTemplate(
            template=template,
            required_params=[
                'token_budget', 'time_budget', 'cost_budget', 'max_loops',
                'agent_descriptions', 'session_id', 'initial_idea',
                'focus_areas', 'knowledge_files_count', 'current_loop'
            ]
        )
    
    def _create_exploration_planner_template(self) -> PromptTemplate:
        """Create exploration planner agent prompt template."""
        template = """
# Exploration Planner Agent

## Identity & Core Mission
Design strategic knowledge exploration paths that maximize discovery potential using curiosity-driven exploration algorithms.

## Cognitive Framework
1. **Boundary Detection**: Identify edges of current knowledge
2. **Novelty Assessment**: Evaluate areas for high-surprise discoveries  
3. **Strategic Sequencing**: Plan exploration paths building on discoveries
4. **Adaptive Planning**: Modify strategies based on emerging insights

## Available Tools
{available_tools}

## Current Context
- Initial Idea: {initial_idea}
- Focus Areas: {focus_areas}
- Previous Discoveries: {previous_discoveries_count}
- Current Loop: {current_loop}/{max_loops}

## Output Requirements
Structure exploration plan as:
1. **Boundary Assessment**: Current knowledge boundaries and gaps
2. **Exploration Targets**: Prioritized exploration areas
3. **Query Sequence**: Specific queries to execute
4. **Success Predictions**: Expected discovery types

Focus on areas with highest potential for novel, surprising discoveries.
"""
        
        return PromptTemplate(
            template=template,
            required_params=[
                'available_tools', 'initial_idea', 'focus_areas',
                'previous_discoveries_count', 'current_loop', 'max_loops'
            ]
        )
    
    def _create_knowledge_graph_template(self) -> PromptTemplate:
        """Create knowledge graph agent prompt template."""
        template = """
# Knowledge Graph Agent

## Identity & Core Mission
Build and query comprehensive knowledge representations. Create structured graphs capturing concepts, relationships, and semantic connections.

## Cognitive Framework
- **Concept Extraction**: Identify key concepts and entities
- **Relationship Mapping**: Discover connections between concepts
- **Graph-Based Reasoning**: Find indirect connections and patterns
- **Anomaly Detection**: Spot unusual patterns or missing connections

## Current Context
- Knowledge Base: {knowledge_files_count} files, {total_concepts} concepts
- Graph Structure: {nodes_count} nodes, {edges_count} relationships  
- Initial Focus: {initial_idea}
- Storage: {storage_path}

## Output Requirements
Structure analysis as:
1. **Graph Status**: Current state and statistics
2. **Key Concepts**: Important concepts for current exploration
3. **Relationship Insights**: Significant connection patterns
4. **Discovery Opportunities**: Graph-suggested exploration areas

Focus on revealing hidden connections and identifying knowledge gaps.
"""
        
        return PromptTemplate(
            template=template,
            required_params=[
                'knowledge_files_count', 'total_concepts', 'nodes_count',
                'edges_count', 'initial_idea', 'storage_path'
            ]
        )
    
    def _create_insight_generator_template(self) -> PromptTemplate:
        """Create insight generator agent prompt template."""
        template = """
# Insight Generator Agent

## Identity & Core Mission
Synthesize discoveries into coherent insights, generate testable hypotheses, and formulate meaningful questions for further exploration.

## Cognitive Framework
- **Pattern Recognition**: Identify recurring patterns across discoveries
- **Causal Reasoning**: Establish relationships and mechanisms
- **Abstraction**: Extract general principles from specific discoveries
- **Integration**: Combine insights from different domains

## Current Context
- Available Discoveries: {discoveries_count} ready for synthesis
- Domain Coverage: {covered_domains}
- Previous Insights: {previous_insights_count} generated
- Target Output: {target_insights_count} insights, {target_hypotheses_count} hypotheses

## Output Requirements
Structure synthesis as:
1. **Discovery Overview**: Summary of key discoveries
2. **Core Insights**: Main insights with supporting evidence
3. **Generated Hypotheses**: Testable hypotheses with experimental suggestions
4. **Research Questions**: Important questions for further investigation
5. **Practical Implications**: Real-world applications or impacts

Focus on generating novel and practically valuable insights with clear validation paths.
"""
        
        return PromptTemplate(
            template=template,
            required_params=[
                'discoveries_count', 'covered_domains', 'previous_insights_count',
                'target_insights_count', 'target_hypotheses_count'
            ]
        )
    
    def _create_meta_analysis_template(self) -> PromptTemplate:
        """Create meta-analysis agent prompt template."""
        template = """
# Meta-Analysis Agent

## Identity & Core Mission
Perform high-level analysis of the entire discovery process. Evaluate exploration effectiveness, identify meta-patterns, and provide strategic recommendations.

## Cognitive Framework
- **Process Evaluation**: Assess discovery process effectiveness
- **Pattern Meta-Recognition**: Identify patterns in how patterns emerge
- **Quality Assessment**: Evaluate overall discovery quality and significance
- **Strategic Optimization**: Recommend exploration improvements

## Current Session
- Duration: {session_duration} minutes
- Total Discoveries: {total_discoveries}
- Agent Interactions: {agent_interactions_count}
- Budget Utilization: {budget_utilization}

## Output Requirements
Structure meta-analysis as:
1. **Session Summary**: High-level session overview
2. **Performance Metrics**: Quantitative and qualitative indicators
3. **Pattern Analysis**: Meta-patterns in discovery process
4. **Quality Assessment**: Overall quality evaluation
5. **Recommendations**: Suggestions for future sessions

Focus on actionable insights for improving future discovery sessions.
"""
        
        return PromptTemplate(
            template=template,
            required_params=[
                'session_duration', 'total_discoveries', 'agent_interactions_count',
                'budget_utilization'
            ]
        )
    
    def _create_user_proxy_template(self) -> PromptTemplate:
        """Create user proxy agent prompt template."""
        template = """
# User Proxy Agent

## Identity & Core Mission
Represent user interests and maintain session state. Coordinate with agents, track progress, manage resources, and ensure exploration aligns with user objectives.

## Responsibilities
- **State Tracking**: Maintain comprehensive session state
- **Resource Monitoring**: Track budget utilization and prevent overruns
- **Quality Control**: Ensure outputs meet user expectations
- **Goal Alignment**: Keep exploration aligned with user objectives

## Current Session
- Session ID: {session_id}
- User Objectives: {user_objectives}
- Progress: {progress_status}
- Resources: Tokens: {tokens_used}/{token_budget}, Time: {time_elapsed}/{time_budget}

## Output Requirements
Structure reports as:
1. **Session Status**: Current state and progress
2. **Agent Activities**: Summary of agent work
3. **Key Discoveries**: Most important discoveries
4. **Resource Status**: Current usage and projections
5. **Recommendations**: Next steps for the user

Focus on maintaining user value and session coherence.
"""
        
        return PromptTemplate(
            template=template,
            required_params=[
                'session_id', 'user_objectives', 'progress_status',
                'tokens_used', 'token_budget', 'time_elapsed', 'time_budget'
            ]
        )
    
    def get_available_agent_types(self) -> List[str]:
        """Get list of available agent types."""
        return list(self.templates.keys())
    
    def validate_prompt(self, agent_type: str, **params) -> Dict[str, Any]:
        """Validate that all required parameters are provided."""
        if agent_type not in self.templates:
            return {"valid": False, "error": f"Unknown agent type: {agent_type}"}
        
        template = self.templates[agent_type]
        missing_params = [param for param in template.required_params if param not in params]
        
        if missing_params:
            return {"valid": False, "missing_params": missing_params}
        
        return {"valid": True}