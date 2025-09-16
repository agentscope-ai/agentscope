# -*- coding: utf-8 -*-
"""
HiVA User Proxy Agent - Main orchestrator for the MBTI Dynamic Agent Generation System

This agent serves as the primary interface between users and the HiVA-driven
MBTI+Domain Expert agent ecosystem, coordinating task analysis, agent generation,
and execution.
"""

import asyncio
import time
import json
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict

from ..agent import ReActAgent
from ..message import Msg
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit

from ._task_complexity_analyzer import TaskComplexityAnalyzer, ComplexityAnalysis
from ._mbti_domain_templates import MBTIDomainTemplateRegistry
from ._hiva_evolution_engine import HiVAEvolutionEngine
from ._dynamic_agent_generator import DynamicAgentGenerator, AgentGenerationRequest, GeneratedAgent


@dataclass
class TaskExecution:
    """Represents a complete task execution."""
    task_id: str
    original_task: str
    complexity_analysis: ComplexityAnalysis
    generated_agents: List[GeneratedAgent]
    execution_results: Dict[str, Any]
    start_time: float
    end_time: float
    success_score: float
    user_feedback: Optional[Dict[str, Any]] = None


class HiVAUserProxyAgent(ReActAgent):
    """
    Main orchestrator agent for the HiVA MBTI Dynamic Agent Generation System.
    
    This agent coordinates:
    - Task complexity analysis
    - Dynamic MBTI+Domain expert agent generation
    - Multi-agent collaboration and execution
    - Continuous learning and evolution
    - User interaction and feedback integration
    """
    
    def __init__(
        self,
        name: str = "HiVA_UserProxy",
        model: ChatModelBase = None,
        formatter: FormatterBase = None,
        memory: MemoryBase = None,
        **kwargs
    ):
        
        system_prompt = """
        You are the HiVA User Proxy Agent, the main orchestrator of an advanced 
        MBTI+Domain Expert dynamic agent generation system.
        
        Your core responsibilities:
        1. Analyze user tasks for complexity and requirements
        2. Coordinate with the Task Complexity Analyzer to determine optimal agent deployment
        3. Orchestrate dynamic generation of MBTI+Domain Expert agents
        4. Facilitate multi-agent collaboration and execution
        5. Integrate continuous learning and evolution through HiVA
        6. Manage user feedback and personalization
        
        SYSTEM ARCHITECTURE:
        - Task Complexity Analyzer: Determines 1-6 agent scaling based on task complexity
        - MBTI+Domain Template Registry: Provides templates for 16 MBTI types × multiple domains
        - Dynamic Agent Generator: Creates agents at runtime from templates
        - HiVA Evolution Engine: Provides continuous learning and adaptation
        - Multi-agent collaboration networks with real-time optimization
        
        INTERACTION STYLE:
        - Be clear and informative about the system's analysis and decisions
        - Explain the reasoning behind agent selections and complexity assessments
        - Provide insights into how different MBTI types contribute to the solution
        - Facilitate learning from user feedback to improve future performance
        - Maintain transparency about the system's capabilities and limitations
        
        COLLABORATION COORDINATION:
        - Ensure each MBTI+Domain expert contributes their unique perspective
        - Facilitate effective communication between different cognitive styles
        - Synthesize diverse viewpoints into coherent, comprehensive solutions
        - Monitor collaboration effectiveness and adapt strategies
        
        Always remember: You are coordinating a dynamic, intelligent system that
        learns and evolves. Each interaction contributes to the system's growing
        understanding of optimal agent deployment and collaboration patterns.
        """
        
        super().__init__(
            name=name,
            sys_prompt=system_prompt,
            model=model,
            formatter=formatter,
            memory=memory or InMemoryMemory(),
            **kwargs
        )
        
        # Core system components
        self.complexity_analyzer: Optional[TaskComplexityAnalyzer] = None
        self.template_registry: Optional[MBTIDomainTemplateRegistry] = None
        self.evolution_engine: Optional[HiVAEvolutionEngine] = None
        self.agent_generator: Optional[DynamicAgentGenerator] = None
        
        # Execution tracking
        self.active_executions: Dict[str, TaskExecution] = {}
        self.execution_history: List[TaskExecution] = []
        
        # System state
        self.system_initialized = False
        self.learning_active = False
        
        # User context and personalization
        self.user_profiles: Dict[str, Dict[str, Any]] = {}
        self.session_context: Dict[str, Any] = {}
    
    async def initialize_system(
        self,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Optional[Toolkit] = None
    ) -> None:
        """Initialize the complete HiVA system."""
        
        if self.system_initialized:
            return
        
        print("🚀 Initializing HiVA MBTI Dynamic Agent Generation System...")
        
        # Initialize core components
        self.complexity_analyzer = TaskComplexityAnalyzer(
            model=model,
            formatter=formatter
        )
        
        self.template_registry = MBTIDomainTemplateRegistry()
        
        self.evolution_engine = HiVAEvolutionEngine()
        
        self.agent_generator = DynamicAgentGenerator(
            template_registry=self.template_registry,
            evolution_engine=self.evolution_engine
        )
        
        # Start background systems
        await self.evolution_engine.start_evolution_engine()
        await self.agent_generator.start_agent_lifecycle_management()
        
        self.system_initialized = True
        self.learning_active = True
        
        print("✅ HiVA System initialized successfully")
        print(f"📊 Available templates: {self.template_registry.get_template_count()}")
        print("🧠 Continuous learning activated")
    
    async def shutdown_system(self) -> None:
        """Shutdown the HiVA system."""
        
        if not self.system_initialized:
            return
        
        print("🔄 Shutting down HiVA system...")
        
        # Stop background systems
        if self.evolution_engine:
            await self.evolution_engine.stop_evolution_engine()
        
        if self.agent_generator:
            await self.agent_generator.stop_agent_lifecycle_management()
        
        self.learning_active = False
        self.system_initialized = False
        
        print("✅ HiVA system shutdown complete")
    
    async def process_user_task(
        self,
        task: str,
        user_id: str = "default",
        context: Dict[str, Any] = None,
        preferences: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Process a user task through the complete HiVA pipeline.
        
        Args:
            task: The task description from the user
            user_id: Identifier for the user (for personalization)
            context: Additional context for the task
            preferences: User preferences for agent selection
            
        Returns:
            Complete execution results including agent insights and recommendations
        """
        
        if not self.system_initialized:
            return {
                'error': 'HiVA system not initialized. Call initialize_system() first.',
                'success': False
            }
        
        task_id = f"task_{int(time.time())}_{user_id}"
        start_time = time.time()
        
        print(f"\n🎯 Processing task: {task[:100]}...")
        
        try:
            # Step 1: Analyze task complexity
            print("📊 Analyzing task complexity...")
            complexity_analysis = await self._analyze_task_complexity(
                task, context, user_id, preferences
            )
            
            # Step 2: Generate appropriate agents
            print(f"🤖 Generating {complexity_analysis.agent_count} MBTI+Domain expert agents...")
            generated_agents = await self._generate_task_agents(
                complexity_analysis, task_id
            )
            
            # Step 3: Execute task with generated agents
            print("⚡ Executing task with multi-agent collaboration...")
            execution_results = await self._execute_task_with_agents(
                task, generated_agents, complexity_analysis
            )
            
            # Step 4: Process results and learn
            final_results = await self._process_execution_results(
                task_id, task, complexity_analysis, generated_agents,
                execution_results, start_time, user_id
            )
            
            print(f"✅ Task completed in {time.time() - start_time:.2f}s")
            
            return final_results
            
        except Exception as e:
            error_result = {
                'task_id': task_id,
                'error': str(e),
                'success': False,
                'execution_time': time.time() - start_time
            }
            
            print(f"❌ Task execution failed: {e}")
            return error_result
    
    async def _analyze_task_complexity(
        self,
        task: str,
        context: Dict[str, Any],
        user_id: str,
        preferences: Dict[str, Any]
    ) -> ComplexityAnalysis:
        """Analyze task complexity and determine agent requirements."""
        
        # Get user historical patterns
        user_patterns = self.user_profiles.get(user_id, {})
        
        # Perform complexity analysis
        complexity_analysis = await self.complexity_analyzer.analyze_task_complexity(
            task=task,
            context=context,
            user_preferences=preferences,
            historical_patterns=user_patterns
        )
        
        print(f"📈 Complexity Level: {complexity_analysis.complexity_level.value}/6")
        print(f"🎯 Recommended Agents: {complexity_analysis.agent_count}")
        print(f"🧠 MBTI Types: {', '.join(complexity_analysis.mbti_recommendations)}")
        print(f"🔬 Domains: {', '.join([req.domain for req in complexity_analysis.domain_requirements])}")
        
        return complexity_analysis
    
    async def _generate_task_agents(
        self,
        complexity_analysis: ComplexityAnalysis,
        task_id: str
    ) -> List[GeneratedAgent]:
        """Generate agents based on complexity analysis."""
        
        generation_request = AgentGenerationRequest(
            complexity_analysis=complexity_analysis,
            model=self.model,
            formatter=self.formatter,
            toolkit=self.toolkit,
            memory=None,  # Each agent gets fresh memory
            additional_config={'task_id': task_id}
        )
        
        generated_agents = await self.agent_generator.generate_agents(generation_request)
        
        # Log agent details
        for agent in generated_agents:
            print(f"  🎭 {agent.mbti_type} {agent.domain} Expert - {agent.agent_id}")
        
        return generated_agents
    
    async def _execute_task_with_agents(
        self,
        task: str,
        agents: List[GeneratedAgent],
        complexity_analysis: ComplexityAnalysis
    ) -> Dict[str, Any]:
        """Execute the task using the generated agents."""
        
        execution_results = {
            'agent_responses': {},
            'collaboration_patterns': {},
            'synthesis': '',
            'success_metrics': {},
            'individual_insights': {}
        }
        
        # Sequential execution with each agent
        for agent in agents:
            try:
                # Prepare agent-specific prompt
                agent_prompt = self._prepare_agent_prompt(task, agent, complexity_analysis)
                
                # Execute with agent
                response = await agent.agent_instance.reply(agent_prompt)
                
                # Store response
                execution_results['agent_responses'][agent.agent_id] = {
                    'mbti_type': agent.mbti_type,
                    'domain': agent.domain,
                    'response': response.content,
                    'timestamp': time.time()
                }
                
                print(f"  ✓ {agent.mbti_type} {agent.domain} analysis complete")
                
            except Exception as e:
                print(f"  ❌ Error with {agent.agent_id}: {e}")
                execution_results['agent_responses'][agent.agent_id] = {
                    'error': str(e),
                    'mbti_type': agent.mbti_type,
                    'domain': agent.domain
                }
        
        # Synthesize results
        execution_results['synthesis'] = await self._synthesize_agent_responses(
            execution_results['agent_responses'], task, complexity_analysis
        )
        
        # Calculate success metrics
        execution_results['success_metrics'] = self._calculate_success_metrics(
            execution_results, agents
        )
        
        return execution_results
    
    def _prepare_agent_prompt(
        self,
        task: str,
        agent: GeneratedAgent,
        complexity_analysis: ComplexityAnalysis
    ) -> str:
        """Prepare a specific prompt for an agent."""
        
        prompt = f"""
        TASK: {task}
        
        CONTEXT:
        - This is a collaborative analysis with {complexity_analysis.agent_count} experts
        - Task complexity level: {complexity_analysis.complexity_level.value}/6
        - Your role: {agent.mbti_type} {agent.domain} Expert
        
        INSTRUCTIONS:
        1. Apply your {agent.mbti_type} cognitive approach to analyze this task
        2. Leverage your {agent.domain} domain expertise
        3. Provide insights that complement other expert perspectives
        4. Focus on aspects where your unique combination of personality and expertise adds value
        5. Be prepared to collaborate with other experts who may have different approaches
        
        Please provide your analysis, insights, and recommendations for this task.
        """
        
        return prompt
    
    async def _synthesize_agent_responses(
        self,
        agent_responses: Dict[str, Dict[str, Any]],
        original_task: str,
        complexity_analysis: ComplexityAnalysis
    ) -> str:
        """Synthesize all agent responses into a coherent final answer."""
        
        # Prepare synthesis prompt
        responses_summary = []
        for agent_id, response_data in agent_responses.items():
            if 'error' not in response_data:
                mbti_type = response_data['mbti_type']
                domain = response_data['domain']
                response = response_data['response']
                responses_summary.append(f"{mbti_type} {domain} Expert: {response}")
        
        synthesis_prompt = f"""
        ORIGINAL TASK: {original_task}
        
        EXPERT ANALYSES:
        {chr(10).join(responses_summary)}
        
        As the HiVA orchestrator, synthesize these diverse expert perspectives into:
        1. A comprehensive analysis that integrates all viewpoints
        2. Key insights that emerged from the multi-perspective approach
        3. Practical recommendations and next steps
        4. How the different MBTI cognitive styles contributed unique value
        5. A coherent final answer that addresses the original task
        
        Focus on creating synergy between the different approaches rather than just summarizing them.
        """
        
        synthesis_response = await self.reply(synthesis_prompt)
        return synthesis_response.content
    
    def _calculate_success_metrics(
        self,
        execution_results: Dict[str, Any],
        agents: List[GeneratedAgent]
    ) -> Dict[str, float]:
        """Calculate success metrics for the execution."""
        
        # Basic metrics
        successful_responses = len([
            r for r in execution_results['agent_responses'].values()
            if 'error' not in r
        ])
        
        response_rate = successful_responses / len(agents) if agents else 0
        
        # Response quality heuristics (simplified)
        total_response_length = sum(
            len(r.get('response', '')) 
            for r in execution_results['agent_responses'].values()
            if 'error' not in r
        )
        
        avg_response_length = total_response_length / successful_responses if successful_responses > 0 else 0
        quality_score = min(1.0, avg_response_length / 500)  # Normalize by expected length
        
        synthesis_quality = min(1.0, len(execution_results.get('synthesis', '')) / 1000)
        
        # Overall success score
        success_score = (response_rate * 0.4 + quality_score * 0.3 + synthesis_quality * 0.3)
        
        return {
            'response_rate': response_rate,
            'quality_score': quality_score,
            'synthesis_quality': synthesis_quality,
            'success_score': success_score,
            'agent_count': len(agents),
            'successful_responses': successful_responses
        }
    
    async def _process_execution_results(
        self,
        task_id: str,
        original_task: str,
        complexity_analysis: ComplexityAnalysis,
        generated_agents: List[GeneratedAgent],
        execution_results: Dict[str, Any],
        start_time: float,
        user_id: str
    ) -> Dict[str, Any]:
        """Process and store execution results, update learning systems."""
        
        end_time = time.time()
        success_score = execution_results['success_metrics']['success_score']
        
        # Create task execution record
        task_execution = TaskExecution(
            task_id=task_id,
            original_task=original_task,
            complexity_analysis=complexity_analysis,
            generated_agents=generated_agents,
            execution_results=execution_results,
            start_time=start_time,
            end_time=end_time,
            success_score=success_score
        )
        
        # Store execution
        self.execution_history.append(task_execution)
        
        # Update learning systems
        if self.learning_active:
            # Record with evolution engine
            agent_ids = [agent.agent_id for agent in generated_agents]
            await self.evolution_engine.record_task_execution(
                task=original_task,
                agents_used=agent_ids,
                execution_result=execution_results['success_metrics']
            )
            
            # Update agent performance metrics
            for agent in generated_agents:
                performance_metrics = {
                    'task_success': success_score,
                    'response_quality': execution_results['success_metrics']['quality_score'],
                    'collaboration_effectiveness': 0.8  # Simplified metric
                }
                
                await self.agent_generator.update_agent_performance(
                    agent.agent_id,
                    performance_metrics
                )
        
        # Prepare final response
        final_results = {
            'task_id': task_id,
            'success': True,
            'execution_time': end_time - start_time,
            'complexity_analysis': {
                'level': complexity_analysis.complexity_level.value,
                'agent_count': complexity_analysis.agent_count,
                'mbti_types': complexity_analysis.mbti_recommendations,
                'domains': [req.domain for req in complexity_analysis.domain_requirements],
                'confidence': complexity_analysis.confidence_score
            },
            'agent_details': [
                {
                    'agent_id': agent.agent_id,
                    'mbti_type': agent.mbti_type,
                    'domain': agent.domain,
                    'template_strengths': agent.template_used.strengths,
                    'collaboration_style': agent.template_used.collaboration_style
                }
                for agent in generated_agents
            ],
            'results': {
                'synthesis': execution_results['synthesis'],
                'individual_responses': execution_results['agent_responses'],
                'success_metrics': execution_results['success_metrics']
            },
            'system_insights': {
                'learning_active': self.learning_active,
                'total_executions': len(self.execution_history),
                'evolution_summary': self.evolution_engine.get_evolution_summary() if self.evolution_engine else {},
                'agent_statistics': self.agent_generator.get_generation_statistics() if self.agent_generator else {}
            }
        }
        
        return final_results
    
    async def provide_user_feedback(
        self,
        task_id: str,
        user_id: str,
        feedback: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Process user feedback for continuous learning."""
        
        if not self.learning_active:
            return {'message': 'Learning system not active'}
        
        # Find the execution
        execution = None
        for exec_record in self.execution_history:
            if exec_record.task_id == task_id:
                execution = exec_record
                break
        
        if not execution:
            return {'error': 'Task execution not found'}
        
        # Store feedback
        execution.user_feedback = feedback
        
        # Update user profile
        if user_id not in self.user_profiles:
            self.user_profiles[user_id] = {}
        
        # Record feedback with evolution engine
        await self.evolution_engine.record_user_feedback(user_id, feedback)
        
        print(f"📝 User feedback recorded for task {task_id}")
        
        return {
            'message': 'Feedback recorded successfully',
            'task_id': task_id,
            'learning_impact': 'Feedback will influence future agent selection and collaboration patterns'
        }
    
    def get_system_status(self) -> Dict[str, Any]:
        """Get comprehensive system status."""
        
        status = {
            'system_initialized': self.system_initialized,
            'learning_active': self.learning_active,
            'total_executions': len(self.execution_history),
            'active_agents': 0,
            'template_count': 0,
            'evolution_summary': {},
            'recent_performance': []
        }
        
        if self.system_initialized:
            if self.agent_generator:
                agent_stats = self.agent_generator.get_generation_statistics()
                status['active_agents'] = agent_stats['active_agents']
            
            if self.template_registry:
                status['template_count'] = self.template_registry.get_template_count()
            
            if self.evolution_engine:
                status['evolution_summary'] = self.evolution_engine.get_evolution_summary()
            
            # Recent performance
            recent_executions = self.execution_history[-10:] if self.execution_history else []
            status['recent_performance'] = [
                {
                    'task_id': exec.task_id,
                    'success_score': exec.success_score,
                    'agent_count': len(exec.generated_agents),
                    'execution_time': exec.end_time - exec.start_time
                }
                for exec in recent_executions
            ]
        
        return status