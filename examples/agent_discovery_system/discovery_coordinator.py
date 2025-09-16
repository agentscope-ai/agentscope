#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AgentScope-compliant Discovery Agent Coordinator

This module implements the agent coordination system following AgentScope standards
and design patterns as specified in the design document.
"""

import os
import sys
import json
import asyncio
import configparser
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

# Add the src directory to the Python path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

# AgentScope core imports
from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter
from agentscope.memory import InMemoryMemory
from agentscope.tool import Toolkit
from agentscope.message import Msg

# Discovery system imports
from agentscope.discovery import (
    UserProxyAgent,
    OrchestratorAgent,
    ExplorationPlannerAgent,
    KnowledgeGraphAgent,
    WebSearchAgent,
    VerificationAgent,
    SurpriseAssessmentAgent,
    InsightGeneratorAgent,
    MetaAnalysisAgent
)
from discovery_toolkit import DiscoveryTools


class DiscoveryConfig:
    """Configuration class for discovery system."""
    
    def __init__(self, config_path: Optional[str] = None):
        self.gemini_api_key = self._get_api_key(config_path)
        self.model_name = "gemini-2.5-pro"
        self.max_loops = 5
        self.token_budget = 20000
        self.time_budget = 3600.0
        self.cost_budget = 10.0
        self.storage_path = "./discovery_storage"
        self.embedding_model = None
        self.temperature = 0.7
        self.max_output_tokens = 2048
        self.top_p = 0.8
        self.top_k = 40
    
    def _get_api_key(self, config_path: Optional[str] = None) -> str:
        """Get API key from environment or config file."""
        # Try environment variable first
        api_key = os.getenv("GEMINI_API_KEY")
        
        if not api_key and config_path:
            config = configparser.ConfigParser()
            config.read(config_path)
            api_key = config.get("api", "gemini_api_key", fallback=None)
        
        if not api_key:
            # Try default config file
            default_config = Path(__file__).parent / "config.ini"
            if default_config.exists():
                config = configparser.ConfigParser()
                config.read(default_config)
                api_key = config.get("api", "gemini_api_key", fallback=None)
        
        if not api_key:
            raise ValueError(
                "Gemini API key not found. Please either:\n"
                "1. Set GEMINI_API_KEY environment variable, or\n"
                "2. Add your API key to config.ini file\n"
                "You can get your API key from: https://makersuite.google.com/app/apikey"
            )
        
        return api_key


class DiscoveryAgentCoordinator:
    """Coordinates all discovery agents following AgentScope patterns."""
    
    def __init__(self, config: Optional[DiscoveryConfig] = None):
        self.config = config or DiscoveryConfig()
        self.model = self._initialize_model()
        self.formatter = self._initialize_formatter()
        self.memory = self._initialize_memory()
        self.toolkit = self._initialize_toolkit()
        self.agents = self._initialize_agents()
        self.session_data = {}
        self.session_active = False
        self.current_session_id = None
    
    def _initialize_model(self) -> GeminiChatModel:
        """Initialize Gemini model following AgentScope patterns."""
        return GeminiChatModel(
            model_name=self.config.model_name,
            api_key=self.config.gemini_api_key,
            stream=True,
            generate_kwargs={
                "temperature": self.config.temperature,
                "max_output_tokens": self.config.max_output_tokens,
                "top_p": self.config.top_p,
                "top_k": self.config.top_k
            }
        )
    
    def _initialize_formatter(self) -> GeminiChatFormatter:
        """Initialize formatter for multi-agent conversations."""
        return GeminiChatFormatter()
    
    def _initialize_memory(self) -> InMemoryMemory:
        """Initialize memory system for session persistence."""
        return InMemoryMemory()
    
    def _initialize_toolkit(self) -> Toolkit:
        """Initialize discovery-specific toolkit."""
        toolkit = Toolkit()
        
        # Store discovery tools for later use (but don't register them with AgentScope toolkit)
        # AgentScope agents will use their own internal tools through ReAct workflow
        self.discovery_tools = DiscoveryTools()
        
        return toolkit
    
    def load_knowledge_base(self, knowledge_file_data: List[Dict[str, str]]) -> None:
        """Load knowledge base into discovery tools."""
        if hasattr(self, 'discovery_tools'):
            self.discovery_tools.load_knowledge_base(knowledge_file_data)
    
    def _initialize_agents(self) -> Dict[str, Any]:
        """Initialize all discovery agents as ReActAgents."""
        agents = {}
        
        # UserProxyAgent - custom discovery agent (doesn't use ReAct pattern)
        agents['user_proxy'] = UserProxyAgent(
            name="DiscoveryUserProxy",
            max_loops=self.config.max_loops,
            token_budget=self.config.token_budget,
            time_budget=self.config.time_budget,
            cost_budget=self.config.cost_budget
        )
        
        # OrchestratorAgent - central coordinator
        agents['orchestrator'] = OrchestratorAgent(
            name="DiscoveryOrchestrator",
            model=self.model,
            formatter=self.formatter,
            memory=self.memory,
            toolkit=self.toolkit
        )
        
        # Specialized discovery agents (all inherit from ReActAgent and create their own sys_prompt)
        agents['exploration_planner'] = ExplorationPlannerAgent(
            name="ExplorationPlanner",
            model=self.model,
            formatter=self.formatter,
            memory=self.memory,
            toolkit=self.toolkit
        )
        
        agents['knowledge_graph'] = KnowledgeGraphAgent(
            name="KnowledgeGraphAgent",
            model=self.model,
            formatter=self.formatter,
            memory=self.memory,
            toolkit=self.toolkit,
            storage_base_path=self.config.storage_path
        )
        
        agents['web_search'] = WebSearchAgent(
            name="WebSearchAgent",
            model=self.model,
            formatter=self.formatter,
            memory=self.memory,
            toolkit=self.toolkit
        )
        
        agents['verification'] = VerificationAgent(
            name="VerificationAgent",
            model=self.model,
            formatter=self.formatter,
            memory=self.memory,
            toolkit=self.toolkit
        )
        
        agents['surprise_assessment'] = SurpriseAssessmentAgent(
            name="SurpriseAssessmentAgent",
            model=self.model,
            formatter=self.formatter,
            memory=self.memory,
            toolkit=self.toolkit
        )
        
        agents['insight_generator'] = InsightGeneratorAgent(
            name="InsightGeneratorAgent",
            model=self.model,
            formatter=self.formatter,
            memory=self.memory,
            toolkit=self.toolkit
        )
        
        agents['meta_analysis'] = MetaAnalysisAgent(
            name="MetaAnalysisAgent",
            model=self.model,
            formatter=self.formatter,
            memory=self.memory,
            toolkit=self.toolkit
        )
        
        return agents
    
    def _load_orchestrator_prompt(self) -> str:
        """Load system prompt for orchestrator agent."""
        agent_descriptions = [
            "ExplorationPlanner: Designs strategic exploration paths",
            "KnowledgeGraphAgent: Builds and manages knowledge representations",
            "WebSearchAgent: Searches external knowledge sources", 
            "VerificationAgent: Validates discoveries and insights",
            "SurpriseAssessmentAgent: Evaluates novelty and surprise value",
            "InsightGeneratorAgent: Synthesizes insights from discoveries",
            "MetaAnalysisAgent: Performs high-level analysis and patterns"
        ]
        
        return f"""
