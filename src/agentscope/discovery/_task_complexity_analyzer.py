# -*- coding: utf-8 -*-
"""
Task Complexity Analyzer - Core component for HiVA-Driven MBTI Dynamic Agent Generation

This module implements the intelligent analyzer that determines task complexity and 
scales MBTI+Domain Expert agent deployment from 1 to 6 agents based on complexity assessment.
"""

import re
import json
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from ..agent import ReActAgent
from ..message import Msg
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase


class ComplexityLevel(Enum):
    """Enumeration for task complexity levels."""
    SIMPLE = 1
    SIMPLE_MEDIUM = 2
    MEDIUM = 3
    MEDIUM_COMPLEX = 4
    COMPLEX = 5
    HIGHLY_COMPLEX = 6


@dataclass
class DomainRequirement:
    """Represents a domain expertise requirement."""
    domain: str
    importance: float  # 0.0 to 1.0
    specializations: List[str]
    cognitive_style_preference: Optional[str] = None


@dataclass
class ComplexityAnalysis:
    """Results of task complexity analysis."""
    complexity_level: ComplexityLevel
    agent_count: int
    domain_requirements: List[DomainRequirement]
    mbti_recommendations: List[str]
    scaling_rationale: str
    confidence_score: float
    quality_prediction: float
    efficiency_score: float
    analysis_factors: Dict[str, float]


