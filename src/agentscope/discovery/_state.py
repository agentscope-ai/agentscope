# -*- coding: utf-8 -*-
"""State management classes for Agent Discovery System exploration sessions."""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set
from enum import Enum
import time
import json
import shortuuid

try:
    import networkx as nx
except ImportError:
    # Mock networkx for testing environments
    class MockGraph:
        def __init__(self):
            self._nodes = {}
            self._edges = []
        def nodes(self, data=False):
            if data:
                return list(self._nodes.items())
            return list(self._nodes.keys())
        def edges(self, data=False):
            return self._edges
        def add_nodes_from(self, nodes):
            for node in nodes:
                if isinstance(node, tuple):
                    self._nodes[node[0]] = node[1] if len(node) > 1 else {}
                else:
                    self._nodes[node] = {}
        def add_edges_from(self, edges):
            self._edges.extend(edges)
    
    class MockNetworkX:
        def Graph(self):
            return MockGraph()
    
    nx = MockNetworkX()

from ._message import BudgetInfo


class ExplorationPhase(Enum):
    """Phases of the discovery exploration process."""
    
    INITIALIZATION = "initialization"
    SEEDING = "seeding"
    ACTIVE_EXPLORATION = "active_exploration"
    INSIGHT_GENERATION = "insight_generation"
    META_ANALYSIS = "meta_analysis"
    REPORTING = "reporting"
    COMPLETE = "complete"


@dataclass
class SurpriseEvent:
    """Represents a high-surprise discovery event."""
    
    id: str = field(default_factory=shortuuid.uuid)
    content: str = ""
    source: str = ""
    surprise_score: float = 0.0
    kl_divergence: float = 0.0
    timestamp: float = field(default_factory=time.time)
    related_concepts: List[str] = field(default_factory=list)
    paradigm_shift: bool = False
    validation_status: str = "pending"  # pending, verified, refuted
    impact_assessment: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "surprise_score": self.surprise_score,
            "kl_divergence": self.kl_divergence,
            "timestamp": self.timestamp,
            "related_concepts": self.related_concepts,
            "paradigm_shift": self.paradigm_shift,
            "validation_status": self.validation_status,
            "impact_assessment": self.impact_assessment,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SurpriseEvent":
        """Create from dictionary format."""
        return cls(
            id=data.get("id", shortuuid.uuid()),
            content=data.get("content", ""),
            source=data.get("source", ""),
            surprise_score=data.get("surprise_score", 0.0),
            kl_divergence=data.get("kl_divergence", 0.0),
            timestamp=data.get("timestamp", time.time()),
            related_concepts=data.get("related_concepts", []),
            paradigm_shift=data.get("paradigm_shift", False),
            validation_status=data.get("validation_status", "pending"),
            impact_assessment=data.get("impact_assessment", {}),
        )


@dataclass
class TemporaryInsight:
    """Represents an unverified insight in working memory."""
    
    id: str = field(default_factory=shortuuid.uuid)
    insight: str = ""
    hypothesis: Optional[str] = None
    connections: List[str] = field(default_factory=list)
    confidence: float = 0.0
    novelty_score: float = 0.0
    supporting_evidence: List[str] = field(default_factory=list)
    contradicting_evidence: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    exploration_path: List[str] = field(default_factory=list)
    validation_attempts: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "id": self.id,
            "insight": self.insight,
            "hypothesis": self.hypothesis,
            "connections": self.connections,
            "confidence": self.confidence,
            "novelty_score": self.novelty_score,
            "supporting_evidence": self.supporting_evidence,
            "contradicting_evidence": self.contradicting_evidence,
            "timestamp": self.timestamp,
            "exploration_path": self.exploration_path,
            "validation_attempts": self.validation_attempts,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TemporaryInsight":
        """Create from dictionary format."""
        return cls(
            id=data.get("id", shortuuid.uuid()),
            insight=data.get("insight", ""),
            hypothesis=data.get("hypothesis"),
            connections=data.get("connections", []),
            confidence=data.get("confidence", 0.0),
            novelty_score=data.get("novelty_score", 0.0),
            supporting_evidence=data.get("supporting_evidence", []),
            contradicting_evidence=data.get("contradicting_evidence", []),
            timestamp=data.get("timestamp", time.time()),
            exploration_path=data.get("exploration_path", []),
            validation_attempts=data.get("validation_attempts", 0),
        )