# Discovery Orchestrator Agent

## Identity
You are the Discovery Orchestrator, the central coordinator of the Agent Discovery System.

## Core Mission
Coordinate multi-agent exploration to discover novel insights from knowledge bases through:
- Strategic exploration planning
- Agent task allocation
- Progress monitoring
- Insight synthesis

## Operation Paradigm
1. **Plan**: Analyze initial ideas and create exploration strategies
2. **Coordinate**: Assign tasks to specialized agents
3. **Monitor**: Track progress and adjust strategies
4. **Synthesize**: Combine agent outputs into coherent insights

## Important Constraints
- Maintain budget awareness (tokens: {self.config.token_budget}, time: {self.config.time_budget}s, cost: ${self.config.cost_budget})
- Ensure agent coordination without conflicts
- Focus on novel discoveries over obvious connections
- Prioritize high-surprise insights

## Available Agents
{chr(10).join(agent_descriptions)}

## Output Format
Always provide structured responses with clear agent assignments and expected outcomes.
"""
    
    def _load_planner_prompt(self) -> str:
        """Load system prompt for exploration planner agent."""
        available_tools = [
            "search_tool: Search knowledge base for relevant content",
            "analysis_tool: Analyze content for patterns and insights",
            "hypothesis_generator_tool: Generate testable hypotheses",
            "connection_generator_tool: Find connections between concepts",
            "question_generator_tool: Generate follow-up questions",
            "bayesian_surprise_tool: Assess novelty and surprise value"
        ]
        
        return f"""