class TaskComplexityAnalyzer(ReActAgent):
    """
    Intelligent analyzer that determines task complexity and scales 
    MBTI+Domain Expert agent deployment from 1 to 6 agents.
    
    This component is the heart of the HiVA-driven dynamic agent generation system,
    providing intelligent scaling decisions based on comprehensive task analysis.
    """
    
    def __init__(
        self,
        name: str = "TaskComplexityAnalyzer",
        model: ChatModelBase = None,
        formatter: FormatterBase = None,
        memory: MemoryBase = None,
        **kwargs
    ):
        analyzer_prompt = """
        You are a Task Complexity Analyzer with expertise in determining optimal 
        MBTI+Domain Expert agent deployment strategies for the HiVA framework.
        
        Your role is to analyze incoming tasks and intelligently scale agent deployment
        from 1 to 6 MBTI+Domain Expert agents based on complexity assessment.
        
        COMPLEXITY SCALING LOGIC:
        
        Level 1 (Simple Tasks) - Deploy 1 MBTI+Domain Expert Agent:
        - Single-domain focused problems
        - Clear, well-defined objectives with minimal ambiguity
        - Limited scope and variables
        - Straightforward fact-finding, explanation, or basic analysis
        - Examples: "Explain photosynthesis", "Analyze this poem's structure", "Define quantum entanglement"
        
        Level 2-3 (Medium Tasks) - Deploy 2-3 MBTI+Domain Expert Agents:
        - Cross-domain connections emerging
        - Multiple perspectives would add significant value
        - Moderate complexity with some ambiguity or uncertainty
        - Balanced analysis requirements across 2-3 domains
        - Examples: "Impact of AI on creative industries", "Sustainable urban planning strategies"
        
        Level 4-6 (Complex Tasks) - Deploy 4-6 MBTI+Domain Expert Agents:
        - Highly complex, multi-faceted problems
        - Multiple domains intersecting with non-obvious connections
        - High uncertainty, ambiguity, or conflicting requirements
        - Strategic planning with long-term implications
        - Comprehensive analysis needed from diverse perspectives
        - Examples: "Climate change solutions", "Future of human consciousness", "Redesign global economic systems"
        
        ANALYSIS FACTORS TO EVALUATE:
        1. Domain breadth (single vs. multi-domain complexity)
        2. Problem ambiguity level (clear vs. uncertain vs. paradoxical)
        3. Required perspective diversity (homogeneous vs. heterogeneous thinking)
        4. Analysis depth needed (surface vs. deep vs. foundational)
        5. Time sensitivity vs. thoroughness trade-off
        6. Interdisciplinary connection potential
        7. Creative vs. analytical thinking requirements
        8. Stakeholder complexity and conflicting interests
        9. Ethical implications and considerations
        10. Innovation vs. optimization focus
        
        MBTI COGNITIVE FUNCTION CONSIDERATIONS:
        - Ni (Introverted Intuition): Pattern recognition, future implications, system insights
        - Ne (Extraverted Intuition): Brainstorming, possibility exploration, creative connections
        - Ti (Introverted Thinking): Logical analysis, systematic reasoning, precision
        - Te (Extraverted Thinking): Efficiency, implementation, objective organization
        - Fi (Introverted Feeling): Values alignment, authentic assessment, individual impact
        - Fe (Extraverted Feeling): Group harmony, social impact, consensus building
        - Si (Introverted Sensing): Detailed accuracy, historical context, practical grounding
        - Se (Extraverted Sensing): Real-time adaptation, immediate solutions, hands-on approach
        
        OUTPUT REQUIREMENTS:
        Provide structured analysis in JSON format with:
        - complexity_level (1-6)
        - agent_count (recommended number of agents)
        - domain_requirements (list of domains and importance scores)
        - mbti_recommendations (specific MBTI types that would excel)
        - scaling_rationale (detailed reasoning for the decision)
        - confidence_score (0.0-1.0)
        - quality_prediction (expected analysis quality 0.0-1.0)
        - efficiency_score (resource efficiency 0.0-1.0)
        - analysis_factors (scores for each evaluation factor)
        """
        
        super().__init__(
            name=name,
            sys_prompt=analyzer_prompt,
            model=model,
            formatter=formatter,
            memory=memory,
            **kwargs
        )
        
        # Analysis history for pattern learning
        self.complexity_history: List[Dict[str, Any]] = []
        self.user_patterns: Dict[str, Any] = {}
        
        # Domain expertise mapping
        self.domain_keywords = self._initialize_domain_keywords()
        
        # MBTI cognitive function strengths
        self.mbti_strengths = self._initialize_mbti_strengths()
    
    def _initialize_domain_keywords(self) -> Dict[str, List[str]]:
        """Initialize domain keyword mappings for analysis."""
        return {
            "science": [
                "physics", "chemistry", "biology", "mathematics", "research",
                "experiment", "hypothesis", "theory", "data", "analysis"
            ],
            "technology": [
                "programming", "software", "AI", "machine learning", "algorithm",
                "computer", "digital", "automation", "system", "network"
            ],
            "humanities": [
                "literature", "philosophy", "history", "ethics", "culture",
                "society", "language", "art", "religion", "psychology"
            ],
            "business": [
                "strategy", "management", "finance", "marketing", "economics",
                "leadership", "organization", "profit", "market", "competition"
            ],
            "creative": [
                "design", "artistic", "creative", "innovation", "imagination",
                "aesthetic", "visual", "music", "writing", "expression"
            ],
            "social": [
                "community", "social", "relationship", "communication", "team",
                "collaboration", "culture", "diversity", "inclusion", "society"
            ],
            "environmental": [
                "environment", "sustainability", "climate", "ecology", "conservation",
                "renewable", "green", "carbon", "ecosystem", "biodiversity"
            ],
            "health": [
                "health", "medical", "wellness", "therapy", "disease", "treatment",
                "mental health", "nutrition", "fitness", "healthcare"
            ]
        }
    
    def _initialize_mbti_strengths(self) -> Dict[str, Dict[str, Any]]:
        """Initialize MBTI type strengths and preferences."""
        return {
            'INTJ': {
                'functions': 'Ni-Te-Fi-Se',
                'strengths': ['strategic thinking', 'system optimization', 'long-term vision', 'complex problem solving'],
                'domains': ['science', 'technology', 'strategy'],
                'complexity_preference': 'high',
                'approach': 'How does this fit the bigger picture? What systems are at play?'
            },
            'INTP': {
                'functions': 'Ti-Ne-Si-Fe',
                'strengths': ['logical analysis', 'theoretical exploration', 'precision', 'innovation'],
                'domains': ['science', 'technology', 'humanities'],
                'complexity_preference': 'high',
                'approach': 'What are the underlying principles? How can we refine this?'
            },
            'ENTJ': {
                'functions': 'Te-Ni-Se-Fi',
                'strengths': ['leadership', 'implementation', 'efficiency', 'strategic planning'],
                'domains': ['business', 'technology', 'social'],
                'complexity_preference': 'medium-high',
                'approach': 'What\'s the most effective way to achieve results?'
            },
            'ENTP': {
                'functions': 'Ne-Ti-Fe-Si',
                'strengths': ['innovation', 'brainstorming', 'possibility exploration', 'adaptability'],
                'domains': ['creative', 'technology', 'business'],
                'complexity_preference': 'medium-high',
                'approach': 'What if we tried differently? What connections exist?'
            },
            'INFJ': {
                'functions': 'Ni-Fe-Ti-Se',
                'strengths': ['insight', 'empathy', 'holistic thinking', 'value alignment'],
                'domains': ['humanities', 'social', 'health'],
                'complexity_preference': 'medium-high',
                'approach': 'What does this mean for people? What\'s the deeper significance?'
            },
            'INFP': {
                'functions': 'Fi-Ne-Si-Te',
                'strengths': ['authenticity', 'creativity', 'individual focus', 'value-driven analysis'],
                'domains': ['creative', 'humanities', 'social'],
                'complexity_preference': 'medium',
                'approach': 'Is this aligned with core values? What feels authentic?'
            },
            'ENFJ': {
                'functions': 'Fe-Ni-Se-Ti',
                'strengths': ['people development', 'harmony', 'communication', 'inspiration'],
                'domains': ['social', 'health', 'humanities'],
                'complexity_preference': 'medium',
                'approach': 'How can we bring people together? What serves the greater good?'
            },
            'ENFP': {
                'functions': 'Ne-Fi-Te-Si',
                'strengths': ['enthusiasm', 'inspiration', 'possibility focus', 'people connection'],
                'domains': ['creative', 'social', 'business'],
                'complexity_preference': 'medium',
                'approach': 'What excites people about this? What new possibilities emerge?'
            },
            'ISTJ': {
                'functions': 'Si-Te-Fi-Ne',
                'strengths': ['reliability', 'thoroughness', 'systematic approach', 'practical focus'],
                'domains': ['business', 'science', 'health'],
                'complexity_preference': 'low-medium',
                'approach': 'What\'s the proven method? How do we ensure accuracy?'
            },
            'ISFJ': {
                'functions': 'Si-Fe-Ti-Ne',
                'strengths': ['detailed care', 'practical support', 'thoroughness', 'service orientation'],
                'domains': ['health', 'social', 'humanities'],
                'complexity_preference': 'low-medium',
                'approach': 'How can we help people? What practical needs exist?'
            },
            'ESTJ': {
                'functions': 'Te-Si-Ne-Fi',
                'strengths': ['organization', 'efficiency', 'implementation', 'results focus'],
                'domains': ['business', 'technology', 'environmental'],
                'complexity_preference': 'medium',
                'approach': 'What\'s the most efficient way to get results?'
            },
            'ESFJ': {
                'functions': 'Fe-Si-Ne-Ti',
                'strengths': ['harmony', 'support', 'practical help', 'team building'],
                'domains': ['social', 'health', 'business'],
                'complexity_preference': 'low-medium',
                'approach': 'How does this affect everyone? What support is needed?'
            },
            'ISTP': {
                'functions': 'Ti-Se-Ni-Fe',
                'strengths': ['hands-on problem solving', 'technical skill', 'adaptability', 'efficiency'],
                'domains': ['technology', 'science', 'environmental'],
                'complexity_preference': 'medium',
                'approach': 'What works in practice? How can we optimize this?'
            },
            'ISFP': {
                'functions': 'Fi-Se-Ni-Te',
                'strengths': ['aesthetic appreciation', 'individual focus', 'adaptability', 'authenticity'],
                'domains': ['creative', 'health', 'environmental'],
                'complexity_preference': 'low-medium',
                'approach': 'What feels right? How does this express individual values?'
            },
            'ESTP': {
                'functions': 'Se-Ti-Fe-Ni',
                'strengths': ['real-time adaptation', 'practical solutions', 'hands-on analysis', 'crisis management'],
                'domains': ['business', 'technology', 'social'],
                'complexity_preference': 'low-medium',
                'approach': 'What works right now? What can we try immediately?'
            },
            'ESFP': {
                'functions': 'Se-Fi-Te-Ni',
                'strengths': ['enthusiasm', 'people connection', 'practical help', 'positive energy'],
                'domains': ['creative', 'social', 'health'],
                'complexity_preference': 'low',
                'approach': 'How can we make this enjoyable? What brings out the best in people?'
            }
        }
    
    async def analyze_task_complexity(
        self,
        task: str,
        context: Dict[str, Any] = None,
        user_preferences: Dict[str, Any] = None,
        historical_patterns: Dict[str, Any] = None
    ) -> ComplexityAnalysis:
        """
        Analyze task and determine optimal MBTI+Domain Expert agent deployment strategy.
        
        Args:
            task: The task description to analyze
            context: Additional context information
            user_preferences: User's preferences and patterns
            historical_patterns: Historical analysis patterns for learning
        
        Returns:
            ComplexityAnalysis: Structured analysis results
        """
        
        # Prepare analysis prompt
        analysis_prompt = f"""
        TASK TO ANALYZE: {task}
        
        CONTEXT: {json.dumps(context or {}, indent=2)}
        USER_PREFERENCES: {json.dumps(user_preferences or {}, indent=2)}
        HISTORICAL_PATTERNS: {json.dumps(historical_patterns or {}, indent=2)}
        
        Analyze this task and provide a structured complexity assessment that will determine
        the optimal number and types of MBTI+Domain Expert agents to deploy (1-6 agents).
        
        Consider all 10 analysis factors and provide specific MBTI type recommendations
        with detailed reasoning for each decision.
        
        Respond with a JSON object containing:
        {{
            "complexity_level": <integer 1-6>,
            "agent_count": <recommended number of agents>,
            "domain_requirements": [
                {{
                    "domain": "<domain name>",
                    "importance": <float 0.0-1.0>,
                    "specializations": ["<specialization>", ...],
                    "cognitive_style_preference": "<MBTI preference if any>"
                }}, ...
            ],
            "mbti_recommendations": ["<MBTI type>", ...],
            "scaling_rationale": "<detailed reasoning for scaling decision>",
            "confidence_score": <float 0.0-1.0>,
            "quality_prediction": <float 0.0-1.0>,
            "efficiency_score": <float 0.0-1.0>,
            "analysis_factors": {{
                "domain_breadth": <float 0.0-1.0>,
                "problem_ambiguity": <float 0.0-1.0>,
                "perspective_diversity_needed": <float 0.0-1.0>,
                "analysis_depth_required": <float 0.0-1.0>,
                "time_vs_thoroughness": <float 0.0-1.0>,
                "interdisciplinary_potential": <float 0.0-1.0>,
                "creative_vs_analytical": <float 0.0-1.0>,
                "stakeholder_complexity": <float 0.0-1.0>,
                "ethical_implications": <float 0.0-1.0>,
                "innovation_requirements": <float 0.0-1.0>
            }}
        }}
        """
        
        # Get LLM analysis
        response = await self.reply(analysis_prompt)
        
        # Parse and validate response
        try:
            analysis_data = self._parse_analysis_response(response.content)
            complexity_analysis = self._create_complexity_analysis(analysis_data)
            
            # Store for learning
            self._record_analysis(task, context, complexity_analysis)
            
            return complexity_analysis
            
        except Exception as e:
            # Fallback to rule-based analysis if LLM parsing fails
            print(f"Warning: LLM analysis parsing failed ({e}), using fallback analysis")
            return self._fallback_analysis(task, context, user_preferences)
    
    def _parse_analysis_response(self, response_content: str) -> Dict[str, Any]:
        """Parse the LLM response and extract structured analysis data."""
        
        # Extract JSON from response
        json_match = re.search(r'\{.*\}', response_content, re.DOTALL)
        if not json_match:
            raise ValueError("No JSON found in response")
        
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in response: {e}")
    
    def _create_complexity_analysis(self, data: Dict[str, Any]) -> ComplexityAnalysis:
        """Create ComplexityAnalysis object from parsed data."""
        
        # Validate and normalize data
        complexity_level = ComplexityLevel(max(1, min(6, data.get('complexity_level', 3))))
        agent_count = max(1, min(6, data.get('agent_count', complexity_level.value)))
        
        # Parse domain requirements
        domain_requirements = []
        for req_data in data.get('domain_requirements', []):
            domain_req = DomainRequirement(
                domain=req_data.get('domain', 'general'),
                importance=max(0.0, min(1.0, req_data.get('importance', 0.5))),
                specializations=req_data.get('specializations', []),
                cognitive_style_preference=req_data.get('cognitive_style_preference')
            )
            domain_requirements.append(domain_req)
        
        # Ensure we have MBTI recommendations
        mbti_recommendations = data.get('mbti_recommendations', [])
        if not mbti_recommendations:
            mbti_recommendations = self._generate_default_mbti_recommendations(
                complexity_level, domain_requirements
            )
        
        return ComplexityAnalysis(
            complexity_level=complexity_level,
            agent_count=agent_count,
            domain_requirements=domain_requirements,
            mbti_recommendations=mbti_recommendations[:agent_count],  # Limit to agent count
            scaling_rationale=data.get('scaling_rationale', 'Standard complexity-based scaling'),
            confidence_score=max(0.0, min(1.0, data.get('confidence_score', 0.7))),
            quality_prediction=max(0.0, min(1.0, data.get('quality_prediction', 0.7))),
            efficiency_score=max(0.0, min(1.0, data.get('efficiency_score', 0.7))),
            analysis_factors=data.get('analysis_factors', {})
        )
    
    def _generate_default_mbti_recommendations(
        self,
        complexity_level: ComplexityLevel,
        domain_requirements: List[DomainRequirement]
    ) -> List[str]:
        """Generate default MBTI recommendations based on complexity and domains."""
        
        # Get relevant MBTI types based on domains
        relevant_types = set()
        for domain_req in domain_requirements:
            for mbti_type, info in self.mbti_strengths.items():
                if domain_req.domain in info['domains']:
                    relevant_types.add(mbti_type)
        
        # If no domain match, use complexity-based defaults
        if not relevant_types:
            if complexity_level.value <= 2:
                relevant_types = {'ISTJ', 'ISFJ', 'ESTJ', 'ESFJ'}  # Detail-oriented, practical
            elif complexity_level.value <= 4:
                relevant_types = {'INTJ', 'ENTJ', 'INFJ', 'ENFJ'}  # Strategic, balanced
            else:
                relevant_types = {'INTJ', 'INTP', 'ENTJ', 'ENTP'}  # Complex problem solvers
        
        # Select diverse types based on cognitive functions
        selected_types = []
        used_functions = set()
        
        for mbti_type in relevant_types:
            functions = self.mbti_strengths[mbti_type]['functions'].split('-')
            primary_function = functions[0]
            
            # Prefer cognitive diversity
            if primary_function not in used_functions or len(selected_types) < complexity_level.value:
                selected_types.append(mbti_type)
                used_functions.add(primary_function)
                
                if len(selected_types) >= complexity_level.value:
                    break
        
        return selected_types
    
    def _fallback_analysis(
        self,
        task: str,
        context: Dict[str, Any] = None,
        user_preferences: Dict[str, Any] = None
    ) -> ComplexityAnalysis:
        """Provide fallback rule-based analysis when LLM analysis fails."""
        
        # Simple rule-based complexity assessment
        task_lower = task.lower()
        word_count = len(task.split())
        
        # Count domain indicators
        domain_matches = {}
        for domain, keywords in self.domain_keywords.items():
            matches = sum(1 for keyword in keywords if keyword in task_lower)
            if matches > 0:
                domain_matches[domain] = matches / len(keywords)
        
        # Determine complexity based on simple heuristics
        complexity_indicators = 0
        
        # Length and complexity words
        if word_count > 50:
            complexity_indicators += 1
        if any(word in task_lower for word in ['complex', 'comprehensive', 'analyze', 'evaluate', 'design', 'strategy']):
            complexity_indicators += 1
        if len(domain_matches) > 2:
            complexity_indicators += 1
        if any(word in task_lower for word in ['future', 'predict', 'innovate', 'transform', 'revolutionize']):
            complexity_indicators += 1
        
        # Map to complexity level
        complexity_level = ComplexityLevel(min(6, max(1, complexity_indicators + 1)))
        
        # Generate domain requirements
        domain_requirements = []
        for domain, score in sorted(domain_matches.items(), key=lambda x: x[1], reverse=True)[:3]:
            domain_req = DomainRequirement(
                domain=domain,
                importance=min(1.0, score * 2),  # Scale importance
                specializations=[],
                cognitive_style_preference=None
            )
            domain_requirements.append(domain_req)
        
        # Generate MBTI recommendations
        mbti_recommendations = self._generate_default_mbti_recommendations(
            complexity_level, domain_requirements
        )
        
        return ComplexityAnalysis(
            complexity_level=complexity_level,
            agent_count=complexity_level.value,
            domain_requirements=domain_requirements,
            mbti_recommendations=mbti_recommendations,
            scaling_rationale="Fallback rule-based analysis due to LLM parsing failure",
            confidence_score=0.6,  # Lower confidence for fallback
            quality_prediction=0.6,
            efficiency_score=0.8,  # Higher efficiency for rule-based
            analysis_factors={}
        )
    
    def _record_analysis(
        self,
        task: str,
        context: Dict[str, Any],
        analysis: ComplexityAnalysis
    ) -> None:
        """Record analysis for learning and pattern recognition."""
        
        record = {
            'timestamp': asyncio.get_event_loop().time(),
            'task': task,
            'context': context,
            'analysis': analysis,
            'word_count': len(task.split()),
            'domain_count': len(analysis.domain_requirements)
        }
        
        self.complexity_history.append(record)
        
        # Keep only recent history to prevent memory bloat
        if len(self.complexity_history) > 100:
            self.complexity_history = self.complexity_history[-100:]
    
    def get_analysis_patterns(self) -> Dict[str, Any]:
        """Get analysis patterns for continuous learning."""
        
        if not self.complexity_history:
            return {}
        
        # Analyze patterns in complexity assignments
        complexity_distribution = {}
        domain_frequency = {}
        mbti_frequency = {}
        
        for record in self.complexity_history:
            analysis = record['analysis']
            
            # Complexity distribution
            level = analysis.complexity_level.value
            complexity_distribution[level] = complexity_distribution.get(level, 0) + 1
            
            # Domain frequency
            for domain_req in analysis.domain_requirements:
                domain_frequency[domain_req.domain] = domain_frequency.get(domain_req.domain, 0) + 1
            
            # MBTI frequency
            for mbti_type in analysis.mbti_recommendations:
                mbti_frequency[mbti_type] = mbti_frequency.get(mbti_type, 0) + 1
        
        return {
            'total_analyses': len(self.complexity_history),
            'complexity_distribution': complexity_distribution,
            'domain_frequency': domain_frequency,
            'mbti_frequency': mbti_frequency,
            'average_confidence': sum(r['analysis'].confidence_score for r in self.complexity_history) / len(self.complexity_history),
            'average_agents_deployed': sum(r['analysis'].agent_count for r in self.complexity_history) / len(self.complexity_history)
        }
    
    async def update_user_patterns(self, user_feedback: Dict[str, Any]) -> None:
        """Update user pattern learning based on feedback."""
        
        # This would implement user pattern learning
        # For now, just store the feedback
        feedback_id = f"feedback_{len(self.user_patterns)}"
        self.user_patterns[feedback_id] = {
            'timestamp': asyncio.get_event_loop().time(),
            'feedback': user_feedback
        }