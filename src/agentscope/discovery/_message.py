# -*- coding: utf-8 -*-
"""Message classes for Agent Discovery System inter-agent communication."""
from dataclasses import dataclass
from typing import Any, Dict, Optional
from enum import Enum
import shortuuid
import json

from ..message import Msg


class MessageType(Enum):
    """Types of discovery messages for agent communication."""
    
    # Query types
    QUERY_EXPLORE = "query.explore"
    QUERY_SEARCH = "query.search"
    QUERY_ANALYZE = "query.analyze"
    QUERY_VERIFY = "query.verify"
    
    # Evidence types
    EVIDENCE_FOUND = "evidence.found"
    EVIDENCE_VERIFY = "evidence.verify"
    EVIDENCE_INTEGRATE = "evidence.integrate"
    
    # Insight types
    INSIGHT_GENERATE = "insight.generate"
    INSIGHT_ASSESS = "insight.assess"
    INSIGHT_SYNTHESIZE = "insight.synthesize"
    
    # Control types
    CONTROL_START = "control.start"
    CONTROL_CONTINUE = "control.continue"
    CONTROL_TERMINATE = "control.terminate"
    CONTROL_BUDGET_UPDATE = "control.budget_update"
    
    # Meta types
    META_ANALYZE = "meta.analyze"
    META_REPORT = "meta.report"


@dataclass
class BudgetInfo:
    """Budget information for exploration control."""
    
    loops_remaining: int
    max_loops: int
    tokens_remaining: int
    token_budget: int
    time_remaining: float
    cost_remaining: float
    

@dataclass 
class DiscoveryMessage:
    """Standardized message format for inter-agent communication in discovery system."""
    
    message_type: MessageType
    task_id: str
    payload: Dict[str, Any]
    budget_info: BudgetInfo
    priority: float = 0.5
    sender_id: Optional[str] = None
    recipient_id: Optional[str] = None
    timestamp: Optional[float] = None
    
    def __post_init__(self):
        """Initialize after creation."""
        if self.task_id is None:
            self.task_id = shortuuid.uuid()
        if self.timestamp is None:
            import time
            self.timestamp = time.time()
    
    @classmethod
    def create_exploration_query(
        cls,
        query: str,
        context: Dict[str, Any],
        budget_info: BudgetInfo,
        priority: float = 0.5,
        sender_id: Optional[str] = None,
    ) -> "DiscoveryMessage":
        """Create an exploration query message."""
        return cls(
            message_type=MessageType.QUERY_EXPLORE,
            task_id=shortuuid.uuid(),
            payload={
                "query": query,
                "context": context,
                "exploration_depth": context.get("exploration_depth", "normal"),
                "focus_areas": context.get("focus_areas", []),
            },
            budget_info=budget_info,
            priority=priority,
            sender_id=sender_id,
        )
    
    @classmethod
    def create_evidence_message(
        cls,
        evidence: Dict[str, Any],
        confidence: float,
        sources: list,
        budget_info: BudgetInfo,
        sender_id: Optional[str] = None,
    ) -> "DiscoveryMessage":
        """Create an evidence found message."""
        return cls(
            message_type=MessageType.EVIDENCE_FOUND,
            task_id=shortuuid.uuid(),
            payload={
                "evidence": evidence,
                "confidence": confidence,
                "sources": sources,
                "timestamp": evidence.get("timestamp"),
                "validation_status": "pending",
            },
            budget_info=budget_info,
            priority=confidence,  # Higher confidence = higher priority
            sender_id=sender_id,
        )
    
    @classmethod
    def create_insight_message(
        cls,
        insight: str,
        hypothesis: Optional[str],
        connections: list,
        novelty_score: float,
        budget_info: BudgetInfo,
        sender_id: Optional[str] = None,
    ) -> "DiscoveryMessage":
        """Create an insight generation message.""" 
        return cls(
            message_type=MessageType.INSIGHT_GENERATE,
            task_id=shortuuid.uuid(),
            payload={
                "insight": insight,
                "hypothesis": hypothesis,
                "connections": connections,
                "novelty_score": novelty_score,
                "confidence": 0.0,  # To be assessed
                "supporting_evidence": [],
            },
            budget_info=budget_info,
            priority=novelty_score,  # Higher novelty = higher priority
            sender_id=sender_id,
        )
    
    @classmethod
    def create_control_message(
        cls,
        control_type: MessageType,
        payload: Dict[str, Any],
        budget_info: BudgetInfo,
        sender_id: Optional[str] = None,
    ) -> "DiscoveryMessage":
        """Create a control message for workflow management."""
        return cls(
            message_type=control_type,
            task_id=shortuuid.uuid(),
            payload=payload,
            budget_info=budget_info,
            priority=1.0,  # Control messages have highest priority
            sender_id=sender_id,
        )
    
    def to_msg(self) -> Msg:
        """Convert to AgentScope Msg format for inter-agent communication."""
        return Msg(
            name=f"discovery_{self.message_type.value}",
            content=self.to_json(),
            role="assistant",
        )
    
    @classmethod
    def from_msg(cls, msg: Msg) -> "DiscoveryMessage":
        """Create from AgentScope Msg format."""
        if isinstance(msg.content, str):
            data = json.loads(msg.content)
        else:
            data = msg.content
            
        return cls.from_dict(data)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary format."""
        return {
            "message_type": self.message_type.value,
            "task_id": self.task_id,
            "payload": self.payload,
            "budget_info": {
                "loops_remaining": self.budget_info.loops_remaining,
                "max_loops": self.budget_info.max_loops,
                "tokens_remaining": self.budget_info.tokens_remaining,
                "token_budget": self.budget_info.token_budget,
                "time_remaining": self.budget_info.time_remaining,
                "cost_remaining": self.budget_info.cost_remaining,
            },
            "priority": self.priority,
            "sender_id": self.sender_id,
            "recipient_id": self.recipient_id,
            "timestamp": self.timestamp,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DiscoveryMessage":
        """Create from dictionary format."""
        budget_data = data["budget_info"]
        budget_info = BudgetInfo(
            loops_remaining=budget_data["loops_remaining"],
            max_loops=budget_data["max_loops"],
            tokens_remaining=budget_data["tokens_remaining"],
            token_budget=budget_data["token_budget"],
            time_remaining=budget_data["time_remaining"],
            cost_remaining=budget_data["cost_remaining"],
        )
        
        return cls(
            message_type=MessageType(data["message_type"]),
            task_id=data["task_id"],
            payload=data["payload"],
            budget_info=budget_info,
            priority=data["priority"],
            sender_id=data.get("sender_id"),
            recipient_id=data.get("recipient_id"),
            timestamp=data.get("timestamp"),
        )
    
    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)
    
    def update_budget(self, budget_info: BudgetInfo) -> None:
        """Update budget information."""
        self.budget_info = budget_info
    
    def is_high_priority(self) -> bool:
        """Check if message is high priority."""
        return self.priority > 0.8
    
    def is_budget_critical(self) -> bool:
        """Check if budget is critically low."""
        budget = self.budget_info
        return (
            budget.loops_remaining <= 1 or
            budget.tokens_remaining < budget.token_budget * 0.1 or
            budget.time_remaining < 60.0 or  # Less than 1 minute
            budget.cost_remaining < 1.0
        )