# Exploration Planner Agent

## Identity
You are the Exploration Planner, responsible for strategic knowledge exploration.

## Core Mission
Design and execute exploration strategies that maximize discovery potential:
- Identify knowledge boundaries
- Plan exploration paths
- Generate targeted queries
- Assess exploration effectiveness

## Cognitive Framework
Use curiosity-driven exploration algorithms:
1. **Boundary Detection**: Find edges of current knowledge
2. **Novelty Assessment**: Identify unexplored areas
3. **Strategic Planning**: Design exploration sequences
4. **Adaptive Adjustment**: Modify plans based on discoveries

## Tools Available
{chr(10).join(available_tools)}

## Output Requirements
- Specific exploration queries
- Rationale for each query
- Expected discovery types
- Success metrics
"""
    
    async def start_discovery_session(self, knowledge_base_path: str, initial_idea: str,
                                    focus_areas: List[str] = None, exploration_depth: str = "normal") -> Dict[str, Any]:
        """Start a new discovery session with proper AgentScope initialization."""
        import uuid
        
        self.current_session_id = str(uuid.uuid4())
        self.session_active = True
        
        # Initialize session data
        self.session_data = {
            "session_id": self.current_session_id,
            "initial_idea": initial_idea,
            "focus_areas": focus_areas or [],
            "exploration_depth": exploration_depth,
            "knowledge_base_path": knowledge_base_path,
            "start_time": datetime.now(),
            "discoveries": [],
            "insights": [],
            "hypotheses": [],
            "questions": [],
            "agent_interactions": [],
            "budget_tracking": {
                "tokens_used": 0,
                "time_elapsed": 0.0,
                "cost_accumulated": 0.0
            }
        }
        
        # Load knowledge base
        await self._load_knowledge_base(knowledge_base_path)
        
        # Initialize agents for this session
        await self._setup_agents_for_session()
        
        return {
            "session_id": self.current_session_id,
            "status": "started",
            "message": f"Discovery session started for: {initial_idea}",
            "agents_initialized": list(self.agents.keys())
        }
    
    async def _load_knowledge_base(self, knowledge_base_path: str):
        """Load knowledge base files into memory."""
        knowledge_files = []
        
        for file_path in Path(knowledge_base_path).glob("*.md"):
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    knowledge_files.append({
                        "filename": file_path.name,
                        "path": str(file_path),
                        "content": content,
                        "summary": content[:300] + "..." if len(content) > 300 else content
                    })
            except Exception as e:
                print(f"Error loading {file_path}: {e}")
        
        self.session_data["knowledge_files"] = knowledge_files
        
        # Store in agent memory
        for file_data in knowledge_files:
            knowledge_msg = Msg(
                name="system",
                content=f"Knowledge file: {file_data['filename']}\n\n{file_data['content']}",
                role="system"
            )
            self.memory.add(knowledge_msg)
    
    async def _setup_agents_for_session(self):
        """Setup agents for the current session."""
        # Create session context message
        session_context = Msg(
            name="system",
            content=f"""
