#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Real-time WebSocket Streaming Architecture for Discovery System

This module implements comprehensive real-time streaming capabilities
following the design specification for agent thought processes,
discovery progress, and session management.
"""

import asyncio
import json
import uuid
import logging
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from enum import Enum
from dataclasses import dataclass, asdict
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel


class EventType(Enum):
    """Enumeration of WebSocket event types."""
    # Session Events
    SESSION_STARTED = "session_started"
    SESSION_STATUS = "session_status"
    SESSION_TERMINATED = "session_terminated"
    
    # Agent Events
    AGENT_THINKING_START = "agent_thinking_start"
    AGENT_THINKING_STEP = "agent_thinking_step"
    AGENT_THINKING_COMPLETE = "agent_thinking_complete"
    AGENT_TASK_ASSIGNED = "agent_task_assigned"
    AGENT_TASK_COMPLETED = "agent_task_completed"
    
    # AI Model Call Events
    AI_MODEL_CALL_START = "ai_model_call_start"
    AI_MODEL_CALL_RESPONSE = "ai_model_call_response" 
    AI_MODEL_CALL_COMPLETE = "ai_model_call_complete"
    AI_MODEL_CALL_ERROR = "ai_model_call_error"
    AI_MODEL_TOKEN_USAGE = "ai_model_token_usage"
    
    # Discovery Events
    DISCOVERY_FOUND = "discovery_found"
    INSIGHT_GENERATED = "insight_generated"
    HYPOTHESIS_FORMED = "hypothesis_formed"
    QUESTION_RAISED = "question_raised"
    
    # Loop Events
    EXPLORATION_LOOP_STARTING = "exploration_loop_starting"
    EXPLORATION_LOOP_PROGRESS = "exploration_loop_progress"
    EXPLORATION_LOOP_COMPLETED = "exploration_loop_completed"
    
    # Knowledge Events
    KNOWLEDGE_BASE_UPLOADED = "knowledge_base_uploaded"
    KNOWLEDGE_GRAPH_UPDATED = "knowledge_graph_updated"
    
    # System Events
    ERROR_OCCURRED = "error_occurred"
    WARNING_ISSUED = "warning_issued"
    PROGRESS_UPDATE = "progress_update"
    HEARTBEAT = "heartbeat"


@dataclass
class StreamEvent:
    """Base class for streaming events."""
    event_type: EventType = EventType.HEARTBEAT
    timestamp: str = ""
    session_id: Optional[str] = None
    data: Optional[Dict[str, Any]] = None
    
    def __post_init__(self):
        if not self.timestamp:
            from datetime import datetime
            self.timestamp = datetime.now().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert event to dictionary for JSON serialization."""
        return {
            "type": self.event_type.value,
            "timestamp": self.timestamp,
            "session_id": self.session_id,
            "data": self.data or {}
        }


@dataclass
class AgentThinkingEvent(StreamEvent):
    """Event for agent thinking processes."""
    agent_name: str = ""
    thinking_step: str = ""
    content: str = ""
    confidence: float = 0.0
    
    def __post_init__(self):
        self.data = {
            "agent_name": self.agent_name,
            "thinking_step": self.thinking_step,
            "content": self.content,
            "confidence": self.confidence
        }


@dataclass
class DiscoveryEvent(StreamEvent):
    """Event for new discoveries."""
    discovery: Dict[str, Any] = None
    agent_source: str = ""
    discovery_id: str = ""
    
    def __post_init__(self):
        if self.discovery is None:
            self.discovery = {}
        self.data = {
            "discovery": self.discovery,
            "agent_source": self.agent_source,
            "discovery_id": self.discovery_id
        }


@dataclass
class ProgressEvent(StreamEvent):
    """Event for progress updates."""
    loop_number: int = 0
    total_loops: int = 0
    progress_percentage: float = 0.0
    current_step: str = ""
    
    def __post_init__(self):
        self.data = {
            "loop_number": self.loop_number,
            "total_loops": self.total_loops,
            "progress_percentage": self.progress_percentage,
            "current_step": self.current_step
        }


