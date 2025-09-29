#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discovery-Specific Toolkit with Core Tools

This module implements specialized tools for the discovery system,
following AgentScope tool patterns and design specifications.
"""

import os
import re
import json
import hashlib
import numpy as np
from typing import Dict, List, Optional, Any, Tuple
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass

# AgentScope imports
from agentscope.tool import ToolResponse


class ToolBase:
    """Simple base class for tools."""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description


@dataclass
class SearchResult:
    """Represents a search result with metadata."""
    content: str
    source: str
    relevance_score: float
    concept_tags: List[str]
    metadata: Dict[str, Any]


@dataclass
class Discovery:
    """Represents a discovery with confidence and validation metrics."""
    text: str
    confidence: float
    evidence: List[str]
    surprise_score: float
    connections: List[str]
    metadata: Dict[str, Any]


class DiscoveryToolBase(ToolBase):
    """Base class for discovery tools with common functionality."""
    
    def __init__(self, name: str, description: str):
        super().__init__(name=name, description=description)
        self.usage_count = 0
        self.last_used = None
        self.performance_metrics = {
            "success_rate": 1.0,
            "average_confidence": 0.0,
            "total_discoveries": 0
        }
    
    def _update_metrics(self, success: bool, confidence: float = 0.0):
        """Update tool performance metrics."""
        self.usage_count += 1
        self.last_used = datetime.now()
        
        # Update success rate
        current_successes = self.performance_metrics["success_rate"] * (self.usage_count - 1)
        new_successes = current_successes + (1 if success else 0)
        self.performance_metrics["success_rate"] = new_successes / self.usage_count
        
        # Update confidence if provided
        if confidence > 0:
            current_avg = self.performance_metrics["average_confidence"]
            self.performance_metrics["average_confidence"] = (
                (current_avg * (self.usage_count - 1) + confidence) / self.usage_count
            )


class SearchTool(DiscoveryToolBase):
    """Advanced search tool for knowledge base exploration."""
    
    def __init__(self):
        super().__init__(
            name="search_tool",
            description="Search knowledge base with semantic understanding and relevance ranking"
        )
        self.knowledge_base = {}
        self.concept_index = {}
        self.search_history = []
    
    def load_knowledge_base(self, knowledge_files: List[Dict[str, str]]):
        """Load knowledge base from file data."""
        self.knowledge_base = {}
        self.concept_index = {}
        
        for file_data in knowledge_files:
            filename = file_data["filename"]
            content = file_data["content"]
            
            # Store content with extracted concepts
            self.knowledge_base[filename] = {
                "content": content,
                "concepts": self._extract_concepts(content),
                "sections": self._split_into_sections(content)
            }
            
            # Build concept index
            concepts = self._extract_concepts(content)
            for concept in concepts:
                if concept not in self.concept_index:
                    self.concept_index[concept] = []
                self.concept_index[concept].append(filename)
    
    def _extract_concepts(self, text: str) -> List[str]:
        """Extract key concepts from text."""
        concepts = []
        
        # Extract capitalized terms and technical terms
        capitalized = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        quoted = re.findall(r'"([^"]+)"', text)
        technical = re.findall(r'\b[a-z]+_[a-z_]+\b|\b[a-z]+[A-Z][a-zA-Z]*\b', text)
        
        concepts.extend(capitalized + quoted + technical)
        return list(set(concepts))
    
    def _split_into_sections(self, text: str) -> List[Dict[str, str]]:
        """Split text into logical sections."""
        sections = []
        header_pattern = r'^(#{1,6})\s+(.+)$'
        lines = text.split('\n')
        current_section = {"header": "Introduction", "content": "", "level": 0}
        
        for line in lines:
            header_match = re.match(header_pattern, line, re.MULTILINE)
            if header_match:
                if current_section["content"].strip():
                    sections.append(current_section)
                
                level = len(header_match.group(1))
                header = header_match.group(2)
                current_section = {"header": header, "content": "", "level": level}
            else:
                current_section["content"] += line + "\n"
        
        if current_section["content"].strip():
            sections.append(current_section)
        
        return sections
    
    async def __call__(self, query: str, max_results: int = 10, 
                      relevance_threshold: float = 0.3) -> List[SearchResult]:
        """Search knowledge base with the given query."""
        self.search_history.append({
            "query": query,
            "timestamp": datetime.now().isoformat(),
            "max_results": max_results
        })
        
        results = []
        query_concepts = self._extract_concepts(query.lower())
        
        # Search through knowledge base
        for filename, file_data in self.knowledge_base.items():
            content = file_data["content"]
            file_concepts = file_data["concepts"]
            
            # Calculate relevance score
            relevance_score = self._calculate_relevance(query, content, query_concepts, file_concepts)
            
            if relevance_score >= relevance_threshold:
                # Find relevant sections
                relevant_sections = self._find_relevant_sections(query, file_data["sections"])
                
                for section in relevant_sections:
                    section_relevance = self._calculate_relevance(
                        query, section["content"], query_concepts, file_concepts
                    )
                    
                    if section_relevance >= relevance_threshold:
                        results.append(SearchResult(
                            content=section["content"][:500] + "..." if len(section["content"]) > 500 else section["content"],
                            source=f"{filename}#{section['header']}",
                            relevance_score=section_relevance,
                            concept_tags=self._extract_concepts(section["content"]),
                            metadata={
                                "filename": filename,
                                "section": section["header"],
                                "section_level": section["level"],
                                "full_content_length": len(section["content"])
                            }
                        ))
        
        # Sort by relevance and limit results
        results.sort(key=lambda x: x.relevance_score, reverse=True)
        results = results[:max_results]
        
        self._update_metrics(len(results) > 0, np.mean([r.relevance_score for r in results]) if results else 0)
        
        return results
    
    def _calculate_relevance(self, query: str, content: str, 
                           query_concepts: List[str], content_concepts: List[str]) -> float:
        """Calculate relevance score between query and content."""
        query_lower = query.lower()
        content_lower = content.lower()
        
        # Word overlap score
        query_words = set(re.findall(r'\w+', query_lower))
        content_words = set(re.findall(r'\w+', content_lower))
        word_overlap = len(query_words.intersection(content_words)) / len(query_words) if query_words else 0
        
        # Concept overlap score
        concept_overlap = len(set(query_concepts).intersection(set(content_concepts))) / len(query_concepts) if query_concepts else 0
        
        # Exact phrase matching
        phrase_score = 1.0 if query_lower in content_lower else 0
        
        # Combined score
        relevance = (word_overlap * 0.4 + concept_overlap * 0.4 + phrase_score * 0.2)
        return min(relevance, 1.0)
    
    def _find_relevant_sections(self, query: str, sections: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Find sections most relevant to the query."""
        relevant_sections = []
        query_concepts = self._extract_concepts(query.lower())
        
        for section in sections:
            section_concepts = self._extract_concepts(section["content"])
            relevance = self._calculate_relevance(query, section["content"], query_concepts, section_concepts)
            
            if relevance > 0.2:  # Lower threshold for sections
                relevant_sections.append(section)
        
        return relevant_sections


class AnalysisTool(DiscoveryToolBase):
    """Tool for analyzing content to identify patterns and insights."""
    
    def __init__(self):
        super().__init__(
            name="analysis_tool",
            description="Analyze content for patterns, relationships, and insights"
        )
        self.analysis_cache = {}
    
    async def __call__(self, content: str, analysis_type: str = "comprehensive") -> Dict[str, Any]:
        """Analyze content based on the specified analysis type."""
        cache_key = hashlib.md5(f"{content}{analysis_type}".encode()).hexdigest()
        
        if cache_key in self.analysis_cache:
            return self.analysis_cache[cache_key]
        
        analysis_result = {}
        
        if analysis_type in ["comprehensive", "patterns"]:
            analysis_result["patterns"] = await self._identify_patterns(content)
        
        if analysis_type in ["comprehensive", "relationships"]:
            analysis_result["relationships"] = await self._identify_relationships(content)
        
        if analysis_type in ["comprehensive", "themes"]:
            analysis_result["themes"] = await self._identify_themes(content)
        
        # Cache result
        self.analysis_cache[cache_key] = analysis_result
        
        # Update metrics
        confidence = np.mean([
            len(analysis_result.get("patterns", [])) / 10,
            len(analysis_result.get("relationships", [])) / 10,
            len(analysis_result.get("themes", [])) / 5
        ])
        self._update_metrics(True, min(confidence, 1.0))
        
        return analysis_result
    
    async def _identify_patterns(self, content: str) -> List[Dict[str, Any]]:
        """Identify recurring patterns in content."""
        patterns = []
        
        # Identify repeated phrases
        words = re.findall(r'\w+', content.lower())
        phrase_counts = {}
        
        for i in range(len(words) - 2):
            phrase = " ".join(words[i:i+3])
            phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
        
        frequent_phrases = [(phrase, count) for phrase, count in phrase_counts.items() if count > 2]
        frequent_phrases.sort(key=lambda x: x[1], reverse=True)
        
        for phrase, count in frequent_phrases[:10]:
            patterns.append({
                "type": "repeated_phrase",
                "pattern": phrase,
                "frequency": count,
                "confidence": min(count / 10, 1.0)
            })
        
        return patterns
    
    async def _identify_relationships(self, content: str) -> List[Dict[str, Any]]:
        """Identify relationships between concepts."""
        relationships = []
        
        # Simple relationship patterns
        relationship_patterns = [
            (r'(\w+)\s+(?:causes?|leads? to|results? in)\s+(\w+)', "causal"),
            (r'(\w+)\s+(?:is a|are)\s+(\w+)', "categorical"),
            (r'(\w+)\s+(?:relates? to|connects? to)\s+(\w+)', "associative"),
            (r'(\w+)\s+(?:vs\.?|versus|compared to)\s+(\w+)', "comparative")
        ]
        
        for pattern, rel_type in relationship_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                relationships.append({
                    "type": rel_type,
                    "source": match[0],
                    "target": match[1],
                    "confidence": 0.7
                })
        
        return relationships
    
    async def _identify_themes(self, content: str) -> List[Dict[str, Any]]:
        """Identify main themes in content."""
        themes = []
        
        # Extract significant words (excluding common words)
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'}
        
        words = re.findall(r'\w+', content.lower())
        significant_words = [w for w in words if len(w) > 3 and w not in stop_words]
        
        # Count word frequencies
        word_counts = {}
        for word in significant_words:
            word_counts[word] = word_counts.get(word, 0) + 1
        
        # Identify themes based on frequent words
        frequent_words = sorted(word_counts.items(), key=lambda x: x[1], reverse=True)
        
        for word, count in frequent_words[:10]:
            if count > 3:
                themes.append({
                    "theme": word,
                    "frequency": count,
                    "prominence": count / len(significant_words),
                    "confidence": min(count / 20, 1.0)
                })
        
        return themes


