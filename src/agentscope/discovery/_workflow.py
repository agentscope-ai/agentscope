# -*- coding: utf-8 -*-
"""Main discovery workflow orchestration."""
import asyncio
from typing import Any, Dict, List, Optional
import time

from ..model import ChatModelBase
from ..formatter import FormatterBase
from ._user_proxy_agent import UserProxyAgent
from ._orchestrator_agent import OrchestratorAgent
from ._knowledge_graph_agent import KnowledgeGraphAgent
from ._message import DiscoveryMessage, MessageType


class DiscoveryWorkflow:
    """
    Main orchestrator for the Agent Discovery System workflow.
    
    Coordinates the entire discovery process from initialization to final results.
    """
    
    def __init__(
        self,
        model: ChatModelBase,
        formatter: FormatterBase,
        storage_path: str = "./discovery_storage",
        max_loops: int = 5,
        token_budget: int = 20000,
        time_budget: float = 3600.0,
        cost_budget: float = 10.0,
    ) -> None:
        """
        Initialize the discovery workflow.
        
        Args:
            model: Language model for agents
            formatter: Message formatter
            storage_path: Path for storing session data
            max_loops: Maximum exploration loops
            token_budget: Token budget for the session
            time_budget: Time budget in seconds
            cost_budget: Cost budget for external services
        """
        self.model = model
        self.formatter = formatter
        self.storage_path = storage_path
        
        # Initialize agents
        self.user_proxy = UserProxyAgent(
            name="UserProxy",
            max_loops=max_loops,
            token_budget=token_budget,
            time_budget=time_budget,
            cost_budget=cost_budget,
            session_save_path=storage_path,
        )
        
        self.orchestrator = OrchestratorAgent(
            name="Orchestrator",
            model=model,
            formatter=formatter,
        )
        
        self.knowledge_graph_agent = KnowledgeGraphAgent(
            name="KnowledgeGraph",
            model=model,
            formatter=formatter,
            storage_base_path=f"{storage_path}/knowledge",
        )
        
        # Register agents with orchestrator
        self.orchestrator.register_agent("knowledge_graph", self.knowledge_graph_agent)
        
        # Mock agents for demonstration (would be implemented fully)
        self._register_mock_agents()
        
        self.session_active = False
        self.current_session_id = None
    
    async def start_discovery(
        self,
        knowledge_base_path: str,
        initial_idea: str,
        focus_areas: Optional[List[str]] = None,
        exploration_depth: str = "normal",
    ) -> Dict[str, Any]:
        """
        Start a discovery session.
        
        Args:
            knowledge_base_path: Path to user's knowledge base
            initial_idea: Starting idea or question
            focus_areas: Optional focus areas
            exploration_depth: "shallow", "normal", or "deep"
            
        Returns:
            Session initialization results
        """
        if self.session_active:
            raise RuntimeError("A discovery session is already active")
        
        # Start session with UserProxyAgent
        session_info = await self.user_proxy.start_discovery_session(
            knowledge_base_path=knowledge_base_path,
            initial_idea=initial_idea,
            focus_areas=focus_areas or [],
            exploration_depth=exploration_depth,
        )
        
        self.current_session_id = session_info["session_id"]
        self.session_active = True
        
        # Extract discovery message for orchestrator
        discovery_msg = DiscoveryMessage.from_dict(session_info["message"])
        
        # Initialize session with orchestrator
        orchestration_result = await self.orchestrator.start_discovery_session(discovery_msg)
        
        return {
            "session_id": self.current_session_id,
            "status": "started",
            "user_proxy_result": session_info,
            "orchestrator_result": orchestration_result,
        }
    
    async def run_exploration_loop(self) -> Dict[str, Any]:
        """
        Run a single exploration loop.
        
        Returns:
            Loop execution results
        """
        if not self.session_active:
            raise RuntimeError("No active discovery session")
        
        # Execute exploration loop through orchestrator
        loop_result = await self.orchestrator.coordinate_exploration_loop()
        
        # Check if session should terminate
        if loop_result["status"] == "session_terminated":
            self.session_active = False
        
        return loop_result
    
    async def run_full_discovery(
        self,
        knowledge_base_path: str,
        initial_idea: str,
        focus_areas: Optional[List[str]] = None,
        exploration_depth: str = "normal",
    ) -> Dict[str, Any]:
        """
        Run complete discovery workflow from start to finish.
        
        Args:
            knowledge_base_path: Path to user's knowledge base
            initial_idea: Starting idea or question
            focus_areas: Optional focus areas
            exploration_depth: "shallow", "normal", or "deep"
            
        Returns:
            Complete discovery results
        """
        # Start discovery session
        start_result = await self.start_discovery(
            knowledge_base_path=knowledge_base_path,
            initial_idea=initial_idea,
            focus_areas=focus_areas,
            exploration_depth=exploration_depth,
        )
        
        print(f"Discovery session started: {start_result['session_id']}")
        
        # Run exploration loops until completion
        loop_count = 0
        loop_results = []
        
        while self.session_active:
            loop_count += 1
            print(f"Running exploration loop {loop_count}...")
            
            loop_result = await self.run_exploration_loop()
            loop_results.append(loop_result)
            
            # Check termination conditions
            if loop_result["status"] == "session_terminated":
                print(f"Session terminated: {loop_result['reason']}")
                break
            
            # Add small delay between loops
            await asyncio.sleep(1.0)
        
        # Generate final insights
        print("Generating final insights...")
        insight_result = await self.orchestrator.generate_final_insights()
        
        # Perform meta-analysis
        print("Performing meta-analysis...")
        meta_result = await self.orchestrator.perform_meta_analysis()
        
        # Get final results from UserProxy
        final_results = await self.user_proxy.get_final_results()
        
        return {
            "session_id": self.current_session_id,
            "start_result": start_result,
            "exploration_loops": loop_results,
            "insight_generation": insight_result,
            "meta_analysis": meta_result,
            "final_results": final_results,
        }
    
    async def get_session_status(self) -> Dict[str, Any]:
        """Get current session status."""
        if not self.session_active:
            return {"status": "no_active_session"}
        
        return await self.user_proxy.get_session_status()
    
    async def terminate_session(self, reason: str = "user_request") -> Dict[str, Any]:
        """Terminate current session early."""
        if not self.session_active:
            return {"status": "no_active_session"}
        
        termination_result = await self.user_proxy.request_termination(reason)
        self.session_active = False
        
        return termination_result
    
    def _register_mock_agents(self) -> None:
        """Register mock agents for demonstration purposes."""
        # In a full implementation, these would be real agent instances
        
        class MockAgent:
            def __init__(self, agent_type: str):
                self.agent_type = agent_type
            
            async def plan_exploration(self, **kwargs):
                return {
                    "queries": [f"Mock query from {self.agent_type}"],
                    "exploration_strategy": "mock",
                }
            
            async def search_information(self, **kwargs):
                return {
                    "evidence": [f"Mock evidence from {self.agent_type}"],
                    "sources": ["mock_source"],
                }
            
            async def verify_information(self, **kwargs):
                return {
                    "verified": [f"Mock verified info from {self.agent_type}"],
                    "confidence_scores": [0.8],
                }
            
            async def assess_surprise(self, **kwargs):
                return {
                    "surprise_events": [f"Mock surprise from {self.agent_type}"],
                    "average_surprise": 0.6,
                }
            
            async def generate_insights(self, **kwargs):
                return {
                    "insights": [f"Mock insight from {self.agent_type}"],
                    "hypotheses": [f"Mock hypothesis from {self.agent_type}"],
                    "questions": [f"Mock question from {self.agent_type}"],
                }
            
            async def perform_meta_analysis(self, **kwargs):
                return {
                    "confidence_assessment": {"overall_score": 0.7},
                    "knowledge_gaps": ["mock_gap_1", "mock_gap_2"],
                    "future_directions": ["mock_direction_1"],
                }
        
        # Register mock agents
        self.orchestrator.register_agent("exploration_planner", MockAgent("exploration_planner"))
        self.orchestrator.register_agent("web_search", MockAgent("web_search"))
        self.orchestrator.register_agent("verification", MockAgent("verification"))
        self.orchestrator.register_agent("surprise_assessment", MockAgent("surprise_assessment"))
        self.orchestrator.register_agent("insight_generator", MockAgent("insight_generator"))
        self.orchestrator.register_agent("meta_analysis", MockAgent("meta_analysis"))


async def create_discovery_workflow(
    model: ChatModelBase,
    formatter: FormatterBase,
    **kwargs
) -> DiscoveryWorkflow:
    """
    Factory function to create a discovery workflow.
    
    Args:
        model: Language model for agents
        formatter: Message formatter
        **kwargs: Additional configuration options
        
    Returns:
        Configured DiscoveryWorkflow instance
    """
    return DiscoveryWorkflow(
        model=model,
        formatter=formatter,
        **kwargs
    )