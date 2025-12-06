# -*- coding: utf-8 -*-
"""UserProxyAgent - Interface between user and discovery system."""
import os
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import time

from ..agent import AgentBase
from ..message import Msg
from ._message import DiscoveryMessage, MessageType, BudgetInfo
from ._state import ExplorationState, ExplorationPhase


class UserProxyAgent(AgentBase):
    """
    Interface between user and discovery system.
    
    Handles user input, budget control, and result presentation.
    Acts as the entry point for discovery sessions.
    """
    
    def __init__(
        self,
        name: str = "UserProxy",
        max_loops: int = 5,
        token_budget: int = 20000,
        time_budget: float = 3600.0,  # 1 hour in seconds
        cost_budget: float = 10.0,
        session_save_path: Optional[str] = None,
    ) -> None:
        """
        Initialize the UserProxyAgent.
        
        Args:
            name: Name of the agent
            max_loops: Maximum exploration loops allowed
            token_budget: Maximum tokens that can be consumed
            time_budget: Maximum time in seconds for exploration
            cost_budget: Maximum cost budget for external services
            session_save_path: Path to save/load exploration sessions
        """
        super().__init__()
        
        self.name = name
        self.max_loops = max_loops
        self.token_budget = token_budget
        self.time_budget = time_budget
        self.cost_budget = cost_budget
        self.session_save_path = session_save_path
        
        # Current exploration state
        self.current_state: Optional[ExplorationState] = None
        self.orchestrator_agent = None  # Will be set when discovery starts
        
        # Session tracking
        self.session_start_time: Optional[float] = None
        self.tokens_used: int = 0
        self.cost_used: float = 0.0
        
        # Register state for persistence
        self.register_state("name")
        self.register_state("max_loops")
        self.register_state("token_budget")
        self.register_state("time_budget")
        self.register_state("cost_budget")
    
    async def start_discovery_session(
        self,
        knowledge_base_path: str,
        initial_idea: str,
        focus_areas: Optional[List[str]] = None,
        exploration_depth: str = "normal",
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Start a new discovery session.
        
        Args:
            knowledge_base_path: Path to user's local knowledge base
            initial_idea: Starting point or question for exploration
            focus_areas: Optional list of specific areas to focus on
            exploration_depth: "shallow", "normal", or "deep"
            session_id: Optional session ID to resume existing session
            
        Returns:
            Dictionary containing session info and initial status
        """
        if not os.path.exists(knowledge_base_path):
            raise ValueError(f"Knowledge base path does not exist: {knowledge_base_path}")
        
        # Initialize budget info
        budget_info = BudgetInfo(
            loops_remaining=self.max_loops,
            max_loops=self.max_loops,
            tokens_remaining=self.token_budget,
            token_budget=self.token_budget,
            time_remaining=self.time_budget,
            cost_remaining=self.cost_budget,
        )
        
        # Create or load exploration state
        if session_id and self.session_save_path:
            try:
                session_file = os.path.join(self.session_save_path, f"{session_id}.json")
                self.current_state = ExplorationState.load_from_file(session_file)
                print(f"Resumed session {session_id}")
            except FileNotFoundError:
                print(f"Session {session_id} not found, creating new session")
                self.current_state = ExplorationState()
        else:
            self.current_state = ExplorationState()
        
        # Update budget info
        self.current_state.budget_info = budget_info
        self.session_start_time = time.time()
        
        # Create discovery message for orchestrator
        discovery_msg = DiscoveryMessage.create_control_message(
            control_type=MessageType.CONTROL_START,
            payload={
                "knowledge_base_path": knowledge_base_path,
                "initial_idea": initial_idea,
                "focus_areas": focus_areas or [],
                "exploration_depth": exploration_depth,
                "session_id": self.current_state.session_id,
            },
            budget_info=budget_info,
            sender_id=self.id,
        )
        
        session_info = {
            "session_id": self.current_state.session_id,
            "status": "initialized",
            "knowledge_base_path": knowledge_base_path,
            "initial_idea": initial_idea,
            "focus_areas": focus_areas,
            "exploration_depth": exploration_depth,
            "budget_info": {
                "max_loops": self.max_loops,
                "token_budget": self.token_budget,
                "time_budget": self.time_budget,
                "cost_budget": self.cost_budget,
            },
            "message": discovery_msg.to_dict(),
        }
        
        return session_info
    
    async def get_session_status(self) -> Dict[str, Any]:
        """
        Get current session status and progress.
        
        Returns:
            Dictionary containing current session status
        """
        if not self.current_state:
            return {"status": "no_active_session"}
        
        elapsed_time = time.time() - self.session_start_time if self.session_start_time else 0
        
        status = {
            "session_id": self.current_state.session_id,
            "phase": self.current_state.phase.value,
            "current_loop": self.current_state.current_loop,
            "budget_status": {
                "loops_remaining": self.current_state.budget_info.loops_remaining,
                "tokens_remaining": self.current_state.budget_info.tokens_remaining,
                "time_remaining": self.current_state.budget_info.time_remaining,
                "cost_remaining": self.current_state.budget_info.cost_remaining,
                "elapsed_time": elapsed_time,
                "is_budget_critical": self.current_state.is_budget_critical(),
                "is_budget_exhausted": self.current_state.is_budget_exhausted(),
            },
            "progress": {
                "total_concepts": len(self.current_state.knowledge_graph.nodes()),
                "visited_concepts": len(self.current_state.visited_concepts),
                "working_memory_insights": len(self.current_state.working_memory),
                "surprise_events": len(self.current_state.surprise_buffer),
                "verified_knowledge": len(self.current_state.verified_knowledge),
            },
            "exploration_coverage": self.current_state.get_exploration_coverage(),
        }
        
        return status
    
    async def request_termination(self, reason: str = "user_request") -> Dict[str, Any]:
        """
        Request early termination of discovery session.
        
        Args:
            reason: Reason for termination
            
        Returns:
            Termination acknowledgment and preliminary results
        """
        if not self.current_state:
            return {"status": "no_active_session"}
        
        # Create termination message
        termination_msg = DiscoveryMessage.create_control_message(
            control_type=MessageType.CONTROL_TERMINATE,
            payload={"reason": reason, "user_requested": True},
            budget_info=self.current_state.budget_info,
            sender_id=self.id,
        )
        
        # Save current state if path provided
        if self.session_save_path:
            self._save_current_session()
        
        return {
            "status": "termination_requested",
            "reason": reason,
            "session_id": self.current_state.session_id,
            "message": termination_msg.to_dict(),
            "preliminary_results": await self._generate_preliminary_results(),
        }
    
    async def get_final_results(self) -> Dict[str, Any]:
        """
        Get final discovery results in structured format.
        
        Returns:
            Comprehensive discovery results report
        """
        if not self.current_state:
            return {"error": "no_active_session"}
        
        # Calculate final metrics
        total_time = time.time() - self.session_start_time if self.session_start_time else 0
        
        # Generate structured report
        report = {
            "executive_summary": {
                "session_id": self.current_state.session_id,
                "total_exploration_time": total_time,
                "loops_completed": self.current_state.current_loop,
                "key_insights": [
                    insight["insight"] for insight in self.current_state.insights[:5]
                ],
                "surprise_level": self._calculate_average_surprise(),
                "exploration_coverage": self._assess_coverage(),
            },
            "discoveries": self.current_state.insights,
            "hypotheses": self.current_state.hypotheses,
            "questions_generated": self.current_state.research_questions,
            "meta_analysis": self.current_state.meta_analysis,
            "budget_utilization": {
                "loops_completed": self.current_state.current_loop,
                "max_loops": self.max_loops,
                "tokens_used": self.token_budget - self.current_state.budget_info.tokens_remaining,
                "token_budget": self.token_budget,
                "time_used": total_time,
                "time_budget": self.time_budget,
                "cost_used": self.cost_budget - self.current_state.budget_info.cost_remaining,
                "cost_budget": self.cost_budget,
                "termination_reason": self._determine_termination_reason(),
            },
            "session_metadata": {
                "start_time": self.session_start_time,
                "end_time": time.time(),
                "knowledge_graph_size": len(self.current_state.knowledge_graph.nodes()),
                "surprise_events": len(self.current_state.surprise_buffer),
                "verified_knowledge_entries": len(self.current_state.verified_knowledge),
            },
        }
        
        # Save final results if path provided
        if self.session_save_path:
            self._save_final_results(report)
        
        return report
    
    async def observe(self, msg: Union[Msg, List[Msg], None]) -> None:
        """
        Receive messages from other agents in the discovery system.
        
        Args:
            msg: Message(s) received from other agents
        """
        if msg is None:
            return
        
        if isinstance(msg, list):
            for m in msg:
                await self._process_message(m)
        else:
            await self._process_message(msg)
    
    async def reply(self, *args: Any, **kwargs: Any) -> Msg:
        """
        Generate replies to incoming messages.
        
        This agent primarily receives status updates and control messages.
        """
        # UserProxyAgent typically doesn't generate conversational replies
        # It processes control messages and status updates
        return Msg(
            name=self.name,
            content="UserProxyAgent ready",
            role="assistant",
        )
    
    async def _process_message(self, msg: Msg) -> None:
        """Process individual messages from discovery system."""
        try:
            if msg.name.startswith("discovery_"):
                discovery_msg = DiscoveryMessage.from_msg(msg)
                await self._handle_discovery_message(discovery_msg)
        except Exception as e:
            print(f"Error processing message: {e}")
    
    async def _handle_discovery_message(self, msg: DiscoveryMessage) -> None:
        """Handle discovery system messages."""
        if msg.message_type == MessageType.CONTROL_BUDGET_UPDATE:
            # Update budget tracking
            if self.current_state:
                self.current_state.update_budget(
                    tokens_used=msg.payload.get("tokens_used", 0),
                    time_used=msg.payload.get("time_used", 0.0),
                    cost_used=msg.payload.get("cost_used", 0.0),
                )
        
        elif msg.message_type == MessageType.META_REPORT:
            # Receive meta-analysis results
            if self.current_state:
                self.current_state.meta_analysis.update(msg.payload)
    
    def _save_current_session(self) -> None:
        """Save current session state to file."""
        if self.session_save_path and self.current_state:
            os.makedirs(self.session_save_path, exist_ok=True)
            session_file = os.path.join(
                self.session_save_path, 
                f"{self.current_state.session_id}.json"
            )
            self.current_state.save_to_file(session_file)
    
    def _save_final_results(self, report: Dict[str, Any]) -> None:
        """Save final results report to file."""
        if self.session_save_path and self.current_state:
            os.makedirs(self.session_save_path, exist_ok=True)
            results_file = os.path.join(
                self.session_save_path,
                f"{self.current_state.session_id}_results.json"
            )
            with open(results_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)
    
    async def _generate_preliminary_results(self) -> Dict[str, Any]:
        """Generate preliminary results for early termination."""
        if not self.current_state:
            return {}
        
        return {
            "insights_found": len(self.current_state.insights),
            "hypotheses_generated": len(self.current_state.hypotheses),
            "surprise_events": len(self.current_state.surprise_buffer),
            "exploration_progress": {
                "loops_completed": self.current_state.current_loop,
                "concepts_explored": len(self.current_state.visited_concepts),
                "coverage": self.current_state.get_exploration_coverage(),
            },
        }
    
    def _calculate_average_surprise(self) -> float:
        """Calculate average surprise score across all events."""
        if not self.current_state or not self.current_state.surprise_buffer:
            return 0.0
        
        total_surprise = sum(event.surprise_score for event in self.current_state.surprise_buffer)
        return total_surprise / len(self.current_state.surprise_buffer)
    
    def _assess_coverage(self) -> str:
        """Assess exploration coverage level."""
        if not self.current_state:
            return "unknown"
        
        coverage = self.current_state.get_exploration_coverage()
        coverage_ratio = coverage["coverage_ratio"]
        
        if coverage_ratio >= 0.8:
            return "comprehensive"
        elif coverage_ratio >= 0.5:
            return "substantial"
        elif coverage_ratio >= 0.3:
            return "moderate"
        else:
            return "limited"
    
    def _determine_termination_reason(self) -> str:
        """Determine why the session terminated."""
        if not self.current_state:
            return "unknown"
        
        if self.current_state.budget_info.loops_remaining <= 0:
            return "loop_budget_exhausted"
        elif self.current_state.budget_info.tokens_remaining <= 0:
            return "token_budget_exhausted"
        elif self.current_state.budget_info.time_remaining <= 0:
            return "time_budget_exhausted"
        elif self.current_state.budget_info.cost_remaining <= 0:
            return "cost_budget_exhausted"
        else:
            return "user_terminated"