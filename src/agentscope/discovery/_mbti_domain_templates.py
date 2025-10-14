# -*- coding: utf-8 -*-
"""
MBTI+Domain Expert Template System - Templates for HiVA Dynamic Agent Generation

This module provides templates that HiVA uses to dynamically generate new agents 
with specific MBTI personalities and domain expertise combinations.
"""

import json
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, asdict
from enum import Enum


class CognitiveFunctionType(Enum):
    """Cognitive function types in MBTI theory."""
    Ni = "Introverted Intuition"
    Ne = "Extraverted Intuition"
    Ti = "Introverted Thinking"
    Te = "Extraverted Thinking"
    Fi = "Introverted Feeling"
    Fe = "Extraverted Feeling"
    Si = "Introverted Sensing"
    Se = "Extraverted Sensing"


@dataclass
class CognitiveFunctionStack:
    """Represents the cognitive function stack for an MBTI type."""
    dominant: CognitiveFunctionType
    auxiliary: CognitiveFunctionType
    tertiary: CognitiveFunctionType
    inferior: CognitiveFunctionType


@dataclass
class DomainExpertise:
    """Represents domain expertise specifications."""
    domain_name: str
    specialization_areas: List[str]
    key_competencies: List[str]
    methodologies: List[str]
    interdisciplinary_connections: List[str]


@dataclass
class MBTIDomainExpertTemplate:
    """Template for creating MBTI+Domain Expert agents."""
    
    mbti_type: str
    domain_expertise: DomainExpertise
    cognitive_functions: CognitiveFunctionStack
    strengths: List[str]
    preferred_approaches: List[str]
    collaboration_style: str
    
    def generate_system_prompt(self) -> str:
        """Generate a comprehensive system prompt for the agent."""
        
        prompt = f"""
You are an {self.mbti_type} personality type with deep expertise in {self.domain_expertise.domain_name}.

PERSONALITY PROFILE:
- MBTI Type: {self.mbti_type}
- Cognitive Functions: {self.cognitive_functions.dominant.name}-{self.cognitive_functions.auxiliary.name}-{self.cognitive_functions.tertiary.name}-{self.cognitive_functions.inferior.name}

DOMAIN EXPERTISE: {self.domain_expertise.domain_name}
- Specializations: {', '.join(self.domain_expertise.specialization_areas)}
- Key Competencies: {', '.join(self.domain_expertise.key_competencies)}
- Methodologies: {', '.join(self.domain_expertise.methodologies)}

CORE STRENGTHS:
{self._format_list(self.strengths)}

PREFERRED APPROACHES:
{self._format_list(self.preferred_approaches)}

COLLABORATION STYLE: {self.collaboration_style}

INSTRUCTIONS:
1. Always approach problems through your {self.mbti_type} cognitive lens, leading with {self.cognitive_functions.dominant.name}
2. Apply your {self.domain_expertise.domain_name} expertise naturally and authentically
3. When collaborating with other agents, respect their different cognitive approaches
4. Maintain your authentic {self.mbti_type} perspective while remaining open to other viewpoints
5. Use your domain expertise to provide deep, specialized insights

Remember: You are approaching problems with the unique cognitive style of an {self.mbti_type} {self.domain_expertise.domain_name} expert.
"""
        return prompt.strip()
    
    def _format_list(self, items: List[str]) -> str:
        """Format a list for display."""
        return '\n'.join([f"- {item}" for item in items])
    
    def get_agent_config(self) -> Dict[str, Any]:
        """Get configuration for creating an agent from this template."""
        return {
            'name': f"{self.mbti_type}_{self.domain_expertise.domain_name}_Expert",
            'sys_prompt': self.generate_system_prompt(),
            'personality_type': self.mbti_type,
            'domain': self.domain_expertise.domain_name,
            'cognitive_functions': asdict(self.cognitive_functions),
            'strengths': self.strengths,
            'collaboration_style': self.collaboration_style
        }