@dataclass  
class ExplorationState:
    """Persistent state for discovery session with hierarchical memory."""
    
    session_id: str = field(default_factory=shortuuid.uuid)
    phase: ExplorationPhase = ExplorationPhase.INITIALIZATION
    start_time: float = field(default_factory=time.time)
    
    # Budget tracking
    budget_info: BudgetInfo = field(default_factory=lambda: BudgetInfo(
        loops_remaining=5,
        max_loops=5,
        tokens_remaining=20000,
        token_budget=20000,
        time_remaining=3600.0,  # 1 hour
        cost_remaining=10.0,
    ))
    
    # Core knowledge structures
    knowledge_graph: nx.Graph = field(default_factory=nx.Graph)
    concept_embeddings: Dict[str, List[float]] = field(default_factory=dict)
    
    # Memory hierarchy
    verified_knowledge: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    working_memory: List[TemporaryInsight] = field(default_factory=list)
    surprise_buffer: List[SurpriseEvent] = field(default_factory=list)
    
    # Exploration tracking
    current_loop: int = 0
    exploration_history: List[Dict[str, Any]] = field(default_factory=list)
    visited_concepts: Set[str] = field(default_factory=set)
    exploration_frontier: List[str] = field(default_factory=list)
    
    # Confidence and assessment
    confidence_scores: Dict[str, float] = field(default_factory=dict)
    uncertainty_map: Dict[str, float] = field(default_factory=dict)
    
    # Generated outputs
    insights: List[Dict[str, Any]] = field(default_factory=list)
    hypotheses: List[Dict[str, Any]] = field(default_factory=list)
    research_questions: List[Dict[str, Any]] = field(default_factory=list)
    
    # Meta-analysis results
    meta_analysis: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        """Initialize after creation."""
        if not isinstance(self.knowledge_graph, nx.Graph):
            self.knowledge_graph = nx.Graph()
        if not isinstance(self.visited_concepts, set):
            self.visited_concepts = set(self.visited_concepts)
    
    def update_budget(self, 
                     tokens_used: int = 0, 
                     time_used: float = 0.0, 
                     cost_used: float = 0.0) -> None:
        """Update budget consumption."""
        self.budget_info.tokens_remaining -= tokens_used
        self.budget_info.time_remaining -= time_used
        self.budget_info.cost_remaining -= cost_used
        
        # Ensure non-negative values
        self.budget_info.tokens_remaining = max(0, self.budget_info.tokens_remaining)
        self.budget_info.time_remaining = max(0.0, self.budget_info.time_remaining)
        self.budget_info.cost_remaining = max(0.0, self.budget_info.cost_remaining)
    
    def start_new_loop(self) -> bool:
        """Start a new exploration loop. Returns False if budget exhausted."""
        if self.is_budget_exhausted():
            return False
            
        self.current_loop += 1
        self.budget_info.loops_remaining -= 1
        
        # Add loop entry to history
        self.exploration_history.append({
            "loop": self.current_loop,
            "start_time": time.time(),
            "phase": self.phase.value,
            "budget_snapshot": {
                "loops_remaining": self.budget_info.loops_remaining,
                "tokens_remaining": self.budget_info.tokens_remaining,
                "time_remaining": self.budget_info.time_remaining,
                "cost_remaining": self.budget_info.cost_remaining,
            },
        })
        
        return True
    
    def is_budget_exhausted(self) -> bool:
        """Check if any budget constraint is exhausted."""
        return (
            self.budget_info.loops_remaining <= 0 or
            self.budget_info.tokens_remaining <= 0 or
            self.budget_info.time_remaining <= 0.0 or
            self.budget_info.cost_remaining <= 0.0
        )
    
    def is_budget_critical(self) -> bool:
        """Check if budget is critically low (< 20% remaining)."""
        budget = self.budget_info
        return (
            budget.loops_remaining <= max(1, budget.max_loops * 0.2) or
            budget.tokens_remaining < budget.token_budget * 0.2 or
            budget.time_remaining < 300.0 or  # Less than 5 minutes
            budget.cost_remaining < 2.0
        )
    
    def add_surprise_event(self, event: SurpriseEvent) -> None:
        """Add a high-surprise event to the buffer."""
        self.surprise_buffer.append(event)
        
        # Keep buffer size manageable (last 50 events)
        if len(self.surprise_buffer) > 50:
            self.surprise_buffer = self.surprise_buffer[-50:]
    
    def add_temporary_insight(self, insight: TemporaryInsight) -> None:
        """Add insight to working memory."""
        self.working_memory.append(insight)
        
        # Keep working memory size manageable (last 20 insights)
        if len(self.working_memory) > 20:
            self.working_memory = self.working_memory[-20:]
    
    def promote_insight_to_verified(self, insight_id: str, confidence: float) -> bool:
        """Promote insight from working memory to verified knowledge."""
        for i, insight in enumerate(self.working_memory):
            if insight.id == insight_id:
                if confidence >= 0.7:  # High confidence threshold
                    verified_entry = {
                        "insight": insight.insight,
                        "hypothesis": insight.hypothesis,
                        "connections": insight.connections,
                        "confidence": confidence,
                        "evidence": insight.supporting_evidence,
                        "timestamp": time.time(),
                        "promoted_from_working_memory": True,
                    }
                    self.verified_knowledge[insight_id] = verified_entry
                    
                    # Remove from working memory
                    del self.working_memory[i]
                    return True
        return False
    
    def get_high_surprise_events(self, threshold: float = 0.8) -> List[SurpriseEvent]:
        """Get events above surprise threshold."""
        return [event for event in self.surprise_buffer 
                if event.surprise_score >= threshold]
    
    def get_high_confidence_insights(self, threshold: float = 0.7) -> List[TemporaryInsight]:
        """Get insights above confidence threshold."""
        return [insight for insight in self.working_memory 
                if insight.confidence >= threshold]
    
    def get_exploration_coverage(self) -> Dict[str, float]:
        """Calculate exploration coverage metrics."""
        total_concepts = len(self.knowledge_graph.nodes())
        visited_ratio = len(self.visited_concepts) / max(1, total_concepts)
        
        return {
            "total_concepts": total_concepts,
            "visited_concepts": len(self.visited_concepts),
            "coverage_ratio": visited_ratio,
            "frontier_size": len(self.exploration_frontier),
            "loops_completed": self.current_loop,
        }
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert state to dictionary format for serialization."""
        return {
            "session_id": self.session_id,
            "phase": self.phase.value,
            "start_time": self.start_time,
            "budget_info": {
                "loops_remaining": self.budget_info.loops_remaining,
                "max_loops": self.budget_info.max_loops,
                "tokens_remaining": self.budget_info.tokens_remaining,
                "token_budget": self.budget_info.token_budget,
                "time_remaining": self.budget_info.time_remaining,
                "cost_remaining": self.budget_info.cost_remaining,
            },
            "knowledge_graph": {
                "nodes": list(self.knowledge_graph.nodes(data=True)),
                "edges": list(self.knowledge_graph.edges(data=True)),
            },
            "concept_embeddings": self.concept_embeddings,
            "verified_knowledge": self.verified_knowledge,
            "working_memory": [insight.to_dict() for insight in self.working_memory],
            "surprise_buffer": [event.to_dict() for event in self.surprise_buffer],
            "current_loop": self.current_loop,
            "exploration_history": self.exploration_history,
            "visited_concepts": list(self.visited_concepts),
            "exploration_frontier": self.exploration_frontier,
            "confidence_scores": self.confidence_scores,
            "uncertainty_map": self.uncertainty_map,
            "insights": self.insights,
            "hypotheses": self.hypotheses,
            "research_questions": self.research_questions,
            "meta_analysis": self.meta_analysis,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExplorationState":
        """Create state from dictionary format."""
        budget_data = data["budget_info"]
        budget_info = BudgetInfo(
            loops_remaining=budget_data["loops_remaining"],
            max_loops=budget_data["max_loops"],
            tokens_remaining=budget_data["tokens_remaining"],
            token_budget=budget_data["token_budget"],
            time_remaining=budget_data["time_remaining"],
            cost_remaining=budget_data["cost_remaining"],
        )
        
        # Reconstruct knowledge graph
        kg = nx.Graph()
        kg_data = data.get("knowledge_graph", {"nodes": [], "edges": []})
        kg.add_nodes_from(kg_data["nodes"])
        kg.add_edges_from(kg_data["edges"])
        
        # Reconstruct working memory and surprise buffer
        working_memory = [
            TemporaryInsight.from_dict(insight_data) 
            for insight_data in data.get("working_memory", [])
        ]
        surprise_buffer = [
            SurpriseEvent.from_dict(event_data) 
            for event_data in data.get("surprise_buffer", [])
        ]
        
        return cls(
            session_id=data.get("session_id", shortuuid.uuid()),
            phase=ExplorationPhase(data.get("phase", "initialization")),
            start_time=data.get("start_time", time.time()),
            budget_info=budget_info,
            knowledge_graph=kg,
            concept_embeddings=data.get("concept_embeddings", {}),
            verified_knowledge=data.get("verified_knowledge", {}),
            working_memory=working_memory,
            surprise_buffer=surprise_buffer,
            current_loop=data.get("current_loop", 0),
            exploration_history=data.get("exploration_history", []),
            visited_concepts=set(data.get("visited_concepts", [])),
            exploration_frontier=data.get("exploration_frontier", []),
            confidence_scores=data.get("confidence_scores", {}),
            uncertainty_map=data.get("uncertainty_map", {}),
            insights=data.get("insights", []),
            hypotheses=data.get("hypotheses", []),
            research_questions=data.get("research_questions", []),
            meta_analysis=data.get("meta_analysis", {}),
        )
    
    def save_to_file(self, filepath: str) -> None:
        """Save state to JSON file."""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)
    
    @classmethod
    def load_from_file(cls, filepath: str) -> "ExplorationState":
        """Load state from JSON file."""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)