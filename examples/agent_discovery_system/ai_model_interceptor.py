#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI Model Call Interceptor

This module provides wrappers for AgentScope model calls to capture and stream
all AI interactions to the UI for maximum visibility.
"""

import time
import json
import asyncio
from typing import Dict, Any, Optional, AsyncGenerator
from functools import wraps

from streaming_manager import agent_hooks


class AIModelInterceptor:
    """Intercepts and streams AI model calls for visibility."""
    
    def __init__(self):
        self.active_calls = {}
        self.call_history = []
        
    def wrap_model_call(self, model_instance, agent_name: str):
        """Wrap a model instance to intercept all calls."""
        original_call = model_instance.__call__
        
        @wraps(original_call)
        async def wrapped_call(*args, **kwargs):
            # Extract request information
            messages = args[0] if args else kwargs.get('messages', [])
            request_data = {
                "messages": messages,
                "model_config": {
                    "model_name": getattr(model_instance, 'model_name', 'unknown'),
                    "temperature": getattr(model_instance, 'temperature', None),
                    "max_tokens": kwargs.get('max_tokens', getattr(model_instance, 'max_output_tokens', None)),
                    "stream": getattr(model_instance, 'stream', False)
                },
                "additional_params": {k: v for k, v in kwargs.items() 
                                   if k not in ['messages', 'max_tokens']}
            }
            
            # Start timing and create call ID
            start_time = time.time()
            call_id = await agent_hooks.ai_model_call_start_hook(
                agent_name=agent_name,
                model_name=getattr(model_instance, 'model_name', 'unknown'),
                request_data=request_data
            )
            
            self.active_calls[call_id] = {
                "start_time": start_time,
                "agent_name": agent_name,
                "model_name": getattr(model_instance, 'model_name', 'unknown'),
                "request_data": request_data
            }
            
            try:
                # Call the original method
                result = await original_call(*args, **kwargs)
                
                # Handle streaming vs non-streaming responses
                if hasattr(result, '__aiter__'):  # Streaming response
                    return self._wrap_streaming_response(result, call_id, agent_name, 
                                                       getattr(model_instance, 'model_name', 'unknown'))
                else:  # Non-streaming response
                    return await self._handle_complete_response(result, call_id, agent_name,
                                                              getattr(model_instance, 'model_name', 'unknown'),
                                                              start_time)
                    
            except Exception as e:
                # Handle errors
                duration_ms = (time.time() - start_time) * 1000
                error_details = {
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                    "traceback": str(e)
                }
                
                await agent_hooks.ai_model_call_error_hook(
                    call_id=call_id,
                    agent_name=agent_name,
                    model_name=getattr(model_instance, 'model_name', 'unknown'),
                    error_details=error_details,
                    duration_ms=duration_ms
                )
                
                # Clean up
                if call_id in self.active_calls:
                    del self.active_calls[call_id]
                
                raise e
        
        # Replace the original method
        model_instance._original_call = original_call
        model_instance.__call__ = wrapped_call
        return model_instance
    
    async def _wrap_streaming_response(self, async_generator, call_id: str, agent_name: str, model_name: str):
        """Wrap streaming response to capture each chunk."""
        accumulated_response = {
            "content": [],
            "token_usage": {},
            "metadata": {}
        }
        
        start_time = self.active_calls[call_id]["start_time"]
        
        async for chunk in async_generator:
            # Extract chunk information
            chunk_data = self._extract_response_data(chunk)
            
            # Stream the chunk
            await agent_hooks.ai_model_call_response_hook(
                call_id=call_id,
                agent_name=agent_name,
                model_name=model_name,
                response_chunk=chunk_data
            )
            
            # Accumulate response data
            if hasattr(chunk, 'content') and chunk.content:
                accumulated_response["content"].extend(chunk.content)
            if hasattr(chunk, 'usage') and chunk.usage:
                accumulated_response["token_usage"] = self._extract_usage_data(chunk.usage)
            
            yield chunk
        
        # Stream completion
        duration_ms = (time.time() - start_time) * 1000
        await agent_hooks.ai_model_call_complete_hook(
            call_id=call_id,
            agent_name=agent_name,
            model_name=model_name,
            final_response=accumulated_response,
            token_usage=accumulated_response.get("token_usage", {}),
            duration_ms=duration_ms
        )
        
        # Clean up
        if call_id in self.active_calls:
            del self.active_calls[call_id]
    
    async def _handle_complete_response(self, response, call_id: str, agent_name: str, 
                                      model_name: str, start_time: float):
        """Handle complete (non-streaming) response."""
        duration_ms = (time.time() - start_time) * 1000
        
        # Extract response data
        response_data = self._extract_response_data(response)
        token_usage = {}
        
        if hasattr(response, 'usage') and response.usage:
            token_usage = self._extract_usage_data(response.usage)
        
        # Stream completion
        await agent_hooks.ai_model_call_complete_hook(
            call_id=call_id,
            agent_name=agent_name,
            model_name=model_name,
            final_response=response_data,
            token_usage=token_usage,
            duration_ms=duration_ms
        )
        
        # Clean up
        if call_id in self.active_calls:
            del self.active_calls[call_id]
        
        return response
    
    def _extract_response_data(self, response) -> Dict[str, Any]:
        """Extract relevant data from response object."""
        data = {}
        
        if hasattr(response, 'content'):
            if isinstance(response.content, list):
                data["content"] = [self._serialize_content_block(block) for block in response.content]
            else:
                data["content"] = str(response.content)
        
        if hasattr(response, 'role'):
            data["role"] = response.role
            
        if hasattr(response, 'metadata'):
            data["metadata"] = response.metadata
            
        # Handle any additional attributes
        for attr in ['model', 'finish_reason', 'id']:
            if hasattr(response, attr):
                data[attr] = getattr(response, attr)
        
        return data
    
    def _serialize_content_block(self, block) -> Dict[str, Any]:
        """Serialize content blocks for streaming."""
        if hasattr(block, 'type'):
            result = {"type": block.type}
            
            if hasattr(block, 'text'):
                result["text"] = block.text
            elif hasattr(block, 'content'):
                result["content"] = block.content
                
            if hasattr(block, 'thinking'):
                result["thinking"] = block.thinking
                
            if hasattr(block, 'name') and hasattr(block, 'input'):
                result["tool_use"] = {
                    "name": block.name,
                    "input": block.input
                }
            
            return result
        else:
            return {"type": "unknown", "content": str(block)}
    
    def _extract_usage_data(self, usage) -> Dict[str, Any]:
        """Extract token usage data."""
        usage_data = {}
        
        for attr in ['input_tokens', 'output_tokens', 'total_tokens', 'time']:
            if hasattr(usage, attr):
                usage_data[attr] = getattr(usage, attr)
        
        return usage_data


# Global interceptor instance
model_interceptor = AIModelInterceptor()


def enable_ai_model_visibility(agent, agent_name: str):
    """Enable AI model call visibility for an agent."""
    if hasattr(agent, 'model'):
        model_interceptor.wrap_model_call(agent.model, agent_name)
    return agent


def wrap_all_models_in_coordinator(coordinator):
    """Wrap all models in the discovery coordinator for full visibility."""
    for agent_name, agent in coordinator.agents.items():
        enable_ai_model_visibility(agent, agent_name)
    return coordinator