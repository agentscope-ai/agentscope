# -*- coding: utf-8 -*-
"""
Agent Discovery System: HiVA-Driven MBTI Dynamic Agent Generation

This module implements a revolutionary HiVA+MBTI framework that completely replaces 
Six Thinking Hats with intelligent dynamic agent generation. The core system features 
a Task Complexity Analyzer that intelligently scales from 1 MBTI+Domain Expert agent 
for simple tasks to up to 6 different MBTI+Domain Expert collaborative agents for 
complex scenarios.
"""

# Original discovery system components
from ._message import DiscoveryMessage
from ._state import ExplorationState, SurpriseEvent, TemporaryInsight
from ._user_proxy_agent import UserProxyAgent
from ._orchestrator_agent import OrchestratorAgent
from ._exploration_planner_agent import ExplorationPlannerAgent
from ._knowledge_graph_agent import KnowledgeGraphAgent
from ._web_search_agent import WebSearchAgent
from ._verification_agent import VerificationAgent
from ._surprise_assessment_agent import SurpriseAssessmentAgent
from ._insight_generator_agent import InsightGeneratorAgent
from ._meta_analysis_agent import MetaAnalysisAgent
from ._knowledge_infrastructure import VectorDatabase, GraphDatabase
from ._discovery_tools import (
    SearchTool,
    AnalysisTool,
    HypothesisGeneratorTool,
    ConnectionGeneratorTool,
    QuestionGeneratorTool,
    BayesianSurpriseTool,
    DiscoveryTools,
)
from ._workflow import DiscoveryWorkflow

# New HiVA MBTI Dynamic Agent Generation System
from ._task_complexity_analyzer import (
    TaskComplexityAnalyzer,
    ComplexityLevel,
    ComplexityAnalysis,
    DomainRequirement
)
from ._mbti_domain_templates import (
    MBTIDomainExpertTemplate,
    MBTIDomainTemplateRegistry,
    CognitiveFunctionType,
    CognitiveFunctionStack,
    DomainExpertise
)
from ._hiva_evolution_engine import (
    HiVAEvolutionEngine,
    EvolutionType,
    EvolutionEvent
)
from ._dynamic_agent_generator import (
    DynamicAgentGenerator,
    GeneratedAgent,
    AgentGenerationRequest
)
from ._hiva_user_proxy_agent import (
    HiVAUserProxyAgent,
    TaskExecution
)
from ._knowledge_infrastructure import (
    UniversalKnowledgeGraph,
    ContinuousLearningSystem,
    KnowledgeNode,
    LearningPattern
)

__all__ = [
    # Original discovery system
    "DiscoveryMessage",
    "ExplorationState",
    "SurpriseEvent", 
    "TemporaryInsight",
    "UserProxyAgent",
    "OrchestratorAgent",
    "ExplorationPlannerAgent",
    "KnowledgeGraphAgent",
    "WebSearchAgent",
    "VerificationAgent",
    "SurpriseAssessmentAgent",
    "InsightGeneratorAgent",
    "MetaAnalysisAgent",
    "VectorDatabase",
    "GraphDatabase",
    "SearchTool",
    "AnalysisTool",
    "HypothesisGeneratorTool",
    "ConnectionGeneratorTool",
    "QuestionGeneratorTool", 
    "BayesianSurpriseTool",
    "DiscoveryTools",
    "DiscoveryWorkflow",
    
    # HiVA MBTI Dynamic Agent Generation System
    "TaskComplexityAnalyzer",
    "ComplexityLevel",
    "ComplexityAnalysis",
    "DomainRequirement",
    "MBTIDomainExpertTemplate",
    "MBTIDomainTemplateRegistry",
    "CognitiveFunctionType",
    "CognitiveFunctionStack",
    "DomainExpertise",
    "HiVAEvolutionEngine",
    "EvolutionType",
    "EvolutionEvent",
    "DynamicAgentGenerator",
    "GeneratedAgent",
    "AgentGenerationRequest",
    "HiVAUserProxyAgent",
    "TaskExecution",
    "UniversalKnowledgeGraph",
    "ContinuousLearningSystem",
    "KnowledgeNode",
    "LearningPattern",
]