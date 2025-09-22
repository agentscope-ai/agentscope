#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Production-Ready AgentScope Discovery System Server

This server integrates all implemented components following the design specification
and AgentScope standards for a complete, production-ready discovery system.
"""

import asyncio
import os
import sys
import json
import shutil
import tempfile
import configparser
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime

# Add the src directory to the Python path
src_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(src_path))

from fastapi import FastAPI, UploadFile, File, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

# Import our implemented components
from discovery_coordinator import DiscoveryAgentCoordinator, DiscoveryConfig
from prompt_manager import DiscoveryPromptManager
from streaming_manager import (
    DiscoveryStreamer, ConnectionManager, DiscoveryAgentHooks,
    websocket_handler, heartbeat_task
)
from discovery_toolkit import DiscoveryTools
from ai_model_interceptor import wrap_all_models_in_coordinator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Pydantic models for API requests/responses
class DiscoveryRequest(BaseModel):
    initial_idea: str
    focus_areas: List[str] = []
    exploration_depth: str = "normal"  # "shallow", "normal", "deep"
    max_loops: int = 3
    token_budget: int = 10000
    time_budget: float = 1800.0
    cost_budget: float = 5.0


class DiscoveryStatus(BaseModel):
    session_id: Optional[str] = None
    status: str
    current_loop: int = 0
    total_loops: int = 0
    progress_percentage: float = 0.0
    current_step: str = ""
    elapsed_time: float = 0.0


class DiscoveryResults(BaseModel):
    session_id: str
    status: str
    discoveries: List[Dict[str, Any]] = []
    insights: List[str] = []
    hypotheses: List[str] = []
    questions: List[str] = []
    budget_utilization: Dict[str, Any] = {}
    meta_analysis: Dict[str, Any] = {}


# Global application state
app = FastAPI(
    title="AgentScope Discovery System API",
    version="2.0.0",
    description="Production-ready AI-powered discovery system using AgentScope"
)

# Initialize global components
connection_manager = ConnectionManager()
discovery_streamer = DiscoveryStreamer(connection_manager)
agent_hooks = DiscoveryAgentHooks(discovery_streamer)
prompt_manager = DiscoveryPromptManager()
discovery_tools = DiscoveryTools()

# Application state
current_coordinator: Optional[DiscoveryAgentCoordinator] = None
current_session: Optional[Dict[str, Any]] = None
background_tasks = set()

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def setup_discovery_config() -> DiscoveryConfig:
    """Initialize discovery configuration with API key validation."""
    # Try to get API key from environment variable first
    api_key = os.getenv("GEMINI_API_KEY")
    
    # If not found, try to read from config file
    if not api_key:
        config_path = Path(__file__).parent / "config.ini"
        if config_path.exists():
            config = configparser.ConfigParser()
            config.read(config_path)
            api_key = config.get("api", "gemini_api_key", fallback=None)
    
    if not api_key:
        raise ValueError(
            "Gemini API key not found. Please either:\n"
            "1. Set GEMINI_API_KEY environment variable, or\n"
            "2. Add your API key to config.ini file\n"
            "You can get your API key from: https://makersuite.google.com/app/apikey"
        )
    
    # Create configuration
    config = DiscoveryConfig()
    config.gemini_api_key = api_key
    
    logger.info("Discovery configuration initialized successfully")
    return config


def setup_knowledge_base_from_md_files(upload_files: List[UploadFile], base_path: str):
    """Set up knowledge base from uploaded MD files."""
    os.makedirs(base_path, exist_ok=True)
    
    processed_files = []
    
    for file in upload_files:
        if not file.filename.endswith('.md'):
            continue
            
        file_path = os.path.join(base_path, file.filename)
        
        # Save uploaded file
        with open(file_path, 'wb') as f:
            shutil.copyfileobj(file.file, f)
        
        processed_files.append({
            "filename": file.filename,
            "path": file_path,
            "size": os.path.getsize(file_path)
        })
    
    return processed_files


@app.get("/favicon.ico")
async def favicon():
    """Serve favicon."""
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/")
async def serve_frontend():
    """Serve the enhanced HTML frontend."""
    html_path = Path(__file__).parent / "enhanced_discovery_ui.html"
    if not html_path.exists():
        # Fallback to original UI
        html_path = Path(__file__).parent / "discovery_agent_ui.html"
    
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="Frontend HTML file not found")
    
    return FileResponse(str(html_path))


@app.post("/api/upload-knowledge-base")
async def upload_knowledge_base(files: List[UploadFile] = File(...)):
    """Upload MD files to create knowledge base."""
    global current_session
    
    try:
        # Validate files
        md_files = [f for f in files if f.filename.endswith('.md')]
        if not md_files:
            raise HTTPException(status_code=400, detail="No valid MD files uploaded")
        
        # Create temporary directory for knowledge base
        temp_dir = tempfile.mkdtemp(prefix="discovery_kb_")
        
        # Process uploaded files
        processed_files = setup_knowledge_base_from_md_files(md_files, temp_dir)
        
        # Load into discovery tools
        knowledge_file_data = []
        for file_info in processed_files:
            with open(file_info["path"], 'r', encoding='utf-8') as f:
                content = f.read()
                knowledge_file_data.append({
                    "filename": file_info["filename"],
                    "content": content
                })
        
        # Don't load into global discovery_tools here
        # Will be loaded into coordinator's discovery_tools after creation
        
        # Store knowledge base info for later use
        current_session = {
            "knowledge_base_path": temp_dir,
            "uploaded_files": processed_files,
            "knowledge_file_data": knowledge_file_data,
            "upload_time": datetime.now().isoformat()
        }
        
        # Stream upload event (use simple concept counting)
        total_concepts = sum(len(data["content"].split()) for data in knowledge_file_data)  # Simple word count
        await discovery_streamer.stream_knowledge_base_update(
            files_count=len(processed_files),
            concepts_added=total_concepts
        )
        
        logger.info(f"Successfully uploaded {len(processed_files)} MD files")
        
        return {
            "success": True,
            "files_processed": len(processed_files),
            "files": processed_files,
            "message": f"Successfully uploaded {len(processed_files)} MD files"
        }
        
    except Exception as e:
        logger.error(f"Failed to upload knowledge base: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to upload knowledge base: {str(e)}")


@app.post("/api/start-discovery")
async def start_discovery(request: DiscoveryRequest):
    """Start a new discovery session with full AgentScope integration."""
    global current_coordinator, current_session
    
    try:
        if not current_session or "knowledge_base_path" not in current_session:
            raise HTTPException(status_code=400, detail="No knowledge base uploaded")
        
        # Initialize discovery configuration
        config = setup_discovery_config()
        config.max_loops = request.max_loops
        config.token_budget = request.token_budget
        config.time_budget = request.time_budget
        config.cost_budget = request.cost_budget
        
        # Create discovery coordinator with AgentScope components
        current_coordinator = DiscoveryAgentCoordinator(config)
        
        # Load knowledge base into coordinator's discovery tools
        current_coordinator.load_knowledge_base(current_session["knowledge_file_data"])
        
        # Enable AI model call visibility
        current_coordinator = wrap_all_models_in_coordinator(current_coordinator)
        
        # Start discovery session
        session_result = await current_coordinator.start_discovery_session(
            knowledge_base_path=current_session["knowledge_base_path"],
            initial_idea=request.initial_idea,
            focus_areas=request.focus_areas,
            exploration_depth=request.exploration_depth
        )
        
        # Set up streaming for this session
        await discovery_streamer.set_current_session(session_result["session_id"])
        
        # Update session info
        current_session.update({
            "session_id": session_result["session_id"],
            "status": "running",
            "start_time": datetime.now().isoformat(),
            "request": request.dict(),
            "coordinator": current_coordinator
        })
        
        # Start background exploration task
        exploration_task = asyncio.create_task(run_discovery_exploration())
        background_tasks.add(exploration_task)
        exploration_task.add_done_callback(background_tasks.discard)
        
        logger.info(f"Discovery session {session_result['session_id']} started successfully")
        
        return {
            "success": True,
            "session_id": session_result["session_id"],
            "status": "started",
            "message": "Discovery session started successfully",
            "agents_initialized": list(current_coordinator.agents.keys())
        }
        
    except Exception as e:
        logger.error(f"Failed to start discovery: {e}")
        await discovery_streamer.stream_error(f"Failed to start discovery: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to start discovery: {str(e)}")


async def run_discovery_exploration():
    """Background task to run discovery exploration loops with full integration."""
    global current_coordinator, current_session
    
    if not current_coordinator or not current_session:
        return
    
    try:
        max_loops = current_session.get("request", {}).get("max_loops", 3)
        loop_count = 0
        
        while current_coordinator.session_active and loop_count < max_loops:
            loop_count += 1
            
            # Stream loop start
            await discovery_streamer.stream_loop_start(
                loop_number=loop_count,
                total_loops=max_loops,
                strategy=f"AgentScope multi-agent exploration loop {loop_count}"
            )
            
            # Stream progress
            await discovery_streamer.stream_loop_progress(
                loop_number=loop_count,
                total_loops=max_loops,
                current_step="Orchestrator planning exploration strategy...",
                progress_percentage=(loop_count - 1) / max_loops * 100
            )
            
            # Hook: Pre-reasoning for orchestrator
            await agent_hooks.pre_reasoning_hook(
                "OrchestratorAgent",
                f"Planning exploration strategy for loop {loop_count}"
            )
            
            # Run exploration loop with AgentScope integration
            loop_result = await current_coordinator.run_exploration_loop()
            
            # Stream discoveries and insights from the loop
            if "synthesis" in loop_result and loop_result["synthesis"]:
                synthesis = loop_result["synthesis"]
                
                # Stream discoveries
                for discovery in synthesis.get("discoveries", []):
                    await agent_hooks.discovery_hook(discovery, "SynthesisAgent")
                
                # Stream insights
                for insight in synthesis.get("insights", []):
                    await agent_hooks.insight_hook(insight, [])
            
            # Stream loop completion
            await discovery_streamer.stream_loop_complete(
                loop_number=loop_count,
                total_loops=max_loops,
                summary={
                    "discoveries_count": len(current_coordinator.session_data.get("discoveries", [])),
                    "insights_count": len(current_coordinator.session_data.get("insights", [])),
                    "hypotheses_count": len(current_coordinator.session_data.get("hypotheses", [])),
                    "status": loop_result.get("status", "completed")
                }
            )
            
            # Check if session should terminate
            if loop_result.get("status") == "session_terminated":
                break
            
            # Brief pause between loops
            await asyncio.sleep(2.0)
        
        # Generate final results
        final_results = await current_coordinator.get_final_results()
        
        # Update session status
        current_session.update({
            "status": "completed",
            "end_time": datetime.now().isoformat(),
            "final_results": final_results
        })
        
        # Stream completion
        await discovery_streamer.stream_session_termination(
            reason="exploration_completed",
            final_stats={
                "total_loops": loop_count,
                "discoveries": len(final_results.get("discoveries", [])),
                "insights": len(final_results.get("insights", [])),
                "duration": current_session.get("end_time", "") 
            }
        )
        
        logger.info(f"Discovery session completed with {len(final_results.get('discoveries', []))} discoveries")
        
    except Exception as e:
        logger.error(f"Discovery exploration failed: {e}")
        current_session["status"] = "error"
        current_session["error"] = str(e)
        
        await discovery_streamer.stream_error(
            f"Discovery exploration failed: {str(e)}",
            "exploration_error"
        )


@app.get("/api/discovery-status")
async def get_discovery_status():
    """Get current discovery session status."""
    global current_session
    
    if not current_session:
        return DiscoveryStatus(status="no_session")
    
    status = current_session.get("status", "unknown")
    session_id = current_session.get("session_id")
    
    # Calculate progress and elapsed time
    start_time = current_session.get("start_time")
    elapsed_time = 0.0
    if start_time:
        start_dt = datetime.fromisoformat(start_time)
        elapsed_time = (datetime.now() - start_dt).total_seconds()
    
    # Get current loop info if available
    current_loop = 0
    total_loops = current_session.get("request", {}).get("max_loops", 3)
    if current_coordinator and hasattr(current_coordinator, 'session_data'):
        current_loop = len(current_coordinator.session_data.get("loop_results", []))
    
    progress_percentage = (current_loop / total_loops) * 100 if total_loops > 0 else 0
    
    return DiscoveryStatus(
        session_id=session_id,
        status=status,
        current_loop=current_loop,
        total_loops=total_loops,
        progress_percentage=progress_percentage,
        elapsed_time=elapsed_time,
        current_step=current_session.get("current_step", "")
    )


@app.get("/api/discovery-results")
async def get_discovery_results():
    """Get final discovery results."""
    global current_session
    
    if not current_session or current_session.get("status") != "completed":
        raise HTTPException(status_code=400, detail="No completed discovery session")
    
    final_results = current_session.get("final_results", {})
    
    return DiscoveryResults(
        session_id=current_session["session_id"],
        status="completed",
        discoveries=final_results.get("discoveries", []),
        insights=final_results.get("insights", []),
        hypotheses=final_results.get("hypotheses", []),
        questions=final_results.get("questions", []),
        budget_utilization=final_results.get("budget_utilization", {}),
        meta_analysis=final_results.get("meta_analysis", {})
    )


@app.websocket("/ws/discovery-stream")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time discovery updates."""
    await websocket_handler(websocket)


@app.post("/api/stop-discovery")
async def stop_discovery():
    """Stop the current discovery session."""
    global current_coordinator, current_session
    
    if not current_coordinator or not current_session:
        raise HTTPException(status_code=400, detail="No active discovery session")
    
    try:
        # Terminate the session
        termination_result = await current_coordinator.terminate_session("user_request")
        current_session["status"] = "stopped"
        current_session["termination_result"] = termination_result
        
        # Stream termination
        await discovery_streamer.stream_session_termination(
            reason="user_request",
            final_stats={"status": "stopped_by_user"}
        )
        
        logger.info("Discovery session stopped by user request")
        
        return {
            "success": True,
            "message": "Discovery session stopped",
            "termination_result": termination_result
        }
        
    except Exception as e:
        logger.error(f"Failed to stop discovery: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to stop discovery: {str(e)}")


@app.get("/api/ai-call-stats")
async def get_ai_call_stats():
    """Get AI model call statistics."""
    from ai_model_interceptor import model_interceptor
    
    return {
        "active_calls": len(model_interceptor.active_calls),
        "total_calls": len(model_interceptor.call_history),
        "call_details": list(model_interceptor.active_calls.values()),
        "recent_calls": model_interceptor.call_history[-10:] if model_interceptor.call_history else []
    }