class HypothesisGeneratorTool(DiscoveryToolBase):
    """Tool for generating testable hypotheses from discoveries."""
    
    def __init__(self):
        super().__init__(
            name="hypothesis_generator_tool",
            description="Generate testable hypotheses from discoveries and patterns"
        )
    
    async def __call__(self, discoveries: List[Dict[str, Any]], 
                      context: str = "") -> List[Dict[str, Any]]:
        """Generate hypotheses from discoveries."""
        hypotheses = []
        
        for discovery in discoveries:
            discovery_text = discovery.get("text", "")
            confidence = discovery.get("confidence", 0.5)
            
            # Generate different types of hypotheses
            hypothesis = await self._generate_hypothesis(discovery_text, confidence, context)
            if hypothesis:
                hypotheses.append(hypothesis)
        
        # Sort by confidence and limit
        hypotheses.sort(key=lambda h: h["confidence"], reverse=True)
        
        self._update_metrics(len(hypotheses) > 0, 
                           np.mean([h["confidence"] for h in hypotheses]) if hypotheses else 0)
        
        return hypotheses[:10]  # Return top 10
    
    async def _generate_hypothesis(self, discovery_text: str, base_confidence: float, context: str) -> Optional[Dict[str, Any]]:
        """Generate hypothesis from discovery text."""
        # Extract key elements
        concepts = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', discovery_text)
        
        if len(concepts) >= 2:
            concept1, concept2 = concepts[0], concepts[1]
            
            # Generate if-then hypothesis
            hypothesis = {
                "statement": f"If {concept1} influences the system, then {concept2} will show measurable change",
                "confidence": base_confidence * 0.8,
                "testability": 0.7,
                "experimental_design": f"Controlled experiment manipulating {concept1} and measuring {concept2}",
                "expected_outcomes": [f"Significant change in {concept2}"],
                "variables": {"independent": concept1, "dependent": concept2}
            }
            
            return hypothesis
        
        return None


