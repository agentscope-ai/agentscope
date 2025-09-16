#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FastAPI server for the Agent Discovery System.

This server provides REST API endpoints and WebSocket streaming for the 
Agent Discovery System frontend integration.
"""

import asyncio
import os
import sys
import json
import shutil
import tempfile
import configparser
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

# Import AgentScope components
from agentscope.model import GeminiChatModel
from agentscope.formatter import GeminiChatFormatter

# Import Discovery System components
from agentscope.discovery import DiscoveryWorkflow


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


# Global variables
app = FastAPI(title="Agent Discovery System API", version="1.0.0")
current_workflow: Optional[DiscoveryWorkflow] = None
current_session: Optional[Dict[str, Any]] = None
connected_clients: List[WebSocket] = []

# Enable CORS for frontend integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def setup_model_and_formatter():
    """Initialize the Gemini model and formatter."""
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
    
    model = GeminiChatModel(
        model_name="gemini-2.5-pro",
        api_key=api_key,
        stream=True,
        generate_kwargs={
            "temperature": 0.7,
        }
    )
    
    formatter = GeminiChatFormatter()
    return model, formatter


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


async def broadcast_progress(message: Dict[str, Any]):
    """Broadcast progress updates to all connected WebSocket clients."""
    if connected_clients:
        disconnected = []
        for client in connected_clients:
            try:
                await client.send_text(json.dumps(message))
            except:
                disconnected.append(client)
        
        # Remove disconnected clients
        for client in disconnected:
            connected_clients.remove(client)


@app.get("/")
async def serve_frontend():
    """Serve the main HTML frontend."""
    return FileResponse("discovery_agent_ui.html")


@app.post("/api/upload-knowledge-base")
async def upload_knowledge_base(files: List[UploadFile] = File(...)):
    """Upload MD files to create knowledge base."""
    try:
        # Create temporary directory for knowledge base
        temp_dir = tempfile.mkdtemp(prefix="discovery_kb_")
        
        # Process uploaded files
        processed_files = setup_knowledge_base_from_md_files(files, temp_dir)
        
        if not processed_files:
            raise HTTPException(status_code=400, detail="No valid MD files uploaded")
        
        # Store knowledge base path for later use
        global current_session
        current_session = {
            "knowledge_base_path": temp_dir,
            "uploaded_files": processed_files,
            "upload_time": datetime.now().isoformat()
        }
        
        await broadcast_progress({
            "type": "knowledge_base_uploaded",
            "files_count": len(processed_files),
            "files": processed_files
        })
        
        return {
            "success": True,
            "files_processed": len(processed_files),
            "files": processed_files,
            "message": f"Successfully uploaded {len(processed_files)} MD files"
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload knowledge base: {str(e)}")


@app.post("/api/start-discovery")
async def start_discovery(request: DiscoveryRequest):
    """Start a new discovery session."""
    global current_workflow, current_session
    
    try:
        if not current_session or "knowledge_base_path" not in current_session:
            raise HTTPException(status_code=400, detail="No knowledge base uploaded")
        
        # Initialize model and formatter
        model, formatter = setup_model_and_formatter()
        
        # Create discovery workflow
        current_workflow = DiscoveryWorkflow(
            model=model,
            formatter=formatter,
            storage_path="./discovery_storage",
            max_loops=request.max_loops,
            token_budget=request.token_budget,
            time_budget=request.time_budget,
            cost_budget=request.cost_budget,
        )
        
        await broadcast_progress({
            "type": "discovery_starting",
            "initial_idea": request.initial_idea,
            "focus_areas": request.focus_areas,
            "exploration_depth": request.exploration_depth
        })
        
        # Start discovery session
        start_result = await current_workflow.start_discovery(
            knowledge_base_path=current_session["knowledge_base_path"],
            initial_idea=request.initial_idea,
            focus_areas=request.focus_areas,
            exploration_depth=request.exploration_depth,
        )
        
        # Update session info
        current_session.update({
            "session_id": start_result["session_id"],
            "status": "running",
            "start_time": datetime.now().isoformat(),
            "request": request.dict()
        })
        
        await broadcast_progress({
            "type": "discovery_started",
            "session_id": start_result["session_id"],
            "status": "started"
        })
        
        # Start background task for exploration loops
        asyncio.create_task(run_discovery_loops())
        
        return {
            "success": True,
            "session_id": start_result["session_id"],
            "status": "started",
            "message": "Discovery session started successfully"
        }
        
    except Exception as e:
        await broadcast_progress({
            "type": "error",
            "message": f"Failed to start discovery: {str(e)}"
        })
        raise HTTPException(status_code=500, detail=f"Failed to start discovery: {str(e)}")


async def run_discovery_loops():
    """Background task to run discovery exploration loops."""
    global current_workflow, current_session
    
    if not current_workflow or not current_session:
        return
    
    try:
        loop_count = 0
        max_loops = current_session.get("request", {}).get("max_loops", 3)
        
        while current_workflow.session_active and loop_count < max_loops:
            loop_count += 1
            
            await broadcast_progress({
                "type": "exploration_loop_starting",
                "loop_number": loop_count,
                "total_loops": max_loops
            })
            
            # Run exploration loop
            loop_result = await current_workflow.run_exploration_loop()
            
            await broadcast_progress({
                "type": "exploration_loop_completed",
                "loop_number": loop_count,
                "total_loops": max_loops,
                "loop_result": loop_result
            })
            
            # Check if session terminated
            if loop_result.get("status") == "session_terminated":
                break
            
            # Small delay between loops
            await asyncio.sleep(1.0)
        
        # Generate final insights
        await broadcast_progress({
            "type": "generating_insights",
            "message": "Generating final insights..."
        })
        
        insight_result = await current_workflow.orchestrator.generate_final_insights()
        
        await broadcast_progress({
            "type": "performing_meta_analysis",
            "message": "Performing meta-analysis..."
        })
        
        meta_result = await current_workflow.orchestrator.perform_meta_analysis()
        
        # Get final results
        final_results = await current_workflow.user_proxy.get_final_results()
        
        # Update session
        current_session.update({
            "status": "completed",
            "end_time": datetime.now().isoformat(),
            "final_results": final_results,
            "insight_result": insight_result,
            "meta_result": meta_result
        })
        
        await broadcast_progress({
            "type": "discovery_completed",
            "session_id": current_session["session_id"],
            "final_results": final_results,
            "insights": insight_result,
            "meta_analysis": meta_result
        })
        
    except Exception as e:
        current_session["status"] = "error"
        current_session["error"] = str(e)
        
        await broadcast_progress({
            "type": "error",
            "message": f"Discovery failed: {str(e)}"
        })


@app.get("/api/discovery-status")
async def get_discovery_status():
    """Get current discovery session status."""
    global current_session
    
    if not current_session:
        return DiscoveryStatus(status="no_session")
    
    status = current_session.get("status", "unknown")
    session_id = current_session.get("session_id")
    
    # Calculate progress
    start_time = current_session.get("start_time")
    elapsed_time = 0.0
    if start_time:
        start_dt = datetime.fromisoformat(start_time)
        elapsed_time = (datetime.now() - start_dt).total_seconds()
    
    return DiscoveryStatus(
        session_id=session_id,
        status=status,
        elapsed_time=elapsed_time
    )


@app.get("/api/discovery-results")
async def get_discovery_results():
    """Get final discovery results."""
    global current_session
    
    if not current_session or current_session.get("status") != "completed":
        raise HTTPException(status_code=400, detail="No completed discovery session")
    
    final_results = current_session.get("final_results", {})
    insight_result = current_session.get("insight_result", {})
    meta_result = current_session.get("meta_result", {})
    
    # Extract results in a structured format
    discoveries = final_results.get("discoveries", [])
    insights = insight_result.get("insights", [])
    hypotheses = insight_result.get("hypotheses", [])
    questions = insight_result.get("questions", [])
    budget_utilization = final_results.get("budget_utilization", {})
    
    return DiscoveryResults(
        session_id=current_session["session_id"],
        status="completed",
        discoveries=discoveries,
        insights=insights,
        hypotheses=hypotheses,
        questions=questions,
        budget_utilization=budget_utilization,
        meta_analysis=meta_result
    )


@app.websocket("/ws/discovery-stream")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time progress updates."""
    await websocket.accept()
    connected_clients.append(websocket)
    
    try:
        # Send current status to new client
        if current_session:
            await websocket.send_text(json.dumps({
                "type": "status_update",
                "session": current_session
            }))
        
        # Keep connection alive
        while True:
            try:
                # Wait for messages (ping/pong to keep connection alive)
                message = await websocket.receive_text()
                if message == "ping":
                    await websocket.send_text("pong")
            except WebSocketDisconnect:
                break
            except Exception:
                break
                
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in connected_clients:
            connected_clients.remove(websocket)


@app.post("/api/stop-discovery")
async def stop_discovery():
    """Stop the current discovery session."""
    global current_workflow, current_session
    
    if not current_workflow or not current_session:
        raise HTTPException(status_code=400, detail="No active discovery session")
    
    try:
        termination_result = await current_workflow.terminate_session("user_request")
        current_session["status"] = "stopped"
        current_session["termination_result"] = termination_result
        
        await broadcast_progress({
            "type": "discovery_stopped",
            "message": "Discovery session stopped by user"
        })
        
        return {
            "success": True,
            "message": "Discovery session stopped",
            "termination_result": termination_result
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to stop discovery: {str(e)}")


# Serve static files (for the HTML frontend)
app.mount("/static", StaticFiles(directory="."), name="static")


if __name__ == "__main__":
    # Check for API key
    if not os.getenv("GEMINI_API_KEY"):
        print("❌ GEMINI_API_KEY not set. Please set your Gemini API key.")
        print("   You can get your API key from: https://makersuite.google.com/app/apikey")
        exit(1)
    
    print("🚀 Starting Agent Discovery System Server...")
    print("📚 Make sure to have MD files ready for upload")
    print("🌐 Server will be available at: http://localhost:8000")
    
    uvicorn.run(
        "discovery_server:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )