# -*- coding: utf-8 -*-
"""
Dynamic Agent Generation Framework - Runtime MBTI Agent Instantiation

This module provides the framework for dynamically creating MBTI+Domain Expert agents
at runtime based on task complexity analysis and template selection.
"""

import asyncio
import time
from typing import Dict, List, Any, Optional, Type
from dataclasses import dataclass
import shortuuid

from ..agent import ReActAgent
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit

from ._task_complexity_analyzer import ComplexityAnalysis, TaskComplexityAnalyzer
from ._mbti_domain_templates import MBTIDomainExpertTemplate, MBTIDomainTemplateRegistry
from ._hiva_evolution_engine import HiVAEvolutionEngine


@dataclass
class GeneratedAgent:
    """Represents a dynamically generated agent."""
    agent_id: str
    agent_instance: ReActAgent
    mbti_type: str
    domain: str
    template_used: MBTIDomainExpertTemplate
    creation_timestamp: float
    performance_metrics: Dict[str, float]
    active: bool = True


@dataclass
class AgentGenerationRequest:
    """Request for generating new agents."""
    complexity_analysis: ComplexityAnalysis
    model: ChatModelBase
    formatter: FormatterBase
    toolkit: Optional[Toolkit] = None
    memory: Optional[MemoryBase] = None
    additional_config: Dict[str, Any] = None


class DynamicAgentGenerator:
    """
    Core framework for dynamically generating MBTI+Domain Expert agents
    at runtime based on task requirements.
    """
    
    def __init__(
        self,
        template_registry: MBTIDomainTemplateRegistry,
        evolution_engine: HiVAEvolutionEngine,
        max_concurrent_agents: int = 10,
        agent_lifecycle_minutes: int = 60
    ):
        self.template_registry = template_registry
        self.evolution_engine = evolution_engine
        self.max_concurrent_agents = max_concurrent_agents
        self.agent_lifecycle_seconds = agent_lifecycle_minutes * 60
        
        # Active agent management
        self.active_agents: Dict[str, GeneratedAgent] = {}
        self.agent_pool: Dict[str, List[GeneratedAgent]] = {}  # Pool by type for reuse
        
        # Generation statistics
        self.generation_stats = {
            'total_generated': 0,
            'active_count': 0,
            'reused_count': 0,
            'performance_scores': [],
            'generation_times': []
        }
        
        # Cleanup task
        self.cleanup_task: Optional[asyncio.Task] = None
        self.cleanup_active = False
    
    async def start_agent_lifecycle_management(self) -> None:
        """Start automatic agent lifecycle management."""
        if self.cleanup_active:
            return
        
        self.cleanup_active = True
        self.cleanup_task = asyncio.create_task(self._agent_cleanup_loop())
        print("Agent lifecycle management started")
    
    async def stop_agent_lifecycle_management(self) -> None:
        """Stop agent lifecycle management."""
        self.cleanup_active = False
        if self.cleanup_task:
            self.cleanup_task.cancel()
            try:
                await self.cleanup_task
            except asyncio.CancelledError:
                pass
        print("Agent lifecycle management stopped")
    
    async def generate_agents(
        self,
        generation_request: AgentGenerationRequest
    ) -> List[GeneratedAgent]:
        """
        Generate MBTI+Domain Expert agents based on complexity analysis.
        
        Args:
            generation_request: Request containing complexity analysis and config
            
        Returns:
            List of generated agents ready for task execution
        """
        start_time = time.time()
        
        complexity_analysis = generation_request.complexity_analysis
        
        # Check if we can reuse existing agents
        reusable_agents = await self._find_reusable_agents(complexity_analysis)
        
        # Determine how many new agents we need to generate
        required_count = complexity_analysis.agent_count
        new_agents_needed = max(0, required_count - len(reusable_agents))
        
        # Check concurrent agent limit
        if len(self.active_agents) + new_agents_needed > self.max_concurrent_agents:
            await self._cleanup_inactive_agents()
            
            # Recalculate after cleanup
            if len(self.active_agents) + new_agents_needed > self.max_concurrent_agents:
                new_agents_needed = max(0, self.max_concurrent_agents - len(self.active_agents))
                print(f"Warning: Limited agent generation to {new_agents_needed} due to concurrent limit")
        
        # Generate new agents
        new_agents = []
        if new_agents_needed > 0:
            new_agents = await self._generate_new_agents(
                complexity_analysis, 
                generation_request,
                new_agents_needed
            )
        
        # Combine reused and new agents
        all_agents = reusable_agents + new_agents
        
        # Update statistics
        generation_time = time.time() - start_time
        self.generation_stats['generation_times'].append(generation_time)
        self.generation_stats['total_generated'] += len(new_agents)
        self.generation_stats['reused_count'] += len(reusable_agents)
        self.generation_stats['active_count'] = len(self.active_agents)
        
        print(f"Agent generation completed: {len(new_agents)} new, {len(reusable_agents)} reused, {generation_time:.2f}s")
        
        return all_agents
    
    async def _find_reusable_agents(
        self,
        complexity_analysis: ComplexityAnalysis
    ) -> List[GeneratedAgent]:
        """Find existing agents that can be reused for the task."""
        reusable_agents = []
        
        # Get required MBTI types and domains
        required_mbti_types = set(complexity_analysis.mbti_recommendations)
        required_domains = set(req.domain for req in complexity_analysis.domain_requirements)
        
        # Search through active agents
        for agent_id, generated_agent in self.active_agents.items():
            if not generated_agent.active:
                continue
                
            # Check if agent matches requirements
            mbti_match = generated_agent.mbti_type in required_mbti_types
            domain_match = generated_agent.domain in required_domains
            
            # Check performance threshold
            avg_performance = sum(generated_agent.performance_metrics.values()) / len(generated_agent.performance_metrics) if generated_agent.performance_metrics else 0.5
            performance_ok = avg_performance > 0.6
            
            if mbti_match and domain_match and performance_ok:
                reusable_agents.append(generated_agent)
                
                # Remove from requirements to avoid duplicates
                required_mbti_types.discard(generated_agent.mbti_type)
                if generated_agent.domain in required_domains:
                    required_domains.remove(generated_agent.domain)
                
                if len(reusable_agents) >= complexity_analysis.agent_count:
                    break
        
        return reusable_agents
    
    async def _generate_new_agents(
        self,
        complexity_analysis: ComplexityAnalysis,
        generation_request: AgentGenerationRequest,
        count: int
    ) -> List[GeneratedAgent]:
        """Generate new agents based on requirements."""
        
        # Get optimal templates
        domain_requirements = [req.domain for req in complexity_analysis.domain_requirements]
        mbti_preferences = complexity_analysis.mbti_recommendations
        
        optimal_templates = self.template_registry.get_optimal_templates(
            complexity_level=complexity_analysis.complexity_level.value,
            domain_requirements=domain_requirements,
            mbti_preferences=mbti_preferences
        )
        
        # Ensure we have enough templates
        if len(optimal_templates) < count:
            # Get additional templates if needed
            all_combinations = self.template_registry.list_available_combinations()
            additional_needed = count - len(optimal_templates)
            
            for mbti_type, domains in all_combinations.items():
                if additional_needed <= 0:
                    break
                for domain in domains:
                    if additional_needed <= 0:
                        break
                    template = self.template_registry.get_template(mbti_type, domain)
                    if template and template not in optimal_templates:
                        optimal_templates.append(template)
                        additional_needed -= 1
        
        # Generate agents from templates
        generated_agents = []
        for i in range(min(count, len(optimal_templates))):
            template = optimal_templates[i]
            
            try:
                agent = await self._create_agent_from_template(
                    template,
                    generation_request
                )
                generated_agents.append(agent)
                
                # Add to active agents
                self.active_agents[agent.agent_id] = agent
                
            except Exception as e:
                print(f"Error generating agent from template {template.mbti_type}_{template.domain_expertise.domain_name}: {e}")
        
        return generated_agents
    
    async def _create_agent_from_template(
        self,
        template: MBTIDomainExpertTemplate,
        generation_request: AgentGenerationRequest
    ) -> GeneratedAgent:
        """Create a single agent instance from a template."""
        
        # Generate unique agent ID
        agent_id = f"{template.mbti_type}_{template.domain_expertise.domain_name}_{shortuuid.uuid()[:8]}"
        
        # Get agent configuration from template
        agent_config = template.get_agent_config()
        
        # Create agent instance
        agent_instance = ReActAgent(
            name=agent_config['name'],
            sys_prompt=agent_config['sys_prompt'],
            model=generation_request.model,
            formatter=generation_request.formatter,
            toolkit=generation_request.toolkit or Toolkit(),
            memory=generation_request.memory or InMemoryMemory(),
            max_iters=10
        )
        
        # Create generated agent record
        generated_agent = GeneratedAgent(
            agent_id=agent_id,
            agent_instance=agent_instance,
            mbti_type=template.mbti_type,
            domain=template.domain_expertise.domain_name,
            template_used=template,
            creation_timestamp=time.time(),
            performance_metrics={},
            active=True
        )
        
        return generated_agent
    
    async def _agent_cleanup_loop(self) -> None:
        """Background loop for cleaning up inactive agents."""
        while self.cleanup_active:
            try:
                await self._cleanup_inactive_agents()
                await asyncio.sleep(300)  # Check every 5 minutes
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Agent cleanup error: {e}")
                await asyncio.sleep(60)
    
    async def _cleanup_inactive_agents(self) -> None:
        """Clean up agents that have exceeded their lifecycle."""
        current_time = time.time()
        agents_to_remove = []
        
        for agent_id, generated_agent in self.active_agents.items():
            age = current_time - generated_agent.creation_timestamp
            
            # Remove if agent is too old or explicitly marked inactive
            if age > self.agent_lifecycle_seconds or not generated_agent.active:
                agents_to_remove.append(agent_id)
        
        # Remove inactive agents
        for agent_id in agents_to_remove:
            generated_agent = self.active_agents.pop(agent_id)
            
            # Move to pool for potential reuse if performance is good
            avg_performance = sum(generated_agent.performance_metrics.values()) / len(generated_agent.performance_metrics) if generated_agent.performance_metrics else 0.0
            
            if avg_performance > 0.7:
                pool_key = f"{generated_agent.mbti_type}_{generated_agent.domain}"
                if pool_key not in self.agent_pool:
                    self.agent_pool[pool_key] = []
                
                # Keep only best performers in pool
                self.agent_pool[pool_key].append(generated_agent)
                self.agent_pool[pool_key] = sorted(
                    self.agent_pool[pool_key],
                    key=lambda x: sum(x.performance_metrics.values()) / len(x.performance_metrics) if x.performance_metrics else 0,
                    reverse=True
                )[:3]  # Keep top 3
        
        if agents_to_remove:
            print(f"Cleaned up {len(agents_to_remove)} inactive agents")
    
    async def update_agent_performance(
        self,
        agent_id: str,
        performance_metrics: Dict[str, float]
    ) -> None:
        """Update performance metrics for an agent."""
        
        if agent_id in self.active_agents:
            generated_agent = self.active_agents[agent_id]
            
            # Update metrics with moving average
            for metric, value in performance_metrics.items():
                if metric in generated_agent.performance_metrics:
                    # Moving average: 70% old, 30% new
                    old_value = generated_agent.performance_metrics[metric]
                    generated_agent.performance_metrics[metric] = old_value * 0.7 + value * 0.3
                else:
                    generated_agent.performance_metrics[metric] = value
            
            # Update overall statistics
            avg_performance = sum(generated_agent.performance_metrics.values()) / len(generated_agent.performance_metrics)
            self.generation_stats['performance_scores'].append(avg_performance)
            
            # Keep statistics manageable
            if len(self.generation_stats['performance_scores']) > 1000:
                self.generation_stats['performance_scores'] = self.generation_stats['performance_scores'][-1000:]
    
    async def deactivate_agent(self, agent_id: str) -> None:
        """Deactivate a specific agent."""
        if agent_id in self.active_agents:
            self.active_agents[agent_id].active = False
            print(f"Agent {agent_id} deactivated")
    
    def get_active_agents(self) -> List[GeneratedAgent]:
        """Get all currently active agents."""
        return [agent for agent in self.active_agents.values() if agent.active]
    
    def get_agent_by_id(self, agent_id: str) -> Optional[GeneratedAgent]:
        """Get a specific agent by ID."""
        return self.active_agents.get(agent_id)
    
    def get_generation_statistics(self) -> Dict[str, Any]:
        """Get generation and performance statistics."""
        active_agents = [agent for agent in self.active_agents.values() if agent.active]
        
        # Calculate average performance
        all_performances = []
        for agent in active_agents:
            if agent.performance_metrics:
                avg_perf = sum(agent.performance_metrics.values()) / len(agent.performance_metrics)
                all_performances.append(avg_perf)
        
        avg_performance = sum(all_performances) / len(all_performances) if all_performances else 0.0
        
        # Agent type distribution
        mbti_distribution = {}
        domain_distribution = {}
        
        for agent in active_agents:
            mbti_distribution[agent.mbti_type] = mbti_distribution.get(agent.mbti_type, 0) + 1
            domain_distribution[agent.domain] = domain_distribution.get(agent.domain, 0) + 1
        
        return {
            'active_agents': len(active_agents),
            'total_generated': self.generation_stats['total_generated'],
            'reused_count': self.generation_stats['reused_count'],
            'average_performance': avg_performance,
            'average_generation_time': sum(self.generation_stats['generation_times']) / len(self.generation_stats['generation_times']) if self.generation_stats['generation_times'] else 0,
            'mbti_distribution': mbti_distribution,
            'domain_distribution': domain_distribution,
            'pool_size': sum(len(agents) for agents in self.agent_pool.values())
        }
    
    def get_agent_insights(self, agent_id: str) -> Dict[str, Any]:
        """Get detailed insights about a specific agent."""
        if agent_id not in self.active_agents:
            return {'error': 'Agent not found'}
        
        agent = self.active_agents[agent_id]
        
        return {
            'agent_id': agent.agent_id,
            'mbti_type': agent.mbti_type,
            'domain': agent.domain,
            'creation_time': agent.creation_timestamp,
            'age_seconds': time.time() - agent.creation_timestamp,
            'active': agent.active,
            'performance_metrics': agent.performance_metrics,
            'template_info': {
                'strengths': agent.template_used.strengths,
                'approaches': agent.template_used.preferred_approaches,
                'collaboration_style': agent.template_used.collaboration_style
            }
        }