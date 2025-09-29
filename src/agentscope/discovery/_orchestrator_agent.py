# -*- coding: utf-8 -*-
"""OrchestratorAgent - Central coordinator with budget management."""
import asyncio
import time
from typing import Any, Dict, List, Optional, Set
from enum import Enum

from ..agent import ReActAgent
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit
from ..message import Msg
from ._message import DiscoveryMessage, MessageType, BudgetInfo
from ._state import ExplorationState, ExplorationPhase


class CoordinationStrategy(Enum):
    """Strategies for coordinating multiple agents."""
    
    SEQUENTIAL = "sequential"  # One agent at a time
    PARALLEL = "parallel"     # Multiple agents simultaneously
    ADAPTIVE = "adaptive"     # Dynamic based on budget and findings


class OrchestratorAgent(ReActAgent):
    """
    Central coordinator with budget management.
    
    Orchestrates the entire discovery workflow, manages agent coordination,
    tracks budget consumption, and makes termination decisions.
    """
    
    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Optional[Toolkit] = None,
        memory: Optional[MemoryBase] = None,
        coordination_strategy: CoordinationStrategy = CoordinationStrategy.ADAPTIVE,
        budget_check_interval: int = 5,  # Check budget every N operations
        max_parallel_agents: int = 3,
    ) -> None:
        """
        Initialize the OrchestratorAgent.
        
        Args:
            name: Name of the agent
            model: Language model for reasoning
            formatter: Message formatter
            toolkit: Optional toolkit (will create default if None)
            memory: Optional memory (will create default if None)
            coordination_strategy: Strategy for agent coordination
            budget_check_interval: How often to check budget constraints
            max_parallel_agents: Maximum agents to run in parallel
        """
        # Create system prompt for orchestration
        sys_prompt = self._create_orchestration_prompt()
        
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=memory or InMemoryMemory(),
            max_iters=20,  # Allow complex reasoning chains
        )
        
        self.coordination_strategy = coordination_strategy
        self.budget_check_interval = budget_check_interval
        self.max_parallel_agents = max_parallel_agents
        
        # Agent registry and state
        self.registered_agents: Dict[str, Any] = {}
        self.active_tasks: Dict[str, asyncio.Task] = {}
        self.exploration_state: Optional[ExplorationState] = None
        
        # Budget tracking
        self.operation_count = 0
        self.last_budget_check = time.time()
        
        # Register orchestration tools
        self._register_orchestration_tools()
        
        # Register state variables
        self.register_state("coordination_strategy")
        self.register_state("budget_check_interval")
        self.register_state("max_parallel_agents")
    
    def register_agent(self, agent_type: str, agent_instance: Any) -> None:
        """
        Register an agent for coordination.
        
        Args:
            agent_type: Type identifier for the agent
            agent_instance: The agent instance
        """
        self.registered_agents[agent_type] = agent_instance
    
    async def start_discovery_session(self, discovery_msg: DiscoveryMessage) -> Dict[str, Any]:
        """
        Start a new discovery session.
        
        Args:
            discovery_msg: Initial discovery message from UserProxyAgent
            
        Returns:
            Session initialization response
        """
        if discovery_msg.message_type != MessageType.CONTROL_START:
            raise ValueError("Expected CONTROL_START message")
        
        # Extract session parameters
        payload = discovery_msg.payload
        knowledge_base_path = payload["knowledge_base_path"]
        initial_idea = payload["initial_idea"]
        focus_areas = payload.get("focus_areas", [])
        exploration_depth = payload.get("exploration_depth", "normal")
        session_id = payload.get("session_id")
        
        # Initialize exploration state
        self.exploration_state = ExplorationState()
        if session_id:
            self.exploration_state.session_id = session_id
        self.exploration_state.budget_info = discovery_msg.budget_info
        self.exploration_state.phase = ExplorationPhase.INITIALIZATION
        
        # Log session start
        await self.memory.add_msgs([
            Msg(
                name="session_start",
                content=f"Starting discovery session: {initial_idea}",
                role="system",
            )
        ])
        
        # Initialize knowledge base
        initialization_result = await self._initialize_knowledge_base(
            knowledge_base_path, initial_idea, focus_areas
        )
        
        # Move to seeding phase
        self.exploration_state.phase = ExplorationPhase.SEEDING
        
        return {
            "status": "session_started",
            "session_id": self.exploration_state.session_id,
            "initialization": initialization_result,
            "next_phase": ExplorationPhase.ACTIVE_EXPLORATION.value,
        }
    
    async def coordinate_exploration_loop(self) -> Dict[str, Any]:
        """
        Coordinate a single exploration loop.
        
        Returns:
            Results of the exploration loop
        """
        if not self.exploration_state:
            raise RuntimeError("No active exploration session")
        
        # Check budget before starting loop
        if not await self._check_budget_constraints():
            return await self._terminate_session("budget_exhausted")
        
        # Start new loop
        if not self.exploration_state.start_new_loop():
            return await self._terminate_session("loop_limit_reached")
        
        self.exploration_state.phase = ExplorationPhase.ACTIVE_EXPLORATION
        
        # Coordinate agents based on strategy
        loop_results = await self._execute_exploration_strategy()
        
        # Assess loop results and decide next action
        assessment = await self._assess_loop_results(loop_results)
        
        # Update exploration state
        self.exploration_state.exploration_history[-1]["results"] = loop_results
        self.exploration_state.exploration_history[-1]["assessment"] = assessment
        
        # Decide on continuation
        if assessment["should_continue"] and not self.exploration_state.is_budget_exhausted():
            return {
                "status": "loop_completed",
                "loop_number": self.exploration_state.current_loop,
                "results": loop_results,
                "assessment": assessment,
                "next_action": "continue_exploration",
            }
        else:
            return await self._terminate_session("natural_completion")
    
    async def generate_final_insights(self) -> Dict[str, Any]:
        """
        Generate final insights and meta-analysis.
        
        Returns:
            Final insight generation results
        """
        if not self.exploration_state:
            raise RuntimeError("No active exploration session")
        
        self.exploration_state.phase = ExplorationPhase.INSIGHT_GENERATION
        
        # Coordinate insight generation
        insight_tasks = []
        
        if "insight_generator" in self.registered_agents:
            insight_task = self._create_agent_task(
                "insight_generator",
                "generate_insights",
                {
                    "knowledge_graph": self.exploration_state.knowledge_graph,
                    "working_memory": self.exploration_state.working_memory,
                    "surprise_buffer": self.exploration_state.surprise_buffer,
                }
            )
            insight_tasks.append(insight_task)
        
        # Execute insight generation
        insight_results = await asyncio.gather(*insight_tasks, return_exceptions=True)
        
        # Process results
        successful_results = [
            result for result in insight_results 
            if not isinstance(result, Exception)
        ]
        
        # Update exploration state with insights
        for result in successful_results:
            if "insights" in result:
                self.exploration_state.insights.extend(result["insights"])
            if "hypotheses" in result:
                self.exploration_state.hypotheses.extend(result["hypotheses"])
            if "questions" in result:
                self.exploration_state.research_questions.extend(result["questions"])
        
        return {
            "status": "insights_generated",
            "insights_count": len(self.exploration_state.insights),
            "hypotheses_count": len(self.exploration_state.hypotheses),
            "questions_count": len(self.exploration_state.research_questions),
        }
    
    async def perform_meta_analysis(self) -> Dict[str, Any]:
        """
        Perform final meta-analysis and reporting.
        
        Returns:
            Meta-analysis results
        """
        if not self.exploration_state:
            raise RuntimeError("No active exploration session")
        
        self.exploration_state.phase = ExplorationPhase.META_ANALYSIS
        
        # Coordinate meta-analysis
        if "meta_analysis" in self.registered_agents:
            meta_agent = self.registered_agents["meta_analysis"]
            
            meta_results = await self._execute_agent_task(
                meta_agent,
                "perform_meta_analysis",
                {
                    "exploration_state": self.exploration_state,
                    "session_history": self.exploration_state.exploration_history,
                }
            )
            
            # Update exploration state
            self.exploration_state.meta_analysis.update(meta_results)
        
        self.exploration_state.phase = ExplorationPhase.COMPLETE
        
        return {
            "status": "meta_analysis_completed",
            "confidence_assessment": self.exploration_state.meta_analysis.get("confidence_assessment"),
            "knowledge_gaps": self.exploration_state.meta_analysis.get("knowledge_gaps"),
            "future_directions": self.exploration_state.meta_analysis.get("future_directions"),
        }
    
    async def observe(self, msg: Msg | List[Msg] | None) -> None:
        """Observe messages from other agents."""
        if msg is None:
            return
        
        if isinstance(msg, list):
            for m in msg:
                await self._process_observation(m)
        else:
            await self._process_observation(msg)
    
    async def _process_observation(self, msg: Msg) -> None:
        """Process individual observation messages."""
        # Add to memory for context
        await self.memory.add_msgs([msg])
        
        # Check if it's a discovery message
        if msg.name.startswith("discovery_"):
            try:
                discovery_msg = DiscoveryMessage.from_msg(msg)
                await self._handle_discovery_message(discovery_msg)
            except Exception as e:
                print(f"Error processing discovery message: {e}")
    
    async def _handle_discovery_message(self, msg: DiscoveryMessage) -> None:
        """Handle discovery messages from other agents."""
        if msg.message_type == MessageType.EVIDENCE_FOUND:
            await self._process_evidence(msg)
        elif msg.message_type == MessageType.INSIGHT_GENERATE:
            await self._process_insight(msg)
        elif msg.message_type == MessageType.CONTROL_BUDGET_UPDATE:
            await self._update_budget_tracking(msg)
    
    async def _initialize_knowledge_base(
        self, 
        knowledge_base_path: str, 
        initial_idea: str, 
        focus_areas: List[str]
    ) -> Dict[str, Any]:
        """Initialize the knowledge base and graph."""
        if "knowledge_graph" not in self.registered_agents:
            raise RuntimeError("KnowledgeGraphAgent not registered")
        
        kg_agent = self.registered_agents["knowledge_graph"]
        
        result = await self._execute_agent_task(
            kg_agent,
            "initialize_knowledge_base",
            {
                "knowledge_base_path": knowledge_base_path,
                "initial_idea": initial_idea,
                "focus_areas": focus_areas,
            }
        )
        
        # Update exploration state
        if "knowledge_graph" in result:
            self.exploration_state.knowledge_graph = result["knowledge_graph"]
        if "seed_concepts" in result:
            self.exploration_state.exploration_frontier.extend(result["seed_concepts"])
        
        return result
    
    async def _execute_exploration_strategy(self) -> Dict[str, Any]:
        """Execute exploration based on coordination strategy."""
        if self.coordination_strategy == CoordinationStrategy.SEQUENTIAL:
            return await self._execute_sequential_exploration()
        elif self.coordination_strategy == CoordinationStrategy.PARALLEL:
            return await self._execute_parallel_exploration()
        else:  # ADAPTIVE
            return await self._execute_adaptive_exploration()
    
    async def _execute_sequential_exploration(self) -> Dict[str, Any]:
        """Execute exploration sequentially."""
        results = {}
        
        # 1. Plan exploration
        if "exploration_planner" in self.registered_agents:
            plan_result = await self._execute_agent_task(
                self.registered_agents["exploration_planner"],
                "plan_exploration",
                {"current_state": self.exploration_state}
            )
            results["planning"] = plan_result
        
        # 2. Search for information
        if "web_search" in self.registered_agents:
            search_result = await self._execute_agent_task(
                self.registered_agents["web_search"],
                "search_information",
                {"queries": results.get("planning", {}).get("queries", [])}
            )
            results["search"] = search_result
        
        # 3. Verify information
        if "verification" in self.registered_agents:
            verify_result = await self._execute_agent_task(
                self.registered_agents["verification"],
                "verify_information",
                {"evidence": results.get("search", {}).get("evidence", [])}
            )
            results["verification"] = verify_result
        
        # 4. Assess surprise
        if "surprise_assessment" in self.registered_agents:
            surprise_result = await self._execute_agent_task(
                self.registered_agents["surprise_assessment"],
                "assess_surprise",
                {"verified_evidence": results.get("verification", {}).get("verified", [])}
            )
            results["surprise"] = surprise_result
        
        return results
    
    async def _execute_parallel_exploration(self) -> Dict[str, Any]:
        """Execute exploration with parallel agent coordination."""
        tasks = []
        
        # Create parallel tasks for different exploration aspects
        if "exploration_planner" in self.registered_agents:
            tasks.append(self._create_agent_task(
                "exploration_planner", "plan_exploration",
                {"current_state": self.exploration_state}
            ))
        
        if "web_search" in self.registered_agents:
            tasks.append(self._create_agent_task(
                "web_search", "search_information",
                {"exploration_state": self.exploration_state}
            ))
        
        # Execute tasks in parallel
        task_results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        results = {}
        for i, result in enumerate(task_results):
            if not isinstance(result, Exception):
                agent_type = list(self.registered_agents.keys())[i]
                results[agent_type] = result
        
        return results
    
    async def _execute_adaptive_exploration(self) -> Dict[str, Any]:
        """Execute exploration with adaptive strategy based on budget and findings."""
        # Check budget to determine strategy
        if self.exploration_state.is_budget_critical():
            # Use focused sequential approach when budget is low
            return await self._execute_focused_exploration()
        else:
            # Use parallel approach when budget allows
            return await self._execute_parallel_exploration()
    
    async def _execute_focused_exploration(self) -> Dict[str, Any]:
        """Execute focused exploration for budget-critical situations."""
        # Prioritize highest-impact activities
        results = {}
        
        # Focus on surprise assessment of existing findings
        if self.exploration_state.working_memory and "surprise_assessment" in self.registered_agents:
            surprise_result = await self._execute_agent_task(
                self.registered_agents["surprise_assessment"],
                "assess_working_memory",
                {"working_memory": self.exploration_state.working_memory}
            )
            results["focused_surprise"] = surprise_result
        
        return results
    
    async def _create_agent_task(self, agent_type: str, method: str, args: Dict[str, Any]) -> asyncio.Task:
        """Create an asyncio task for agent execution."""
        agent = self.registered_agents[agent_type]
        
        async def task_wrapper():
            try:
                return await self._execute_agent_task(agent, method, args)
            except Exception as e:
                return {"error": str(e), "agent_type": agent_type, "method": method}
        
        return asyncio.create_task(task_wrapper())
    
    async def _execute_agent_task(self, agent: Any, method: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a task on a specific agent."""
        # Update operation count for budget tracking
        self.operation_count += 1
        
        # Check budget periodically
        if self.operation_count % self.budget_check_interval == 0:
            await self._check_budget_constraints()
        
        # Execute the agent method
        if hasattr(agent, method):
            method_func = getattr(agent, method)
            if asyncio.iscoroutinefunction(method_func):
                return await method_func(**args)
            else:
                return method_func(**args)
        else:
            raise AttributeError(f"Agent {type(agent).__name__} has no method '{method}'")
    
    async def _assess_loop_results(self, loop_results: Dict[str, Any]) -> Dict[str, Any]:
        """Assess the results of an exploration loop."""
        assessment = {
            "should_continue": True,
            "confidence": 0.5,
            "new_insights": 0,
            "surprise_level": 0.0,
            "exploration_progress": 0.0,
        }
        
        # Count new insights
        if "insights" in loop_results:
            assessment["new_insights"] = len(loop_results["insights"])
        
        # Calculate surprise level
        if "surprise" in loop_results:
            surprise_data = loop_results["surprise"]
            if "average_surprise" in surprise_data:
                assessment["surprise_level"] = surprise_data["average_surprise"]
        
        # Assess exploration progress
        if self.exploration_state:
            coverage = self.exploration_state.get_exploration_coverage()
            assessment["exploration_progress"] = coverage["coverage_ratio"]
        
        # Determine if should continue
        assessment["should_continue"] = (
            assessment["new_insights"] > 0 or
            assessment["surprise_level"] > 0.3 or
            assessment["exploration_progress"] < 0.8
        )
        
        return assessment
    
    async def _check_budget_constraints(self) -> bool:
        """Check if budget constraints allow continued exploration."""
        if not self.exploration_state:
            return False
        
        current_time = time.time()
        time_elapsed = current_time - self.last_budget_check
        
        # Update time budget
        self.exploration_state.update_budget(time_used=time_elapsed)
        self.last_budget_check = current_time
        
        # Check if budget is exhausted
        if self.exploration_state.is_budget_exhausted():
            return False
        
        # Send budget update to UserProxyAgent
        if self.exploration_state.is_budget_critical():
            budget_msg = DiscoveryMessage.create_control_message(
                control_type=MessageType.CONTROL_BUDGET_UPDATE,
                payload={
                    "status": "critical",
                    "budget_remaining": {
                        "loops": self.exploration_state.budget_info.loops_remaining,
                        "tokens": self.exploration_state.budget_info.tokens_remaining,
                        "time": self.exploration_state.budget_info.time_remaining,
                        "cost": self.exploration_state.budget_info.cost_remaining,
                    }
                },
                budget_info=self.exploration_state.budget_info,
                sender_id=self.id,
            )
            
            # Send to UserProxyAgent (would need reference to send)
            # For now, just log the critical status
            await self.memory.add_msgs([
                Msg(
                    name="budget_critical",
                    content=f"Budget critical: {budget_msg.to_json()}",
                    role="system",
                )
            ])
        
        return True
    
    async def _terminate_session(self, reason: str) -> Dict[str, Any]:
        """Terminate the discovery session."""
        if self.exploration_state:
            self.exploration_state.phase = ExplorationPhase.COMPLETE
        
        termination_result = {
            "status": "session_terminated",
            "reason": reason,
            "session_id": self.exploration_state.session_id if self.exploration_state else None,
            "final_metrics": self._calculate_final_metrics(),
        }
        
        await self.memory.add_msgs([
            Msg(
                name="session_terminated",
                content=f"Session terminated: {reason}",
                role="system",
            )
        ])
        
        return termination_result
    
    def _calculate_final_metrics(self) -> Dict[str, Any]:
        """Calculate final session metrics."""
        if not self.exploration_state:
            return {}
        
        return {
            "loops_completed": self.exploration_state.current_loop,
            "concepts_explored": len(self.exploration_state.visited_concepts),
            "insights_generated": len(self.exploration_state.insights),
            "hypotheses_formed": len(self.exploration_state.hypotheses),
            "surprise_events": len(self.exploration_state.surprise_buffer),
            "exploration_coverage": self.exploration_state.get_exploration_coverage(),
        }
    
    async def _process_evidence(self, msg: DiscoveryMessage) -> None:
        """Process evidence messages from other agents."""
        # Add evidence to exploration state if high confidence
        evidence = msg.payload.get("evidence", {})
        confidence = msg.payload.get("confidence", 0.0)
        
        if confidence >= 0.7:  # High confidence threshold
            evidence_id = msg.task_id
            self.exploration_state.verified_knowledge[evidence_id] = {
                "evidence": evidence,
                "confidence": confidence,
                "source": msg.sender_id,
                "timestamp": msg.timestamp,
            }
    
    async def _process_insight(self, msg: DiscoveryMessage) -> None:
        """Process insight messages from other agents."""
        # Add insights to exploration state
        insight_data = {
            "insight": msg.payload.get("insight", ""),
            "hypothesis": msg.payload.get("hypothesis"),
            "connections": msg.payload.get("connections", []),
            "novelty_score": msg.payload.get("novelty_score", 0.0),
            "confidence": msg.payload.get("confidence", 0.0),
            "source": msg.sender_id,
            "timestamp": msg.timestamp,
        }
        
        self.exploration_state.insights.append(insight_data)
    
    async def _update_budget_tracking(self, msg: DiscoveryMessage) -> None:
        """Update budget tracking from agent reports."""
        payload = msg.payload
        
        self.exploration_state.update_budget(
            tokens_used=payload.get("tokens_used", 0),
            time_used=payload.get("time_used", 0.0),
            cost_used=payload.get("cost_used", 0.0),
        )
    
    def _register_orchestration_tools(self) -> None:
        """Register tools specific to orchestration."""
        # Tools for budget management, agent coordination, etc.
        # Would be implemented based on specific toolkit requirements
        pass
    
    def _create_orchestration_prompt(self) -> str:
        """Create system prompt for orchestration reasoning."""
        return """You are the OrchestratorAgent in an Agent Discovery System. Your role is to:

1. Coordinate multiple specialized agents for knowledge discovery
2. Manage budget constraints (loops, tokens, time, cost)
3. Make decisions about exploration strategies
4. Assess when to continue or terminate exploration
5. Ensure efficient resource utilization

Key principles:
- Prioritize high-surprise discoveries
- Balance exploration breadth vs depth
- Adapt strategy based on remaining budget
- Coordinate agents to avoid redundant work
- Make termination decisions based on diminishing returns

Always consider budget implications of your decisions and coordinate agents efficiently."""