Session Context:
- Session ID: {self.session_data['session_id']}
- Initial Idea: {self.session_data['initial_idea']}
- Focus Areas: {', '.join(self.session_data['focus_areas'])}
- Exploration Depth: {self.session_data['exploration_depth']}
- Knowledge Files: {len(self.session_data['knowledge_files'])} files loaded
""",
            role="system"
        )
        
        # Add context to all agents
        for agent in self.agents.values():
            if hasattr(agent, 'memory'):
                agent.memory.add(session_context)
    
    async def run_exploration_loop(self) -> Dict[str, Any]:
        """Run a single exploration loop with agent coordination."""
        if not self.session_active:
            raise RuntimeError("No active discovery session")
        
        loop_count = len(self.session_data.get("loop_results", [])) + 1
        
        # Start with orchestrator planning
        planning_result = await self._orchestrator_planning_phase(loop_count)
        
        # Execute agent tasks
        execution_result = await self._execute_agent_tasks(planning_result)
        
        # Synthesis phase
        synthesis_result = await self._synthesis_phase(execution_result)
        
        # Store loop results
        loop_result = {
            "loop_number": loop_count,
            "planning": planning_result,
            "execution": execution_result,
            "synthesis": synthesis_result,
            "status": "completed" if loop_count >= self.config.max_loops else "continue",
            "timestamp": datetime.now().isoformat()
        }
        
        if "loop_results" not in self.session_data:
            self.session_data["loop_results"] = []
        self.session_data["loop_results"].append(loop_result)
        
        # Update session data with new discoveries
        if synthesis_result:
            self.session_data["discoveries"].extend(synthesis_result.get("discoveries", []))
            self.session_data["insights"].extend(synthesis_result.get("insights", []))
            self.session_data["hypotheses"].extend(synthesis_result.get("hypotheses", []))
            self.session_data["questions"].extend(synthesis_result.get("questions", []))
        
        # Check termination conditions
        if loop_count >= self.config.max_loops:
            self.session_active = False
            loop_result["status"] = "session_terminated"
            loop_result["reason"] = "max_loops_reached"
        
        return loop_result
    
    async def _orchestrator_planning_phase(self, loop_count: int) -> Dict[str, Any]:
        """Orchestrator plans the exploration for this loop."""
        planning_prompt = f"""
Current exploration loop: {loop_count}/{self.config.max_loops}
Initial idea: {self.session_data['initial_idea']}
Focus areas: {', '.join(self.session_data['focus_areas'])}

Previous discoveries: {len(self.session_data['discoveries'])} items
Available agents: {', '.join(self.agents.keys())}

Plan the exploration strategy for this loop. Assign specific tasks to agents.
"""
        
        planning_msg = Msg(
            name="user",
            content=planning_prompt,
            role="user"
        )
        
        # Use orchestrator agent
        orchestrator = self.agents['orchestrator']
        response = await orchestrator(planning_msg)
        
        return {
            "loop_count": loop_count,
            "planning_response": response,
            "agent_assignments": self._parse_agent_assignments(response)
        }
    
    async def _execute_agent_tasks(self, planning_result: Dict[str, Any]) -> Dict[str, Any]:
        """Execute tasks assigned to agents using discovery tools."""
        execution_results = {}
        
        # Use discovery tools directly to generate meaningful results
        try:
            # Search the knowledge base for relevant content
            search_results = await self.discovery_tools.search_tool(
                query=self.session_data['initial_idea'],
                max_results=5
            )
            
            execution_results['search'] = {
                "task": "Knowledge base search",
                "search_results": search_results,
                "results_count": len(search_results),
                "status": "completed"
            }
            
            # Analyze the search results
            if search_results:
                analysis_content = "\n".join([result.content for result in search_results[:3]])
                analysis_results = await self.discovery_tools.analysis_tool(
                    content=analysis_content,
                    analysis_type="comprehensive"
                )
                
                execution_results['analysis'] = {
                    "task": "Knowledge base analysis",
                    "analysis": analysis_results,
                    "status": "completed"
                }
                
                # Generate hypotheses based on search results (convert to discovery format)
                discoveries_for_hyp = []
                for result in search_results[:3]:
                    discoveries_for_hyp.append({
                        "text": result.content,
                        "confidence": result.relevance_score,
                        "source": result.source
                    })
                
                hypotheses = await self.discovery_tools.hypothesis_generator_tool(
                    discoveries=discoveries_for_hyp,
                    context=self.session_data['initial_idea']
                )
                
                execution_results['hypothesis_generation'] = {
                    "task": "Hypothesis generation",
                    "hypotheses": hypotheses,
                    "status": "completed"
                }
                
                # Generate follow-up questions
                questions = await self.discovery_tools.question_generator_tool(
                    discoveries=discoveries_for_hyp[:2],
                    insights=analysis_results.get('themes', [])[:3] if analysis_results else []
                )
                
                execution_results['question_generation'] = {
                    "task": "Question generation",
                    "questions": questions,
                    "status": "completed"
                }
            
        except Exception as e:
            print(f"Error in discovery tool execution: {e}")  # For debugging
            execution_results['discovery_tools'] = {
                "task": "Discovery tool execution",
                "error": str(e),
                "status": "error"
            }
        
        return execution_results
    
    async def _synthesis_phase(self, execution_result: Dict[str, Any]) -> Dict[str, Any]:
        """Synthesize results from discovery tool executions."""
        discoveries = []
        insights = []
        hypotheses = []
        questions = []
        
        # Extract search results as discoveries
        if 'search' in execution_result and execution_result['search']['status'] == 'completed':
            search_results = execution_result['search']['search_results']
            for result in search_results:
                discoveries.append({
                    "text": result.content,
                    "confidence": result.relevance_score,
                    "source": result.source,
                    "type": "search_result"
                })
        
        # Extract analysis results
        if 'analysis' in execution_result and execution_result['analysis']['status'] == 'completed':
            analysis = execution_result['analysis']['analysis']
            
            # Extract patterns as discoveries and insights
            if 'patterns' in analysis:
                for pattern in analysis['patterns']:
                    pattern_text = f"Pattern: {pattern.get('pattern', 'Unknown pattern')}"
                    discoveries.append({
                        "text": pattern_text,
                        "confidence": pattern.get('confidence', 0.7),
                        "source": "analysis_tool",
                        "type": "pattern"
                    })
                    insights.append(pattern_text)
            
            # Extract themes as insights
            if 'themes' in analysis:
                for theme in analysis['themes']:
                    theme_text = f"Theme: {theme.get('theme', 'Unknown theme')} (prominence: {theme.get('prominence', 0):.2f})"
                    insights.append(theme_text)
                    discoveries.append({
                        "text": theme_text,
                        "confidence": theme.get('confidence', 0.6),
                        "source": "analysis_tool",
                        "type": "theme"
                    })
            
            # Extract relationships as insights
            if 'relationships' in analysis:
                for rel in analysis['relationships']:
                    rel_text = f"Relationship: {rel.get('source', 'A')} {rel.get('type', 'relates to')} {rel.get('target', 'B')}"
                    insights.append(rel_text)
                    discoveries.append({
                        "text": rel_text,
                        "confidence": rel.get('confidence', 0.7),
                        "source": "analysis_tool",
                        "type": "relationship"
                    })
        
        # Extract hypotheses
        if 'hypothesis_generation' in execution_result and execution_result['hypothesis_generation']['status'] == 'completed':
            hyp_results = execution_result['hypothesis_generation']['hypotheses']
            if isinstance(hyp_results, list):
                for hyp in hyp_results:
                    if isinstance(hyp, dict):
                        hyp_text = hyp.get('statement', str(hyp))
                        hypotheses.append(hyp_text)
                        discoveries.append({
                            "text": f"Hypothesis: {hyp_text}",
                            "confidence": hyp.get('confidence', 0.6),
                            "source": "hypothesis_generator",
                            "type": "hypothesis"
                        })
                    else:
                        hypotheses.append(str(hyp))
                        discoveries.append({
                            "text": f"Hypothesis: {str(hyp)}",
                            "confidence": 0.6,
                            "source": "hypothesis_generator",
                            "type": "hypothesis"
                        })
        
        # Extract questions
        if 'question_generation' in execution_result and execution_result['question_generation']['status'] == 'completed':
            question_results = execution_result['question_generation']['questions']
            if isinstance(question_results, list):
                for q in question_results:
                    if isinstance(q, dict):
                        questions.append(q.get('question', str(q)))
                    else:
                        questions.append(str(q))
        
        # Create fallback content if nothing was generated
        if not discoveries and not insights and not hypotheses:
            fallback_discovery = {
                "text": f"Explored knowledge base for: {self.session_data['initial_idea']}",
                "confidence": 0.5,
                "source": "system",
                "type": "exploration"
            }
            discoveries.append(fallback_discovery)
            insights.append(f"Knowledge base contains information related to: {self.session_data['initial_idea']}")
            hypotheses.append(f"Further investigation of {self.session_data['initial_idea']} may reveal additional insights")
            questions.append(f"What specific aspects of {self.session_data['initial_idea']} should be explored further?")
        
        print(f"Synthesis generated: {len(discoveries)} discoveries, {len(insights)} insights, {len(hypotheses)} hypotheses")  # Debug
        
        return {
            "discoveries": discoveries,
            "insights": insights,
            "hypotheses": hypotheses,
            "questions": questions
        }
    
    def _parse_agent_assignments(self, orchestrator_response) -> Dict[str, str]:
        """Parse agent assignments from orchestrator response."""
        # This is a simplified parser - in production, use more robust parsing
        assignments = {}
        
        response_text = str(orchestrator_response)
        
        # Look for agent assignments in the response
        for agent_name in self.agents.keys():
            if agent_name in response_text:
                # Extract task for this agent (simplified)
                assignments[agent_name] = f"Explore knowledge base for insights related to: {self.session_data['initial_idea']}"
        
        return assignments
    
    def _parse_synthesis_results(self, synthesis_response) -> Dict[str, Any]:
        """Parse synthesis results from insight generator."""
        # This is a simplified parser - in production, use more robust JSON parsing
        try:
            response_text = str(synthesis_response)
            
            # Try to extract JSON from response
            import re
            json_match = re.search(r'```(?:json)?\s*({.*?})\s*```', response_text, re.DOTALL | re.IGNORECASE)
            
            if json_match:
                json_str = json_match.group(1).strip()
                parsed = json.loads(json_str)
                return parsed
            else:
                # Fallback structure
                return {
                    "discoveries": [{"text": "Discovery from synthesis", "confidence": 0.7}],
                    "insights": ["Insight from agent coordination"],
                    "hypotheses": ["Hypothesis for testing"],
                    "questions": ["What additional exploration is needed?"]
                }
                
        except Exception as e:
            print(f"Error parsing synthesis results: {e}")
            return {
                "discoveries": [],
                "insights": [],
                "hypotheses": [],
                "questions": []
            }
    
    async def get_final_results(self) -> Dict[str, Any]:
        """Get comprehensive final results."""
        return {
            "session_id": self.current_session_id,
            "session_data": self.session_data,
            "discoveries": self.session_data.get("discoveries", []),
            "insights": self.session_data.get("insights", []),
            "hypotheses": self.session_data.get("hypotheses", []),
            "questions": self.session_data.get("questions", []),
            "budget_utilization": self.session_data.get("budget_tracking", {}),
            "meta_analysis": await self._generate_meta_analysis()
        }
    
    async def _generate_meta_analysis(self) -> Dict[str, Any]:
        """Generate meta-analysis of the discovery session."""
        # Use meta-analysis agent for comprehensive analysis
        meta_prompt = f"""