@dataclass
class AIModelCallEvent(StreamEvent):
    """Event for AI model calls with full visibility."""
    agent_name: str = ""
    model_name: str = ""
    call_type: str = ""  # 'start', 'response', 'complete', 'error'
    request_data: Dict[str, Any] = None
    response_data: Dict[str, Any] = None
    token_usage: Dict[str, Any] = None
    duration_ms: float = 0.0
    call_id: str = ""
    
    def __post_init__(self):
        if self.request_data is None:
            self.request_data = {}
        if self.response_data is None:
            self.response_data = {}
        if self.token_usage is None:
            self.token_usage = {}
        if not self.call_id:
            import uuid
            self.call_id = str(uuid.uuid4())[:8]
            
        self.data = {
            "agent_name": self.agent_name,
            "model_name": self.model_name,
            "call_type": self.call_type,
            "request_data": self.request_data,
            "response_data": self.response_data,
            "token_usage": self.token_usage,
            "duration_ms": self.duration_ms,
            "call_id": self.call_id
        }


class ConnectionManager:
    """Manages WebSocket connections and client subscriptions."""
    
    def __init__(self):
        self.active_connections: Dict[str, WebSocket] = {}
        self.session_subscriptions: Dict[str, Set[str]] = {}  # session_id -> set of client_ids
        self.client_sessions: Dict[str, str] = {}  # client_id -> session_id
        self.connection_metadata: Dict[str, Dict[str, Any]] = {}
        self.logger = logging.getLogger(__name__)
    
    async def connect(self, websocket: WebSocket, client_id: Optional[str] = None) -> str:
        """Accept new WebSocket connection and return client ID."""
        await websocket.accept()
        
        if not client_id:
            client_id = str(uuid.uuid4())
        
        self.active_connections[client_id] = websocket
        self.connection_metadata[client_id] = {
            "connected_at": datetime.now().isoformat(),
            "last_heartbeat": datetime.now().isoformat(),
            "client_info": {}
        }
        
        self.logger.info(f"Client {client_id} connected")
        
        # Send connection confirmation
        await self.send_to_client(client_id, {
            "type": "connection_established",
            "client_id": client_id,
            "timestamp": datetime.now().isoformat()
        })
        
        return client_id
    
    async def disconnect(self, client_id: str):
        """Handle client disconnection."""
        if client_id in self.active_connections:
            # Remove from session subscriptions
            if client_id in self.client_sessions:
                session_id = self.client_sessions[client_id]
                if session_id in self.session_subscriptions:
                    self.session_subscriptions[session_id].discard(client_id)
                    if not self.session_subscriptions[session_id]:
                        del self.session_subscriptions[session_id]
                del self.client_sessions[client_id]
            
            # Clean up connection data
            del self.active_connections[client_id]
            del self.connection_metadata[client_id]
            
            self.logger.info(f"Client {client_id} disconnected")
    
    async def subscribe_to_session(self, client_id: str, session_id: str):
        """Subscribe client to session events."""
        if client_id not in self.active_connections:
            raise ValueError(f"Client {client_id} not connected")
        
        # Update subscriptions
        if session_id not in self.session_subscriptions:
            self.session_subscriptions[session_id] = set()
        
        self.session_subscriptions[session_id].add(client_id)
        self.client_sessions[client_id] = session_id
        
        self.logger.info(f"Client {client_id} subscribed to session {session_id}")
        
        # Send subscription confirmation
        await self.send_to_client(client_id, {
            "type": "session_subscribed",
            "session_id": session_id,
            "timestamp": datetime.now().isoformat()
        })
    
    async def send_to_client(self, client_id: str, data: Dict[str, Any]):
        """Send data to specific client."""
        if client_id not in self.active_connections:
            return False
        
        try:
            websocket = self.active_connections[client_id]
            await websocket.send_text(json.dumps(data, default=str))
            return True
        except Exception as e:
            self.logger.error(f"Error sending to client {client_id}: {e}")
            await self.disconnect(client_id)
            return False
    
    async def broadcast_to_session(self, session_id: str, data: Dict[str, Any]):
        """Broadcast data to all clients subscribed to a session."""
        if session_id not in self.session_subscriptions:
            return 0
        
        client_ids = list(self.session_subscriptions[session_id])
        successful_sends = 0
        
        for client_id in client_ids:
            if await self.send_to_client(client_id, data):
                successful_sends += 1
        
        return successful_sends
    
    async def broadcast_to_all(self, data: Dict[str, Any]):
        """Broadcast data to all connected clients."""
        client_ids = list(self.active_connections.keys())
        successful_sends = 0
        
        for client_id in client_ids:
            if await self.send_to_client(client_id, data):
                successful_sends += 1
        
        return successful_sends
    
    async def handle_heartbeat(self, client_id: str):
        """Handle heartbeat from client."""
        if client_id in self.connection_metadata:
            self.connection_metadata[client_id]["last_heartbeat"] = datetime.now().isoformat()
            await self.send_to_client(client_id, {
                "type": "heartbeat_ack",
                "timestamp": datetime.now().isoformat()
            })
    
    def get_connection_stats(self) -> Dict[str, Any]:
        """Get connection statistics."""
        return {
            "total_connections": len(self.active_connections),
            "active_sessions": len(self.session_subscriptions),
            "connections_per_session": {
                session_id: len(clients) 
                for session_id, clients in self.session_subscriptions.items()
            }
        }


class DiscoveryStreamer:
    """Main streaming interface for discovery system events."""
    
    def __init__(self, connection_manager: ConnectionManager):
        self.connection_manager = connection_manager
        self.current_session_id: Optional[str] = None
        self.event_history: List[StreamEvent] = []
        self.max_history_size = 1000
        self.logger = logging.getLogger(__name__)
    
    async def set_current_session(self, session_id: str):
        """Set the current session for streaming."""
        self.current_session_id = session_id
        await self._broadcast_event(StreamEvent(
            event_type=EventType.SESSION_STARTED,
            timestamp=datetime.now().isoformat(),
            session_id=session_id,
            data={"message": f"Session {session_id} started"}
        ))
    
    async def stream_agent_thinking_start(self, agent_name: str, task_description: str):
        """Stream agent thinking process start."""
        event = AgentThinkingEvent(
            event_type=EventType.AGENT_THINKING_START,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            agent_name=agent_name,
            thinking_step="initialization",
            content=f"Starting task: {task_description}"
        )
        await self._broadcast_event(event)
    
    async def stream_agent_thinking_step(self, agent_name: str, thinking_step: str, 
                                       content: str, confidence: float = 0.0):
        """Stream individual agent thinking step."""
        event = AgentThinkingEvent(
            event_type=EventType.AGENT_THINKING_STEP,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            agent_name=agent_name,
            thinking_step=thinking_step,
            content=content,
            confidence=confidence
        )
        await self._broadcast_event(event)
    
    async def stream_agent_thinking_complete(self, agent_name: str, summary: str):
        """Stream agent thinking process completion."""
        event = AgentThinkingEvent(
            event_type=EventType.AGENT_THINKING_COMPLETE,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            agent_name=agent_name,
            thinking_step="completion",
            content=summary
        )
        await self._broadcast_event(event)
    
    # AI Model Call Streaming Methods
    async def stream_ai_model_call_start(self, agent_name: str, model_name: str, 
                                       request_data: Dict[str, Any], call_id: str = None):
        """Stream AI model call start."""
        event = AIModelCallEvent(
            event_type=EventType.AI_MODEL_CALL_START,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            agent_name=agent_name,
            model_name=model_name,
            call_type="start",
            request_data=request_data,
            call_id=call_id or str(uuid.uuid4())[:8]
        )
        await self._broadcast_event(event)
        return event.call_id
    
    async def stream_ai_model_call_response(self, call_id: str, agent_name: str,
                                          model_name: str, response_chunk: Dict[str, Any]):
        """Stream AI model response chunks (for streaming responses)."""
        event = AIModelCallEvent(
            event_type=EventType.AI_MODEL_CALL_RESPONSE,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            agent_name=agent_name,
            model_name=model_name,
            call_type="response",
            response_data=response_chunk,
            call_id=call_id
        )
        await self._broadcast_event(event)
    
    async def stream_ai_model_call_complete(self, call_id: str, agent_name: str,
                                          model_name: str, final_response: Dict[str, Any],
                                          token_usage: Dict[str, Any], duration_ms: float):
        """Stream AI model call completion."""
        event = AIModelCallEvent(
            event_type=EventType.AI_MODEL_CALL_COMPLETE,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            agent_name=agent_name,
            model_name=model_name,
            call_type="complete",
            response_data=final_response,
            token_usage=token_usage,
            duration_ms=duration_ms,
            call_id=call_id
        )
        await self._broadcast_event(event)
    
    async def stream_ai_model_call_error(self, call_id: str, agent_name: str,
                                       model_name: str, error_details: Dict[str, Any],
                                       duration_ms: float):
        """Stream AI model call error."""
        event = AIModelCallEvent(
            event_type=EventType.AI_MODEL_CALL_ERROR,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            agent_name=agent_name,
            model_name=model_name,
            call_type="error",
            response_data=error_details,
            duration_ms=duration_ms,
            call_id=call_id
        )
        await self._broadcast_event(event)
    
    async def stream_token_usage(self, agent_name: str, model_name: str,
                               token_usage: Dict[str, Any]):
        """Stream token usage information."""
        event = StreamEvent(
            event_type=EventType.AI_MODEL_TOKEN_USAGE,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "agent_name": agent_name,
                "model_name": model_name,
                "token_usage": token_usage
            }
        )
        await self._broadcast_event(event)
    
    async def stream_discovery(self, discovery: Dict[str, Any], agent_source: str):
        """Stream new discovery."""
        discovery_id = str(uuid.uuid4())
        event = DiscoveryEvent(
            event_type=EventType.DISCOVERY_FOUND,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            discovery=discovery,
            agent_source=agent_source,
            discovery_id=discovery_id
        )
        await self._broadcast_event(event)
    
    async def stream_insight(self, insight: str, supporting_discoveries: List[str]):
        """Stream new insight generation."""
        event = StreamEvent(
            event_type=EventType.INSIGHT_GENERATED,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "insight": insight,
                "supporting_discoveries": supporting_discoveries,
                "insight_id": str(uuid.uuid4())
            }
        )
        await self._broadcast_event(event)
    
    async def stream_hypothesis(self, hypothesis: str, confidence: float, 
                              experimental_suggestions: List[str]):
        """Stream hypothesis formation."""
        event = StreamEvent(
            event_type=EventType.HYPOTHESIS_FORMED,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "hypothesis": hypothesis,
                "confidence": confidence,
                "experimental_suggestions": experimental_suggestions,
                "hypothesis_id": str(uuid.uuid4())
            }
        )
        await self._broadcast_event(event)
    
    async def stream_question(self, question: str, context: str, priority: str = "medium"):
        """Stream question for further exploration."""
        event = StreamEvent(
            event_type=EventType.QUESTION_RAISED,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "question": question,
                "context": context,
                "priority": priority,
                "question_id": str(uuid.uuid4())
            }
        )
        await self._broadcast_event(event)
    
    async def stream_loop_start(self, loop_number: int, total_loops: int, strategy: str):
        """Stream exploration loop start."""
        event = ProgressEvent(
            event_type=EventType.EXPLORATION_LOOP_STARTING,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            loop_number=loop_number,
            total_loops=total_loops,
            progress_percentage=(loop_number - 1) / total_loops * 100,
            current_step=f"Starting loop {loop_number}: {strategy}"
        )
        await self._broadcast_event(event)
    
    async def stream_loop_progress(self, loop_number: int, total_loops: int, 
                                 current_step: str, progress_percentage: float):
        """Stream exploration loop progress."""
        event = ProgressEvent(
            event_type=EventType.EXPLORATION_LOOP_PROGRESS,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            loop_number=loop_number,
            total_loops=total_loops,
            progress_percentage=progress_percentage,
            current_step=current_step
        )
        await self._broadcast_event(event)
    
    async def stream_loop_complete(self, loop_number: int, total_loops: int, 
                                 summary: Dict[str, Any]):
        """Stream exploration loop completion."""
        event = StreamEvent(
            event_type=EventType.EXPLORATION_LOOP_COMPLETED,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "loop_number": loop_number,
                "total_loops": total_loops,
                "progress_percentage": loop_number / total_loops * 100,
                "summary": summary
            }
        )
        await self._broadcast_event(event)
    
    async def stream_knowledge_base_update(self, files_count: int, concepts_added: int):
        """Stream knowledge base update."""
        event = StreamEvent(
            event_type=EventType.KNOWLEDGE_BASE_UPLOADED,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "files_count": files_count,
                "concepts_added": concepts_added,
                "message": f"Knowledge base updated with {files_count} files"
            }
        )
        await self._broadcast_event(event)
    
    async def stream_error(self, error_message: str, error_type: str = "general", 
                         details: Optional[Dict[str, Any]] = None):
        """Stream error information."""
        event = StreamEvent(
            event_type=EventType.ERROR_OCCURRED,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "error_message": error_message,
                "error_type": error_type,
                "details": details or {},
                "error_id": str(uuid.uuid4())
            }
        )
        await self._broadcast_event(event)
    
    async def stream_warning(self, warning_message: str, warning_type: str = "general"):
        """Stream warning information."""
        event = StreamEvent(
            event_type=EventType.WARNING_ISSUED,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "warning_message": warning_message,
                "warning_type": warning_type,
                "warning_id": str(uuid.uuid4())
            }
        )
        await self._broadcast_event(event)
    
    async def stream_session_termination(self, reason: str, final_stats: Dict[str, Any]):
        """Stream session termination."""
        event = StreamEvent(
            event_type=EventType.SESSION_TERMINATED,
            timestamp=datetime.now().isoformat(),
            session_id=self.current_session_id,
            data={
                "termination_reason": reason,
                "final_stats": final_stats,
                "session_duration": final_stats.get("duration", "unknown")
            }
        )
        await self._broadcast_event(event)
    
    async def _broadcast_event(self, event: StreamEvent):
        """Broadcast event to appropriate clients."""
        # Add to history
        self.event_history.append(event)
        if len(self.event_history) > self.max_history_size:
            self.event_history = self.event_history[-self.max_history_size:]
        
        # Broadcast to session subscribers
        if event.session_id:
            await self.connection_manager.broadcast_to_session(
                event.session_id, 
                event.to_dict()
            )
        else:
            # Broadcast to all if no specific session
            await self.connection_manager.broadcast_to_all(event.to_dict())
        
        self.logger.debug(f"Broadcasted event: {event.event_type.value}")
    
    def get_event_history(self, session_id: Optional[str] = None, 
                         event_types: Optional[List[EventType]] = None,
                         limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Get event history with optional filtering."""
        filtered_events = self.event_history
        
        if session_id:
            filtered_events = [e for e in filtered_events if e.session_id == session_id]
        
        if event_types:
            filtered_events = [e for e in filtered_events if e.event_type in event_types]
        
        if limit:
            filtered_events = filtered_events[-limit:]
        
        return [event.to_dict() for event in filtered_events]
    
    async def send_heartbeat(self):
        """Send heartbeat to all connections."""
        await self.connection_manager.broadcast_to_all({
            "type": EventType.HEARTBEAT.value,
            "timestamp": datetime.now().isoformat(),
            "server_status": "active"
        })


class DiscoveryAgentHooks:
    """Integration hooks for AgentScope agents to stream real-time updates."""
    
    def __init__(self, streamer: DiscoveryStreamer):
        self.streamer = streamer
    
    async def pre_reasoning_hook(self, agent_name: str, task: str):
        """Hook called before agent reasoning."""
        await self.streamer.stream_agent_thinking_start(agent_name, task)
    
    async def reasoning_step_hook(self, agent_name: str, step: str, content: str, confidence: float = 0.0):
        """Hook called during agent reasoning steps."""
        await self.streamer.stream_agent_thinking_step(agent_name, step, content, confidence)
    
    async def post_reasoning_hook(self, agent_name: str, summary: str):
        """Hook called after agent reasoning."""
        await self.streamer.stream_agent_thinking_complete(agent_name, summary)
    
    async def discovery_hook(self, discovery: Dict[str, Any], agent_name: str):
        """Hook called when agent makes a discovery."""
        await self.streamer.stream_discovery(discovery, agent_name)
    
    async def insight_hook(self, insight: str, supporting_discoveries: List[str]):
        """Hook called when insight is generated."""
        await self.streamer.stream_insight(insight, supporting_discoveries)
    
    async def error_hook(self, error_message: str, agent_name: str):
        """Hook called when agent encounters error."""
        await self.streamer.stream_error(error_message, "agent_error", {"agent": agent_name})
    
    # AI Model Call Hooks
    async def ai_model_call_start_hook(self, agent_name: str, model_name: str, 
                                     request_data: Dict[str, Any]) -> str:
        """Hook called when AI model call starts."""
        return await self.streamer.stream_ai_model_call_start(agent_name, model_name, request_data)
    
    async def ai_model_call_response_hook(self, call_id: str, agent_name: str,
                                        model_name: str, response_chunk: Dict[str, Any]):
        """Hook called when AI model returns response chunk."""
        await self.streamer.stream_ai_model_call_response(call_id, agent_name, model_name, response_chunk)
    
    async def ai_model_call_complete_hook(self, call_id: str, agent_name: str,
                                        model_name: str, final_response: Dict[str, Any],
                                        token_usage: Dict[str, Any], duration_ms: float):
        """Hook called when AI model call completes."""
        await self.streamer.stream_ai_model_call_complete(call_id, agent_name, model_name, 
                                                        final_response, token_usage, duration_ms)
    
    async def ai_model_call_error_hook(self, call_id: str, agent_name: str,
                                     model_name: str, error_details: Dict[str, Any],
                                     duration_ms: float):
        """Hook called when AI model call encounters error."""
        await self.streamer.stream_ai_model_call_error(call_id, agent_name, model_name, 
                                                      error_details, duration_ms)


# Initialize global streaming components
connection_manager = ConnectionManager()
discovery_streamer = DiscoveryStreamer(connection_manager)
agent_hooks = DiscoveryAgentHooks(discovery_streamer)


# Background task for periodic heartbeat
async def heartbeat_task():
    """Background task to send periodic heartbeats."""
    while True:
        await asyncio.sleep(30)  # Send heartbeat every 30 seconds
        await discovery_streamer.send_heartbeat()


# WebSocket endpoint handler
async def websocket_handler(websocket: WebSocket, client_id: str = None):
    """Handle WebSocket connections."""
    client_id = await connection_manager.connect(websocket, client_id)
    
    try:
        while True:
            try:
                # Wait for client messages
                message = await websocket.receive_text()
                data = json.loads(message)
                
                # Handle different message types
                if data.get("type") == "subscribe_session":
                    session_id = data.get("session_id")
                    if session_id:
                        await connection_manager.subscribe_to_session(client_id, session_id)
                
                elif data.get("type") == "heartbeat":
                    await connection_manager.handle_heartbeat(client_id)
                
                elif data.get("type") == "get_history":
                    session_id = data.get("session_id")
                    limit = data.get("limit", 50)
                    history = discovery_streamer.get_event_history(session_id, limit=limit)
                    await connection_manager.send_to_client(client_id, {
                        "type": "history_response",
                        "history": history,
                        "timestamp": datetime.now().isoformat()
                    })
                
            except WebSocketDisconnect:
                break
            except json.JSONDecodeError:
                await connection_manager.send_to_client(client_id, {
                    "type": "error",
                    "message": "Invalid JSON format",
                    "timestamp": datetime.now().isoformat()
                })
            except Exception as e:
                await connection_manager.send_to_client(client_id, {
                    "type": "error", 
                    "message": f"Server error: {str(e)}",
                    "timestamp": datetime.now().isoformat()
                })
                
    except WebSocketDisconnect:
        pass
    finally:
        await connection_manager.disconnect(client_id)