@app.get("/api/system-info")
async def get_system_info():
    """Get system information and health status."""
    return {
        "system": "AgentScope Discovery System",
        "version": "2.0.0",
        "status": "operational",
        "components": {
            "coordinator": current_coordinator is not None,
            "streamer": discovery_streamer is not None,
            "tools": discovery_tools is not None,
            "prompt_manager": prompt_manager is not None
        },
        "active_connections": len(connection_manager.active_connections),
        "session_active": current_session is not None,
        "timestamp": datetime.now().isoformat()
    }


@app.get("/api/agent-capabilities")
async def get_agent_capabilities():
    """Get information about available agents and their capabilities."""
    if not current_coordinator:
        return {"agents": [], "message": "No active coordinator"}
    
    agent_info = {}
    for agent_name, agent in current_coordinator.agents.items():
        agent_info[agent_name] = {
            "name": agent.name if hasattr(agent, 'name') else agent_name,
            "type": type(agent).__name__,
            "capabilities": getattr(agent, 'capabilities', []),
            "tools": getattr(agent, 'toolkit', None) is not None
        }
    
    return {
        "agents": agent_info,
        "total_agents": len(agent_info),
        "prompt_templates": prompt_manager.get_available_agent_types()
    }


# Serve static files (for additional resources)
static_dir = Path(__file__).parent
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
async def startup_event():
    """Initialize system on startup."""
    logger.info("Starting AgentScope Discovery System...")
    
    # Start heartbeat task
    heartbeat_task_handle = asyncio.create_task(heartbeat_task())
    background_tasks.add(heartbeat_task_handle)
    heartbeat_task_handle.add_done_callback(background_tasks.discard)
    
    logger.info("AgentScope Discovery System started successfully")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown."""
    logger.info("Shutting down AgentScope Discovery System...")
    
    # Cancel background tasks
    for task in background_tasks:
        task.cancel()
    
    # Close any active sessions
    if current_coordinator:
        try:
            await current_coordinator.terminate_session("server_shutdown")
        except:
            pass
    
    logger.info("AgentScope Discovery System shutdown complete")


if __name__ == "__main__":
    # Validate environment
    try:
        setup_discovery_config()
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        exit(1)
    
    print("🚀 Starting AgentScope Discovery System Server...")
    print("📚 Upload MD files to build your knowledge base")
    print("🧠 AI-powered discovery with multi-agent exploration")
    print("🌐 Server will be available at: http://localhost:8000")
    print("🔗 WebSocket streaming at: ws://localhost:8000/ws/discovery-stream")
    
    uvicorn.run(
        "production_discovery_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )