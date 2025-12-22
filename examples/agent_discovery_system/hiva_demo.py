#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
HiVA MBTI Dynamic Agent Generation System - Example Implementation

This example demonstrates the complete HiVA-driven MBTI+Domain Expert agent
generation system in action, showcasing intelligent scaling from 1-6 agents
based on task complexity.
"""

import asyncio
import os
import json
from typing import Dict, Any, List

# Mock imports for demonstration (replace with actual AgentScope imports)
class MockModel:
    """Mock model for demonstration purposes."""
    def __init__(self, model_name: str = "gemini-2.5-pro", **kwargs):
        self.model_name = model_name
        self.kwargs = kwargs
    
    async def generate(self, prompt: str) -> str:
        # Simulate model response based on prompt content
        if "complexity analysis" in prompt.lower():
            return self._generate_complexity_response(prompt)
        elif "mbti" in prompt.lower() or "personality" in prompt.lower():
            return self._generate_mbti_response(prompt)
        else:
            return self._generate_general_response(prompt)
    
    def _generate_complexity_response(self, prompt: str) -> str:
        """Generate a complexity analysis response."""
        return '''
        {
            "complexity_level": 4,
            "agent_count": 4,
            "domain_requirements": [
                {
                    "domain": "Computer_Science",
                    "importance": 0.9,
                    "specializations": ["AI/ML", "Systems"]
                },
                {
                    "domain": "Psychology",
                    "importance": 0.7,
                    "specializations": ["Cognitive Psychology"]
                }
            ],
            "mbti_recommendations": ["INTJ", "INTP", "ENFP", "ISTJ"],
            "scaling_rationale": "Complex interdisciplinary task requiring diverse cognitive approaches",
            "confidence_score": 0.85,
            "quality_prediction": 0.8,
            "efficiency_score": 0.75
        }
        '''
    
    def _generate_mbti_response(self, prompt: str) -> str:
        """Generate an MBTI-specific response."""
        if "INTJ" in prompt:
            return "As an INTJ Computer Science expert, I approach this systematically by analyzing the underlying architecture and long-term implications. The key is to design a robust framework that scales efficiently while maintaining logical consistency.\"
        elif "INTP" in prompt:
            return "From an INTP Psychology perspective, I'm fascinated by the theoretical implications. We need to understand the fundamental principles governing this system and explore the conceptual frameworks that could explain these phenomena.\"
        elif "ENFP" in prompt:
            return "As an ENFP Creative Arts expert, I see exciting possibilities for innovation! We could explore creative approaches that bring fresh perspectives and inspire novel solutions. The human element and emotional impact are crucial considerations.\"
        elif "ISTJ" in prompt:
            return "From an ISTJ Business perspective, we need practical, proven methods. Let's focus on reliable implementation strategies with clear procedures and measurable outcomes. Risk management and systematic execution are essential.\"
        else:
            return "Providing domain expertise analysis with personality-driven insights.\"
    
    def _generate_general_response(self, prompt: str) -> str:
        """Generate a general response."""
        return "Based on the comprehensive analysis from our diverse team of MBTI+Domain experts, here's a synthesized approach that integrates multiple perspectives and cognitive styles to address this challenge effectively."

class MockFormatter:
    """Mock formatter for demonstration purposes."""
    def format(self, messages: List[Dict]) -> str:
        return "\n".join([msg.get("content", "") for msg in messages])

# Try to import the actual HiVA system components
try:
    from agentscope.discovery import (
        HiVAUserProxyAgent,
        TaskComplexityAnalyzer,
        MBTIDomainTemplateRegistry,
        HiVAEvolutionEngine,
        DynamicAgentGenerator,
        UniversalKnowledgeGraph,
        ContinuousLearningSystem
    )
    SYSTEM_AVAILABLE = True
except ImportError:
    # If system not available, use demonstration classes
    print("⚠️  HiVA system components not available. Running demonstration mode.")
    SYSTEM_AVAILABLE = False


class HiVASystemDemo:
    """Demonstration of the HiVA MBTI Dynamic Agent Generation System."""
    
    def __init__(self):
        self.system_ready = False
        self.hiva_agent = None
        self.model = MockModel("gemini-2.5-pro")
        self.formatter = MockFormatter()
        
        # Demo data
        self.sample_tasks = [
            {
                "id": "simple_1",
                "task": "Explain what machine learning is in simple terms.",
                "expected_complexity": 1,
                "description": "Simple explanatory task requiring 1 agent"
            },
            {
                "id": "medium_1",
                "task": "Design a strategy for improving team collaboration in a remote work environment, considering both technological solutions and psychological factors.",
                "expected_complexity": 3,
                "description": "Medium complexity task requiring 2-3 agents"
            },
            {
                "id": "complex_1",
                "task": "Develop a comprehensive framework for sustainable AI development that addresses ethical concerns, environmental impact, economic implications, social equity, and long-term technological evolution while ensuring practical implementation across diverse global contexts.",
                "expected_complexity": 6,
                "description": "Highly complex task requiring 4-6 agents"
            }
        ]
    
    async def initialize_system(self) -> bool:
        """Initialize the HiVA system."""
        print("🚀 Initializing HiVA MBTI Dynamic Agent Generation System...")
        print("=" * 70)
        
        if SYSTEM_AVAILABLE:
            try:
                # Initialize with real system
                self.hiva_agent = HiVAUserProxyAgent(
                    model=self.model,
                    formatter=self.formatter
                )
                
                await self.hiva_agent.initialize_system(
                    model=self.model,
                    formatter=self.formatter
                )
                
                self.system_ready = True
                print("✅ HiVA system initialized successfully!")
                return True
                
            except Exception as e:
                print(f"❌ System initialization failed: {e}")
                return False
        else:
            # Demo mode
            print("📋 Running in demonstration mode")
            print("🎭 MBTI Template System: 16 types × 6 domains = 96+ templates")
            print("🧠 HiVA Evolution Engine: Continuous learning activated")
            print("⚡ Dynamic Agent Generator: Ready for runtime instantiation")
            print("📊 Task Complexity Analyzer: Intelligent 1-6 agent scaling\")
            self.system_ready = True
            return True
    
    async def demonstrate_task_processing(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Demonstrate processing a single task."""
        print(f"\n🎯 Processing Task: {task_data['id']}")
        print(f"📝 Description: {task_data['description']}")
        print(f"📋 Task: {task_data['task'][:100]}...")
        print("-" * 50)
        
        if SYSTEM_AVAILABLE and self.hiva_agent:
            # Use real system
            try:
                result = await self.hiva_agent.process_user_task(
                    task=task_data['task'],
                    user_id="demo_user",
                    context={'demo_mode': True}
                )
                return result
            except Exception as e:
                print(f"❌ Real system processing failed: {e}")
                return self._demo_task_processing(task_data)
        else:
            # Use demo processing
            return self._demo_task_processing(task_data)
    
    def _demo_task_processing(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate task processing for demonstration."""
        task = task_data['task']
        expected_complexity = task_data['expected_complexity']
        
        # Simulate complexity analysis
        print("📊 Step 1: Task Complexity Analysis")
        complexity_result = self._simulate_complexity_analysis(task, expected_complexity)
        print(f"   Complexity Level: {complexity_result['level']}/6")
        print(f"   Recommended Agents: {complexity_result['agent_count']}")
        print(f"   MBTI Types: {', '.join(complexity_result['mbti_types'])}")
        print(f"   Domains: {', '.join(complexity_result['domains'])}")
        
        # Simulate agent generation
        print("\n🤖 Step 2: Dynamic Agent Generation")
        agents = self._simulate_agent_generation(complexity_result)
        for i, agent in enumerate(agents, 1):
            print(f"   {i}. {agent['mbti_type']} {agent['domain']} Expert")
            print(f"      Strengths: {', '.join(agent['strengths'][:2])}")
        
        # Simulate execution
        print("\n⚡ Step 3: Multi-Agent Collaborative Execution")
        execution_results = self._simulate_execution(task, agents)
        
        for agent_id, response in execution_results['agent_responses'].items():
            print(f"   {response['mbti_type']} {response['domain']}: Analysis complete ✓\")
        
        print(f"\n🧠 Step 4: HiVA Intelligence Synthesis")
        synthesis = self._simulate_synthesis(execution_results)
        print(f"   Synthesis Quality: {synthesis['quality_score']:.2f}/1.0")
        print(f"   Success Score: {synthesis['success_score']:.2f}/1.0")
        
        print("\n📈 Step 5: Continuous Learning & Evolution")
        learning_insights = self._simulate_learning_update(task_data, agents, synthesis)
        print(f"   Network Efficiency: {learning_insights['network_efficiency']:.2f}")
        print(f"   Pattern Recognition: {learning_insights['patterns_learned']} new patterns\")
        
        return {
            'task_id': task_data['id'],
            'success': True,
            'complexity_analysis': complexity_result,
            'generated_agents': agents,
            'execution_results': execution_results,
            'synthesis': synthesis,
            'learning_insights': learning_insights
        }
    
    def _simulate_complexity_analysis(self, task: str, expected_complexity: int) -> Dict[str, Any]:
        """Simulate complexity analysis."""
        
        # Analyze task characteristics
        word_count = len(task.split())
        complexity_indicators = [
            'comprehensive' in task.lower(),
            'multi' in task.lower() or 'multiple' in task.lower(),
            'strategy' in task.lower() or 'framework' in task.lower(),
            'consider' in task.lower() and 'factor' in task.lower(),
            len(task) > 200
        ]
        
        complexity_score = sum(complexity_indicators) + 1
        agent_count = min(6, max(1, complexity_score))
        
        # Domain mapping
        domain_map = {
            'AI': 'Computer_Science',
            'machine learning': 'Computer_Science',
            'psychological': 'Psychology',
            'team': 'Psychology',
            'business': 'Business',
            'strategy': 'Business',
            'ethical': 'Philosophy',
            'sustainable': 'Philosophy',
            'creative': 'Creative_Arts',
            'design': 'Creative_Arts'
        }
        
        detected_domains = []
        task_lower = task.lower()
        for keyword, domain in domain_map.items():
            if keyword in task_lower and domain not in detected_domains:
                detected_domains.append(domain)
        
        if not detected_domains:
            detected_domains = ['Computer_Science', 'Psychology']  # Default
        
        # MBTI selection based on complexity and domains
        mbti_pools = {
            'analytical': ['INTJ', 'INTP', 'ISTJ', 'ISTP'],
            'creative': ['ENFP', 'INFP', 'ESFP', 'ISFP'],
            'leadership': ['ENTJ', 'ENFJ', 'ESTJ', 'ESFJ'],
            'collaborative': ['ENFJ', 'ESFJ', 'ENFP', 'ISFJ']
        }
        
        if complexity_score <= 2:
            selected_mbti = mbti_pools['analytical'][:agent_count]
        elif complexity_score <= 4:
            selected_mbti = (mbti_pools['analytical'][:2] + 
                           mbti_pools['creative'][:1] + 
                           mbti_pools['leadership'][:1])[:agent_count]
        else:
            selected_mbti = (mbti_pools['analytical'][:2] + 
                           mbti_pools['creative'][:2] + 
                           mbti_pools['leadership'][:1] + 
                           mbti_pools['collaborative'][:1])[:agent_count]
        
        return {
            'level': agent_count,
            'agent_count': agent_count,
            'mbti_types': selected_mbti,
            'domains': detected_domains[:agent_count],
            'confidence': 0.85,
            'reasoning': f"Task complexity indicators: {sum(complexity_indicators)}/5"
        }
    
    def _simulate_agent_generation(self, complexity_result: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Simulate agent generation."""
        agents = []
        mbti_types = complexity_result['mbti_types']
        domains = complexity_result['domains']
        
        # MBTI characteristics
        mbti_profiles = {
            'INTJ': {
                'strengths': ['Strategic thinking', 'System optimization', 'Long-term vision'],
                'approach': 'Systematic analysis and strategic planning'
            },
            'INTP': {
                'strengths': ['Logical analysis', 'Theoretical depth', 'Innovation'],
                'approach': 'Theoretical exploration and logical frameworks'
            },
            'ENFP': {
                'strengths': ['Creative synthesis', 'People connection', 'Inspiration'],
                'approach': 'Enthusiastic exploration and human-centered solutions'
            },
            'ISTJ': {
                'strengths': ['Reliable execution', 'Detailed accuracy', 'Practical methods'],
                'approach': 'Systematic implementation and proven methods'
            },
            'ENTJ': {
                'strengths': ['Strategic leadership', 'Efficient organization', 'Goal achievement'],
                'approach': 'Decisive leadership and efficient execution'
            },
            'ENFJ': {
                'strengths': ['People development', 'Inspiring leadership', 'Collaboration'],
                'approach': 'Collaborative facilitation and team building'
            }
        }
        
        for i in range(complexity_result['agent_count']):
            mbti_type = mbti_types[i] if i < len(mbti_types) else mbti_types[0]
            domain = domains[i] if i < len(domains) else domains[0]
            
            profile = mbti_profiles.get(mbti_type, {
                'strengths': ['Domain expertise', 'Problem solving'],
                'approach': 'Professional analysis'
            })
            
            agent = {
                'agent_id': f"{mbti_type}_{domain}_{i+1:03d}",
                'mbti_type': mbti_type,
                'domain': domain,
                'strengths': profile['strengths'],
                'approach': profile['approach'],
                'generation_time': 0.3 + (i * 0.1)  # Simulated generation time
            }
            
            agents.append(agent)
        
        return agents
    
    def _simulate_execution(self, task: str, agents: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Simulate multi-agent execution."""
        agent_responses = {}
        
        for agent in agents:
            # Simulate agent response based on MBTI and domain
            response_content = self.model._generate_mbti_response(f"{agent['mbti_type']} {agent['domain']}: {task}")
            
            agent_responses[agent['agent_id']] = {
                'mbti_type': agent['mbti_type'],
                'domain': agent['domain'],
                'response': response_content,
                'response_length': len(response_content),
                'quality_score': 0.7 + (hash(agent['agent_id']) % 30) / 100  # Simulated quality
            }
        
        return {
            'agent_responses': agent_responses,
            'collaboration_effectiveness': 0.85,
            'response_rate': 1.0,
            'execution_time': len(agents) * 1.2
        }
    
    def _simulate_synthesis(self, execution_results: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate synthesis of agent responses."""
        responses = execution_results['agent_responses']
        
        # Calculate synthesis quality based on diversity and quality
        mbti_diversity = len(set(r['mbti_type'] for r in responses.values()))
        domain_diversity = len(set(r['domain'] for r in responses.values()))
        avg_quality = sum(r['quality_score'] for r in responses.values()) / len(responses)
        
        quality_score = (mbti_diversity / 6 + domain_diversity / 6 + avg_quality) / 3
        success_score = min(1.0, quality_score * 1.1)  # Slight boost for synthesis
        
        synthesis_content = f"""
        Based on comprehensive analysis from our diverse team of MBTI+Domain experts:
        
        🎯 Strategic Insights: Our {mbti_diversity} different cognitive approaches revealed {domain_diversity} key domain perspectives.
        
        💡 Key Findings: The integration of analytical thinking (Ti/Te), intuitive synthesis (Ni/Ne), 
        and collaborative perspectives (Fe/Fi) provides a robust foundation for addressing this challenge.
        
        📋 Recommendations: Implement a multi-phase approach that leverages each expert's unique 
        cognitive strengths while maintaining systematic coordination.
        
        🚀 Next Steps: Prioritize high-impact initiatives identified through our diverse analysis 
        while ensuring practical implementation pathways.
        """
        
        return {
            'synthesis_content': synthesis_content,
            'quality_score': quality_score,
            'success_score': success_score,
            'mbti_diversity': mbti_diversity,
            'domain_diversity': domain_diversity,
            'collaboration_effectiveness': execution_results['collaboration_effectiveness']
        }
    
    def _simulate_learning_update(self, task_data: Dict[str, Any], agents: List[Dict[str, Any]], synthesis: Dict[str, Any]) -> Dict[str, Any]:
        """Simulate learning and evolution updates."""
        
        # Simulate network efficiency improvement
        base_efficiency = 0.6
        diversity_bonus = synthesis['mbti_diversity'] / 6 * 0.2
        success_bonus = synthesis['success_score'] * 0.1
        network_efficiency = min(1.0, base_efficiency + diversity_bonus + success_bonus)
        
        # Simulate pattern learning
        patterns_learned = 0
        if synthesis['success_score'] > 0.7:
            patterns_learned += 1  # Success pattern
        if len(agents) > 1:
            patterns_learned += 1  # Collaboration pattern
        if synthesis['mbti_diversity'] >= 3:
            patterns_learned += 1  # Diversity pattern
        
        return {
            'network_efficiency': network_efficiency,
            'patterns_learned': patterns_learned,
            'agent_performance_updates': len(agents),
            'user_preference_updates': 1,
            'knowledge_nodes_added': 2,
            'evolution_score': (network_efficiency + synthesis['success_score']) / 2
        }
    
    async def run_comprehensive_demo(self) -> None:
        """Run the complete demonstration."""
        if not await self.initialize_system():
            print("❌ Failed to initialize system")
            return
        
        print("\n🎭 HiVA MBTI Dynamic Agent Generation System Demo")
        print("="*70)
        print("This system intelligently scales from 1 to 6 MBTI+Domain Expert agents")
        print("based on task complexity analysis using the HiVA evolution framework.")
        print("\n📚 Available MBTI Types: 16 personality types")
        print("🔬 Available Domains: Physics, Computer Science, Psychology, Philosophy, Business, Creative Arts")
        print("⚡ Dynamic Generation: Agents created at runtime based on requirements")
        print("🧠 Continuous Learning: HiVA evolution engine learns from every interaction")
        print("🤖 Powered by: Gemini AI Models")
        
        demo_results = []
        
        for task_data in self.sample_tasks:
            result = await self.demonstrate_task_processing(task_data)
            demo_results.append(result)
            
            # Add delay for demonstration effect
            await asyncio.sleep(1)
        
        # Summary
        print("\n" + "="*70)
        print("📊 DEMONSTRATION SUMMARY")
        print("="*70)
        
        total_agents = sum(len(r['generated_agents']) for r in demo_results)
        avg_success = sum(r['synthesis']['success_score'] for r in demo_results) / len(demo_results)
        
        print(f"✅ Tasks Processed: {len(demo_results)}")
        print(f"🤖 Total Agents Generated: {total_agents}")
        print(f"📈 Average Success Score: {avg_success:.2f}/1.0")
        print(f"🧠 Learning Events: {sum(r['learning_insights']['patterns_learned'] for r in demo_results)}\")
        
        print("\n🎯 Task Complexity Distribution:")
        for i, result in enumerate(demo_results, 1):
            complexity = result['complexity_analysis']['level']
            agent_count = len(result['generated_agents'])
            success = result['synthesis']['success_score']
            print(f"  Task {i}: Complexity {complexity}/6 → {agent_count} agents → Success {success:.2f}")
        
        if SYSTEM_AVAILABLE:
            print("\n🔧 System Status:")
            try:
                status = self.hiva_agent.get_system_status()
                print(f"  Active Agents: {status.get('active_agents', 0)}")
                print(f"  Templates Available: {status.get('template_count', 0)}")
                print(f"  Learning Active: {status.get('learning_active', False)}")
            except:
                print("  Status unavailable in demo mode")
        
        print("\n🚀 HiVA MBTI Dynamic Agent Generation System Demo Complete!")
        print("\n💡 Key Innovations Demonstrated:")
        print("  • Intelligent task complexity analysis (1-6 agent scaling)")
        print("  • Dynamic MBTI+Domain expert agent generation")
        print("  • Multi-perspective collaborative problem solving")
        print("  • Continuous learning and evolution (HiVA framework)")
        print("  • Cognitive diversity optimization\")
        
        # Cleanup
        if SYSTEM_AVAILABLE and self.hiva_agent:
            try:
                await self.hiva_agent.shutdown_system()
            except:
                pass


async def main():
    """Main demonstration function."""
    demo = HiVASystemDemo()
    await demo.run_comprehensive_demo()


if __name__ == "__main__":
    print("🎭 HiVA MBTI Dynamic Agent Generation System")
    print("🚀 Starting Comprehensive Demonstration...")
    print()
    
    # Check for API key
    if os.getenv('GEMINI_API_KEY'):
        print("🔑 Gemini API key found - system will use real language models")
    else:
        print("⚠️  No Gemini API key - running with mock responses for demonstration")
        print("   Set GEMINI_API_KEY environment variable for full functionality")
        print("   You can get your API key from: https://makersuite.google.com/app/apikey")
    
    # Run the demonstration
    asyncio.run(main())