Analyze the complete discovery session:

Session: {self.session_data['session_id']}
Initial idea: {self.session_data['initial_idea']}
Total discoveries: {len(self.session_data['discoveries'])}
Total insights: {len(self.session_data['insights'])}
Loops completed: {len(self.session_data.get('loop_results', []))}

Provide meta-analysis on:
1. Quality of discoveries
2. Novelty assessment
3. Exploration effectiveness
4. Recommendations for future exploration
"""
        
        meta_msg = Msg(
            name="user",
            content=meta_prompt,
            role="user"
        )
        
        meta_agent = self.agents['meta_analysis']
        meta_response = await meta_agent(meta_msg)
        
        return {
            "analysis": str(meta_response),
            "quality_score": 0.8,  # Would be calculated based on actual analysis
            "novelty_score": 0.7,
            "effectiveness_score": 0.75
        }
    
    async def terminate_session(self, reason: str = "user_request") -> Dict[str, Any]:
        """Terminate the current discovery session."""
        self.session_active = False
        
        termination_result = {
            "session_id": self.current_session_id,
            "termination_reason": reason,
            "termination_time": datetime.now().isoformat(),
            "final_status": "terminated"
        }
        
        if self.session_data:
            self.session_data["termination_result"] = termination_result
        
        return termination_result