class QuestionGeneratorTool(DiscoveryToolBase):
    """Tool for generating follow-up questions for further exploration."""
    
    def __init__(self):
        super().__init__(
            name="question_generator_tool",
            description="Generate insightful questions for deeper exploration"
        )
        self.question_templates = [
            "What factors influence {concept}?",
            "How does {concept1} relate to {concept2}?",
            "What are the implications of {discovery}?",
            "Under what conditions does {pattern} occur?",
            "What would happen if {assumption} were false?",
            "How can we test {hypothesis}?",
            "What evidence would support or refute {claim}?",
            "What are alternative explanations for {phenomenon}?"
        ]
    
    async def __call__(self, discoveries: List[Dict[str, Any]], 
                      insights: List[str] = None) -> List[Dict[str, Any]]:
        """Generate questions from discoveries and insights."""
        questions = []
        
        for discovery in discoveries:
            discovery_text = discovery.get("text", "")
            concepts = re.findall(r'\b[A-Z][a-z]+\b', discovery_text)
            
            for template in self.question_templates:
                question = await self._generate_question_from_template(template, concepts, discovery_text)
                if question:
                    questions.append(question)
        
        # Remove duplicates and rank
        unique_questions = self._deduplicate_questions(questions)
        
        self._update_metrics(len(unique_questions) > 0, 0.8)
        
        return unique_questions[:15]  # Return top 15
    
    async def _generate_question_from_template(self, template: str, concepts: List[str], context: str) -> Optional[Dict[str, Any]]:
        """Generate question from template and concepts."""
        try:
            if "{concept}" in template and concepts:
                question_text = template.format(concept=concepts[0])
            elif "{concept1}" in template and "{concept2}" in template and len(concepts) >= 2:
                question_text = template.format(concept1=concepts[0], concept2=concepts[1])
            elif "{discovery}" in template:
                question_text = template.format(discovery=context[:50] + "...")
            else:
                return None
            
            return {
                "question": question_text,
                "context": context,
                "priority": "medium",
                "question_type": "exploratory",
                "concepts": concepts[:2]
            }
        except:
            return None
    
    def _deduplicate_questions(self, questions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove duplicate questions."""
        unique_questions = []
        seen_questions = set()
        
        for question in questions:
            simplified = re.sub(r'\W+', ' ', question["question"].lower()).strip()
            if simplified not in seen_questions:
                seen_questions.add(simplified)
                unique_questions.append(question)
        
        return unique_questions


class BayesianSurpriseTool(DiscoveryToolBase):
    """Tool for assessing surprise value using Bayesian metrics."""
    
    def __init__(self):
        super().__init__(
            name="bayesian_surprise_tool",
            description="Assess novelty and surprise value of discoveries using Bayesian metrics"
        )
        self.baseline_expectations = {}
    
    async def __call__(self, discovery: Dict[str, Any], 
                      baseline_knowledge: List[str] = None) -> Dict[str, Any]:
        """Assess surprise value of a discovery."""
        discovery_text = discovery.get("text", "")
        
        # Calculate different surprise metrics
        surprise_assessment = {
            "discovery_id": discovery.get("id", "unknown"),
            "discovery_text": discovery_text,
            "surprise_score": await self._calculate_surprise_score(discovery_text, baseline_knowledge),
            "novelty_score": await self._calculate_novelty_score(discovery_text),
            "expectation_violation": await self._calculate_expectation_violation(discovery_text),
            "information_gain": await self._calculate_information_gain(discovery_text),
            "surprise_category": "medium"  # Will be determined based on scores
        }
        
        # Determine surprise category
        avg_score = np.mean([
            surprise_assessment["surprise_score"],
            surprise_assessment["novelty_score"],
            surprise_assessment["expectation_violation"]
        ])
        
        if avg_score > 0.8:
            surprise_assessment["surprise_category"] = "high"
        elif avg_score > 0.5:
            surprise_assessment["surprise_category"] = "medium"
        else:
            surprise_assessment["surprise_category"] = "low"
        
        self._update_metrics(True, avg_score)
        
        return surprise_assessment
    
    async def _calculate_surprise_score(self, discovery_text: str, baseline_knowledge: List[str] = None) -> float:
        """Calculate Bayesian surprise score."""
        # Simplified surprise calculation based on concept novelty
        discovery_concepts = set(re.findall(r'\b[A-Z][a-z]+\b', discovery_text))
        
        if baseline_knowledge:
            baseline_concepts = set()
            for knowledge in baseline_knowledge:
                baseline_concepts.update(re.findall(r'\b[A-Z][a-z]+\b', knowledge))
            
            novel_concepts = discovery_concepts - baseline_concepts
            surprise_score = len(novel_concepts) / len(discovery_concepts) if discovery_concepts else 0
        else:
            # Default surprise based on concept complexity
            surprise_score = min(len(discovery_concepts) / 10, 1.0)
        
        return surprise_score
    
    async def _calculate_novelty_score(self, discovery_text: str) -> float:
        """Calculate novelty score based on uniqueness indicators."""
        novelty_indicators = [
            "first time", "never before", "unprecedented", "novel", "new",
            "breakthrough", "discovery", "unexpected", "surprising", "unique"
        ]
        
        text_lower = discovery_text.lower()
        novelty_count = sum(1 for indicator in novelty_indicators if indicator in text_lower)
        
        # Normalize score
        novelty_score = min(novelty_count / 5, 1.0)
        
        return novelty_score
    
    async def _calculate_expectation_violation(self, discovery_text: str) -> float:
        """Calculate how much the discovery violates expectations."""
        violation_indicators = [
            "contrary to", "opposite of", "challenges", "contradicts",
            "unexpected", "surprising", "paradox", "anomaly"
        ]
        
        text_lower = discovery_text.lower()
        violation_count = sum(1 for indicator in violation_indicators if indicator in text_lower)
        
        # Normalize score
        violation_score = min(violation_count / 3, 1.0)
        
        return violation_score
    
    async def _calculate_information_gain(self, discovery_text: str) -> float:
        """Calculate information gain from the discovery."""
        # Simple heuristic based on content richness
        concepts = len(re.findall(r'\b[A-Z][a-z]+\b', discovery_text))
        relationships = len(re.findall(r'\b(causes?|leads? to|results? in|relates? to)\b', discovery_text, re.IGNORECASE))
        
        information_gain = min((concepts + relationships * 2) / 20, 1.0)
        
        return information_gain


# Discovery Tools Collection
class DiscoveryTools:
    """Collection of all discovery tools for easy registration."""
    
    def __init__(self):
        self.search_tool = SearchTool()
        self.analysis_tool = AnalysisTool()
        self.hypothesis_generator_tool = HypothesisGeneratorTool()
        self.question_generator_tool = QuestionGeneratorTool()
        self.bayesian_surprise_tool = BayesianSurpriseTool()
    
    def get_all_tools(self) -> List[DiscoveryToolBase]:
        """Get all discovery tools."""
        return [
            self.search_tool,
            self.analysis_tool,
            self.hypothesis_generator_tool,
            self.question_generator_tool,
            self.bayesian_surprise_tool
        ]
    
    def load_knowledge_base(self, knowledge_files: List[Dict[str, str]]):
        """Load knowledge base into tools that require it."""
        self.search_tool.load_knowledge_base(knowledge_files)