class MBTIDomainTemplateRegistry:
    """Registry of all MBTI+Domain Expert templates for dynamic agent generation."""
    
    def __init__(self):
        self.templates: Dict[str, MBTIDomainExpertTemplate] = {}
        self.mbti_cognitive_functions = self._initialize_mbti_functions()
        self.domain_expertise_profiles = self._initialize_domain_profiles()
        self.mbti_characteristics = self._initialize_mbti_characteristics()
        self._initialize_core_templates()
    
    def _initialize_mbti_functions(self) -> Dict[str, CognitiveFunctionStack]:
        """Initialize cognitive function stacks for all 16 MBTI types."""
        return {
            'INTJ': CognitiveFunctionStack(CognitiveFunctionType.Ni, CognitiveFunctionType.Te, CognitiveFunctionType.Fi, CognitiveFunctionType.Se),
            'INTP': CognitiveFunctionStack(CognitiveFunctionType.Ti, CognitiveFunctionType.Ne, CognitiveFunctionType.Si, CognitiveFunctionType.Fe),
            'ENTJ': CognitiveFunctionStack(CognitiveFunctionType.Te, CognitiveFunctionType.Ni, CognitiveFunctionType.Se, CognitiveFunctionType.Fi),
            'ENTP': CognitiveFunctionStack(CognitiveFunctionType.Ne, CognitiveFunctionType.Ti, CognitiveFunctionType.Fe, CognitiveFunctionType.Si),
            'INFJ': CognitiveFunctionStack(CognitiveFunctionType.Ni, CognitiveFunctionType.Fe, CognitiveFunctionType.Ti, CognitiveFunctionType.Se),
            'INFP': CognitiveFunctionStack(CognitiveFunctionType.Fi, CognitiveFunctionType.Ne, CognitiveFunctionType.Si, CognitiveFunctionType.Te),
            'ENFJ': CognitiveFunctionStack(CognitiveFunctionType.Fe, CognitiveFunctionType.Ni, CognitiveFunctionType.Se, CognitiveFunctionType.Ti),
            'ENFP': CognitiveFunctionStack(CognitiveFunctionType.Ne, CognitiveFunctionType.Fi, CognitiveFunctionType.Te, CognitiveFunctionType.Si),
            'ISTJ': CognitiveFunctionStack(CognitiveFunctionType.Si, CognitiveFunctionType.Te, CognitiveFunctionType.Fi, CognitiveFunctionType.Ne),
            'ISFJ': CognitiveFunctionStack(CognitiveFunctionType.Si, CognitiveFunctionType.Fe, CognitiveFunctionType.Ti, CognitiveFunctionType.Ne),
            'ESTJ': CognitiveFunctionStack(CognitiveFunctionType.Te, CognitiveFunctionType.Si, CognitiveFunctionType.Ne, CognitiveFunctionType.Fi),
            'ESFJ': CognitiveFunctionStack(CognitiveFunctionType.Fe, CognitiveFunctionType.Si, CognitiveFunctionType.Ne, CognitiveFunctionType.Ti),
            'ISTP': CognitiveFunctionStack(CognitiveFunctionType.Ti, CognitiveFunctionType.Se, CognitiveFunctionType.Ni, CognitiveFunctionType.Fe),
            'ISFP': CognitiveFunctionStack(CognitiveFunctionType.Fi, CognitiveFunctionType.Se, CognitiveFunctionType.Ni, CognitiveFunctionType.Te),
            'ESTP': CognitiveFunctionStack(CognitiveFunctionType.Se, CognitiveFunctionType.Ti, CognitiveFunctionType.Fe, CognitiveFunctionType.Ni),
            'ESFP': CognitiveFunctionStack(CognitiveFunctionType.Se, CognitiveFunctionType.Fi, CognitiveFunctionType.Te, CognitiveFunctionType.Ni),
        }
    
    def _initialize_domain_profiles(self) -> Dict[str, DomainExpertise]:
        """Initialize domain expertise profiles."""
        return {
            'Physics': DomainExpertise(
                domain_name='Physics',
                specialization_areas=['Theoretical Physics', 'Quantum Mechanics', 'Cosmology'],
                key_competencies=['Mathematical Modeling', 'Experimental Design', 'Theory Development'],
                methodologies=['Scientific Method', 'Theoretical Modeling', 'Experimental Validation'],
                interdisciplinary_connections=['Mathematics', 'Engineering', 'Computer Science']
            ),
            'Psychology': DomainExpertise(
                domain_name='Psychology',
                specialization_areas=['Cognitive Psychology', 'Social Psychology', 'Clinical Psychology'],
                key_competencies=['Research Design', 'Statistical Analysis', 'Behavioral Observation'],
                methodologies=['Experimental Method', 'Case Study', 'Meta-Analysis'],
                interdisciplinary_connections=['Neuroscience', 'Medicine', 'Education', 'Philosophy']
            ),
            'Computer_Science': DomainExpertise(
                domain_name='Computer Science',
                specialization_areas=['Algorithms', 'Software Engineering', 'AI/ML', 'Systems'],
                key_competencies=['Programming', 'System Design', 'Problem Decomposition'],
                methodologies=['Agile Development', 'Design Patterns', 'Testing Frameworks'],
                interdisciplinary_connections=['Mathematics', 'Engineering', 'Cognitive Science']
            ),
            'Philosophy': DomainExpertise(
                domain_name='Philosophy',
                specialization_areas=['Ethics', 'Epistemology', 'Logic', 'Philosophy of Mind'],
                key_competencies=['Logical Reasoning', 'Argument Analysis', 'Conceptual Clarification'],
                methodologies=['Socratic Method', 'Dialectical Reasoning', 'Thought Experiments'],
                interdisciplinary_connections=['Religion', 'Science', 'Psychology', 'Political Science']
            ),
            'Business': DomainExpertise(
                domain_name='Business',
                specialization_areas=['Strategy', 'Marketing', 'Finance', 'Operations'],
                key_competencies=['Strategic Thinking', 'Financial Analysis', 'Market Research'],
                methodologies=['SWOT Analysis', 'Lean Methodology', 'Design Thinking'],
                interdisciplinary_connections=['Economics', 'Psychology', 'Technology', 'Law']
            ),
            'Creative_Arts': DomainExpertise(
                domain_name='Creative Arts',
                specialization_areas=['Visual Arts', 'Literature', 'Music', 'Design'],
                key_competencies=['Creative Expression', 'Aesthetic Analysis', 'Cultural Interpretation'],
                methodologies=['Creative Process', 'Critique', 'Cultural Analysis'],
                interdisciplinary_connections=['Psychology', 'History', 'Technology', 'Philosophy']
            )
        }
    
    def _initialize_mbti_characteristics(self) -> Dict[str, Dict[str, Any]]:
        """Initialize MBTI characteristics and working styles."""
        return {
            'INTJ': {
                'strengths': ['Strategic vision', 'System optimization', 'Independent analysis', 'Long-term planning'],
                'approaches': ['Focus on big picture patterns', 'Design systematic solutions', 'Analyze long-term implications'],
                'collaboration': 'Values competence and independence, contributes strategic insights'
            },
            'INTP': {
                'strengths': ['Logical analysis', 'Theoretical depth', 'Precision thinking', 'Innovative solutions'],
                'approaches': ['Understand underlying principles', 'Explore theoretical implications', 'Refine logical consistency'],
                'collaboration': 'Values intellectual discourse, contributes deep analysis and creative solutions'
            },
            'ENTJ': {
                'strengths': ['Strategic leadership', 'Efficient organization', 'Goal achievement', 'Implementation focus'],
                'approaches': ['Define clear objectives', 'Organize for maximum efficiency', 'Drive toward results'],
                'collaboration': 'Takes charge and organizes team efforts, drives toward results'
            },
            'ENTP': {
                'strengths': ['Innovation', 'Possibility exploration', 'Creative connections', 'Adaptability'],
                'approaches': ['Brainstorm multiple possibilities', 'Explore innovative connections', 'Challenge assumptions'],
                'collaboration': 'Energizes team with possibilities, challenges conventional thinking'
            },
            'INFJ': {
                'strengths': ['Insightful vision', 'Human-centered focus', 'Value alignment', 'Strategic empathy'],
                'approaches': ['Understand human impact', 'Envision meaningful outcomes', 'Align with deeper values'],
                'collaboration': 'Facilitates understanding and consensus, contributes human insight'
            },
            'INFP': {
                'strengths': ['Authentic values', 'Creative insight', 'Individual understanding', 'Adaptive flexibility'],
                'approaches': ['Ensure values alignment', 'Explore creative alternatives', 'Honor individual perspectives'],
                'collaboration': 'Contributes authentic perspective and creative solutions'
            },
            'ENFJ': {
                'strengths': ['People development', 'Inspiring leadership', 'Collaborative harmony', 'Team building'],
                'approaches': ['Focus on people development', 'Build consensus', 'Inspire shared vision'],
                'collaboration': 'Facilitates team harmony and individual growth'
            },
            'ENFP': {
                'strengths': ['Inspiring enthusiasm', 'People connection', 'Creative possibility', 'Adaptive energy'],
                'approaches': ['Inspire with possibilities', 'Connect authentically', 'Adapt to opportunities'],
                'collaboration': 'Energizes team with enthusiasm and builds strong relationships'
            },
            'ISTJ': {
                'strengths': ['Reliable execution', 'Detailed accuracy', 'Systematic approach', 'Practical wisdom'],
                'approaches': ['Use proven methods', 'Ensure accuracy', 'Build on experience'],
                'collaboration': 'Provides stability and reliable follow-through'
            },
            'ISFJ': {
                'strengths': ['Supportive service', 'Detailed care', 'Reliable assistance', 'Practical empathy'],
                'approaches': ['Focus on supporting others', 'Ensure practical needs are met', 'Maintain harmony'],
                'collaboration': 'Provides supportive assistance and maintains team harmony'
            },
            'ESTJ': {
                'strengths': ['Efficient organization', 'Practical implementation', 'Clear structure', 'Results focus'],
                'approaches': ['Organize for efficiency', 'Implement proven systems', 'Focus on results'],
                'collaboration': 'Organizes team efforts and drives toward concrete results'
            },
            'ESFJ': {
                'strengths': ['Team harmony', 'Practical service', 'Relationship building', 'Supportive organization'],
                'approaches': ['Build team consensus', 'Ensure needs are met', 'Maintain relationships'],
                'collaboration': 'Builds team harmony and ensures everyone feels included'
            },
            'ISTP': {
                'strengths': ['Practical problem-solving', 'Technical skill', 'Efficient analysis', 'Hands-on approach'],
                'approaches': ['Analyze what works', 'Focus on practical solutions', 'Adapt to requirements'],
                'collaboration': 'Contributes practical expertise and flexible problem-solving'
            },
            'ISFP': {
                'strengths': ['Values-driven authenticity', 'Individual focus', 'Aesthetic appreciation', 'Gentle support'],
                'approaches': ['Ensure values alignment', 'Honor uniqueness', 'Maintain authenticity'],
                'collaboration': 'Contributes authentic perspective and supports individual needs'
            },
            'ESTP': {
                'strengths': ['Real-time adaptation', 'Practical action', 'Crisis management', 'Energetic engagement'],
                'approaches': ['Take immediate action', 'Respond to opportunities', 'Adapt quickly'],
                'collaboration': 'Brings energy and practical action, adapts quickly to team needs'
            },
            'ESFP': {
                'strengths': ['Enthusiastic support', 'People connection', 'Positive energy', 'Flexible responsiveness'],
                'approaches': ['Energize and encourage', 'Focus on positive possibilities', 'Respond to people needs'],
                'collaboration': 'Brings enthusiasm and positive energy, supports team morale'
            }
        }
    
    def _initialize_core_templates(self) -> None:
        """Initialize core MBTI+Domain template combinations."""
        
        # Create templates for strategic combinations
        strategic_combinations = [
            ('INTJ', 'Physics'), ('INTJ', 'Computer_Science'), ('INTJ', 'Philosophy'),
            ('INTP', 'Physics'), ('INTP', 'Computer_Science'), ('INTP', 'Philosophy'),
            ('ENTJ', 'Business'), ('ENTJ', 'Computer_Science'),
            ('ENTP', 'Creative_Arts'), ('ENTP', 'Computer_Science'), ('ENTP', 'Business'),
            ('INFJ', 'Psychology'), ('INFJ', 'Philosophy'), ('INFJ', 'Creative_Arts'),
            ('INFP', 'Creative_Arts'), ('INFP', 'Psychology'), ('INFP', 'Philosophy'),
            ('ENFJ', 'Psychology'), ('ENFJ', 'Business'),
            ('ENFP', 'Creative_Arts'), ('ENFP', 'Psychology'), ('ENFP', 'Business'),
            ('ISTJ', 'Business'), ('ISTJ', 'Computer_Science'),
            ('ISFJ', 'Psychology'), ('ISFJ', 'Business'),
            ('ESTJ', 'Business'), ('ESTJ', 'Computer_Science'),
            ('ESFJ', 'Psychology'), ('ESFJ', 'Business'),
            ('ISTP', 'Computer_Science'), ('ISTP', 'Physics'),
            ('ISFP', 'Creative_Arts'), ('ISFP', 'Psychology'),
            ('ESTP', 'Business'), ('ESTP', 'Computer_Science'),
            ('ESFP', 'Creative_Arts'), ('ESFP', 'Psychology')
        ]
        
        for mbti_type, domain_name in strategic_combinations:
            self.register_template(mbti_type, domain_name)
    
    def register_template(self, mbti_type: str, domain_name: str) -> None:
        """Register a new MBTI+Domain template."""
        
        if mbti_type not in self.mbti_cognitive_functions:
            raise ValueError(f"Unknown MBTI type: {mbti_type}")
        
        if domain_name not in self.domain_expertise_profiles:
            raise ValueError(f"Unknown domain: {domain_name}")
        
        template_key = f"{mbti_type}_{domain_name}"
        
        characteristics = self.mbti_characteristics.get(mbti_type, {})
        domain_expertise = self.domain_expertise_profiles[domain_name]
        cognitive_functions = self.mbti_cognitive_functions[mbti_type]
        
        template = MBTIDomainExpertTemplate(
            mbti_type=mbti_type,
            domain_expertise=domain_expertise,
            cognitive_functions=cognitive_functions,
            strengths=characteristics.get('strengths', []),
            preferred_approaches=characteristics.get('approaches', []),
            collaboration_style=characteristics.get('collaboration', '')
        )
        
        self.templates[template_key] = template
    
    def get_template(self, mbti_type: str, domain: str) -> Optional[MBTIDomainExpertTemplate]:
        """Get a specific template by MBTI type and domain."""
        template_key = f"{mbti_type}_{domain}"
        return self.templates.get(template_key)
    
    def get_optimal_templates(
        self,
        complexity_level: int,
        domain_requirements: List[str],
        mbti_preferences: List[str] = None
    ) -> List[MBTIDomainExpertTemplate]:
        """Get optimal templates based on complexity and requirements."""
        
        # Find candidate templates matching domain requirements
        candidate_templates = []
        for domain in domain_requirements:
            domain_templates = [
                template for template_key, template in self.templates.items()
                if template.domain_expertise.domain_name == domain
            ]
            candidate_templates.extend(domain_templates)
        
        # Filter by MBTI preferences if specified
        if mbti_preferences:
            candidate_templates = [
                template for template in candidate_templates
                if template.mbti_type in mbti_preferences
            ]
        
        # Select optimal number based on complexity level
        optimal_count = min(complexity_level, len(candidate_templates), 6)
        
        # Select for cognitive diversity
        selected_templates = self._select_for_cognitive_diversity(
            candidate_templates, optimal_count
        )
        
        return selected_templates
    
    def _select_for_cognitive_diversity(
        self, 
        candidates: List[MBTIDomainExpertTemplate], 
        count: int
    ) -> List[MBTIDomainExpertTemplate]:
        """Select templates to maximize cognitive function diversity."""
        
        selected = []
        used_dominant_functions = set()
        
        # First pass: select different dominant functions
        for template in candidates:
            dominant_function = template.cognitive_functions.dominant
            if dominant_function not in used_dominant_functions:
                selected.append(template)
                used_dominant_functions.add(dominant_function)
                if len(selected) >= count:
                    break
        
        # Second pass: fill remaining slots
        remaining_candidates = [t for t in candidates if t not in selected]
        while len(selected) < count and remaining_candidates:
            selected.append(remaining_candidates.pop(0))
        
        return selected
    
    def list_available_combinations(self) -> Dict[str, List[str]]:
        """List all available MBTI-Domain combinations."""
        combinations = {}
        for template_key in self.templates.keys():
            mbti_type, domain = template_key.split('_', 1)
            if mbti_type not in combinations:
                combinations[mbti_type] = []
            combinations[mbti_type].append(domain)
        return combinations
    
    def get_template_count(self) -> int:
        """Get total number of templates available."""
        return len(self.templates)