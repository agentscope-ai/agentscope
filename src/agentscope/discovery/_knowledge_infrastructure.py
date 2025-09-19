# -*- coding: utf-8 -*-
"""
Knowledge Infrastructure - Universal Knowledge Graph and Continuous Learning Support

This module provides simplified knowledge infrastructure to support the HiVA system
with pattern storage, retrieval, and learning capabilities.
"""

import json
import time
from typing import Dict, List, Any, Optional, Set, Tuple
from dataclasses import dataclass, asdict
from collections import defaultdict


@dataclass
class DocumentChunk:
    """Represents a chunk of a document for processing and storage."""
    chunk_id: str
    content: str
    source_file: str
    chunk_index: int
    metadata: Dict[str, Any]
    embedding: Optional[List[float]] = None
    created_time: float = 0.0
    
    def __post_init__(self):
        if self.created_time == 0.0:
            self.created_time = time.time()


@dataclass
class KnowledgeNode:
    """Represents a node in the knowledge graph."""
    node_id: str
    node_type: str  # 'concept', 'pattern', 'insight', 'relationship'
    content: Dict[str, Any]
    connections: List[str]  # Connected node IDs
    metadata: Dict[str, Any]
    created_time: float
    last_accessed: float
    access_count: int = 0


@dataclass
class LearningPattern:
    """Represents a learned pattern in the system."""
    pattern_id: str
    pattern_type: str  # 'task', 'collaboration', 'user_preference', 'success'
    pattern_data: Dict[str, Any]
    confidence_score: float
    evidence_count: int
    last_reinforced: float
    applications: List[str]  # Where this pattern has been applied


class UniversalKnowledgeGraph:
    """
    Simplified universal knowledge graph for storing and retrieving
    patterns, insights, and relationships in the HiVA system.
    """
    
    def __init__(self, max_nodes: int = 10000):
        self.max_nodes = max_nodes
        
        # Core storage
        self.nodes: Dict[str, KnowledgeNode] = {}
        self.patterns: Dict[str, LearningPattern] = {}
        
        # Indexes for efficient retrieval
        self.type_index: Dict[str, Set[str]] = defaultdict(set)  # node_type -> node_ids
        self.content_keywords: Dict[str, Set[str]] = defaultdict(set)  # keyword -> node_ids
        self.pattern_index: Dict[str, Set[str]] = defaultdict(set)  # pattern_type -> pattern_ids
        
        # Relationship tracking
        self.relationship_graph: Dict[str, Set[str]] = defaultdict(set)
        
        # Statistics
        self.stats = {
            'total_nodes': 0,
            'total_patterns': 0,
            'total_relationships': 0,
            'last_cleanup': time.time()
        }
    
    def add_knowledge_node(
        self,
        node_type: str,
        content: Dict[str, Any],
        connections: List[str] = None,
        metadata: Dict[str, Any] = None
    ) -> str:
        """Add a new knowledge node to the graph."""
        
        node_id = f"{node_type}_{int(time.time())}_{len(self.nodes)}"
        
        node = KnowledgeNode(
            node_id=node_id,
            node_type=node_type,
            content=content,
            connections=connections or [],
            metadata=metadata or {},
            created_time=time.time(),
            last_accessed=time.time()
        )
        
        # Store node
        self.nodes[node_id] = node
        
        # Update indexes
        self.type_index[node_type].add(node_id)
        
        # Index content keywords
        for key, value in content.items():
            if isinstance(value, str):
                keywords = self._extract_keywords(value)
                for keyword in keywords:
                    self.content_keywords[keyword].add(node_id)
        
        # Add relationships
        for connected_id in connections or []:
            if connected_id in self.nodes:
                self.relationship_graph[node_id].add(connected_id)
                self.relationship_graph[connected_id].add(node_id)
        
        self.stats['total_nodes'] = len(self.nodes)
        self.stats['total_relationships'] = sum(len(connections) for connections in self.relationship_graph.values()) // 2
        
        # Cleanup if needed
        if len(self.nodes) > self.max_nodes:
            self._cleanup_old_nodes()
        
        return node_id
    
    def add_learning_pattern(
        self,
        pattern_type: str,
        pattern_data: Dict[str, Any],
        confidence_score: float,
        evidence_count: int = 1
    ) -> str:
        """Add a new learning pattern."""
        
        pattern_id = f"{pattern_type}_{int(time.time())}_{len(self.patterns)}"
        
        pattern = LearningPattern(
            pattern_id=pattern_id,
            pattern_type=pattern_type,
            pattern_data=pattern_data,
            confidence_score=confidence_score,
            evidence_count=evidence_count,
            last_reinforced=time.time(),
            applications=[]
        )
        
        self.patterns[pattern_id] = pattern
        self.pattern_index[pattern_type].add(pattern_id)
        
        self.stats['total_patterns'] = len(self.patterns)
        
        return pattern_id
    
    def find_nodes_by_type(self, node_type: str, limit: int = 50) -> List[KnowledgeNode]:
        """Find nodes by type."""
        node_ids = list(self.type_index.get(node_type, set()))[:limit]
        nodes = [self.nodes[node_id] for node_id in node_ids if node_id in self.nodes]
        
        # Update access tracking
        for node in nodes:
            node.last_accessed = time.time()
            node.access_count += 1
        
        return nodes
    
    def find_nodes_by_content(self, keywords: List[str], limit: int = 50) -> List[KnowledgeNode]:
        """Find nodes by content keywords."""
        matching_node_ids = set()
        
        for keyword in keywords:
            keyword_lower = keyword.lower()
            matching_ids = self.content_keywords.get(keyword_lower, set())
            if matching_node_ids:
                matching_node_ids &= matching_ids  # Intersection
            else:
                matching_node_ids = matching_ids.copy()
        
        # Get nodes and sort by relevance (access count + recency)
        nodes = []
        for node_id in list(matching_node_ids)[:limit]:
            if node_id in self.nodes:
                node = self.nodes[node_id]
                node.last_accessed = time.time()
                node.access_count += 1
                nodes.append(node)
        
        # Sort by relevance score
        nodes.sort(key=lambda n: n.access_count + (1 / (time.time() - n.created_time + 1)), reverse=True)
        
        return nodes[:limit]
    
    def find_patterns_by_type(self, pattern_type: str, min_confidence: float = 0.5) -> List[LearningPattern]:
        """Find learning patterns by type and confidence."""
        pattern_ids = self.pattern_index.get(pattern_type, set())
        patterns = [
            self.patterns[pattern_id] 
            for pattern_id in pattern_ids 
            if pattern_id in self.patterns and self.patterns[pattern_id].confidence_score >= min_confidence
        ]
        
        # Sort by confidence and evidence
        patterns.sort(key=lambda p: p.confidence_score * p.evidence_count, reverse=True)
        
        return patterns
    
    def reinforce_pattern(self, pattern_id: str, additional_evidence: int = 1) -> bool:
        """Reinforce a learning pattern with additional evidence."""
        if pattern_id not in self.patterns:
            return False
        
        pattern = self.patterns[pattern_id]
        pattern.evidence_count += additional_evidence
        pattern.last_reinforced = time.time()
        
        # Update confidence based on evidence (simple formula)
        pattern.confidence_score = min(1.0, pattern.confidence_score + 0.1 * additional_evidence)
        
        return True
    
    def find_related_nodes(self, node_id: str, max_depth: int = 2) -> List[KnowledgeNode]:
        """Find nodes related to a given node."""
        if node_id not in self.nodes:
            return []
        
        related_ids = set()
        current_level = {node_id}
        
        for depth in range(max_depth):
            next_level = set()
            for current_id in current_level:
                connections = self.relationship_graph.get(current_id, set())
                next_level.update(connections)
                related_ids.update(connections)
            current_level = next_level - related_ids  # Only new nodes
        
        related_nodes = [
            self.nodes[rel_id] 
            for rel_id in related_ids 
            if rel_id in self.nodes
        ]
        
        return related_nodes
    
    def get_insights_for_task(self, task_description: str, task_type: str = None) -> Dict[str, Any]:
        """Get relevant insights for a task."""
        insights = {
            'relevant_patterns': [],
            'similar_tasks': [],
            'success_patterns': [],
            'recommendations': []
        }
        
        # Extract keywords from task
        keywords = self._extract_keywords(task_description)
        
        # Find relevant patterns
        if task_type:
            task_patterns = self.find_patterns_by_type(f"task_{task_type}")
            insights['relevant_patterns'].extend(task_patterns[:5])
        
        # Find success patterns
        success_patterns = self.find_patterns_by_type("success", min_confidence=0.7)
        insights['success_patterns'].extend(success_patterns[:3])
        
        # Find similar tasks
        similar_nodes = self.find_nodes_by_content(keywords, limit=10)
        task_nodes = [node for node in similar_nodes if node.node_type == 'task']
        insights['similar_tasks'].extend(task_nodes[:5])
        
        # Generate recommendations based on patterns
        recommendations = self._generate_recommendations(insights)
        insights['recommendations'] = recommendations
        
        return insights
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extract keywords from text (simplified implementation)."""
        if not isinstance(text, str):
            return []
        
        # Simple keyword extraction
        words = text.lower().split()
        
        # Filter out common words
        stop_words = {'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 'been', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should'}
        
        keywords = [word for word in words if len(word) > 2 and word not in stop_words]
        
        return keywords[:10]  # Limit to 10 keywords
    
    def _generate_recommendations(self, insights: Dict[str, Any]) -> List[str]:
        """Generate recommendations based on insights."""
        recommendations = []
        
        # Analyze success patterns
        success_patterns = insights.get('success_patterns', [])
        for pattern in success_patterns[:3]:
            pattern_data = pattern.pattern_data
            if 'approach' in pattern_data:
                recommendations.append(f"Consider approach: {pattern_data['approach']}")
            if 'collaboration_style' in pattern_data:
                recommendations.append(f"Effective collaboration: {pattern_data['collaboration_style']}")
        
        # Analyze relevant patterns
        relevant_patterns = insights.get('relevant_patterns', [])
        for pattern in relevant_patterns[:2]:
            recommendations.append(f"Based on similar tasks: {pattern.pattern_data.get('recommendation', 'Apply learned patterns')}")
        
        # Default recommendations if none found
        if not recommendations:
            recommendations = [
                "Apply diverse MBTI perspectives for comprehensive analysis",
                "Consider both analytical and creative approaches",
                "Ensure domain expertise alignment with task requirements"
            ]
        
        return recommendations[:5]  # Limit to 5 recommendations
    
    def _cleanup_old_nodes(self) -> None:
        """Clean up old, unused nodes to maintain performance."""
        current_time = time.time()
        
        # Find nodes to remove (oldest, least accessed)
        nodes_to_remove = []
        for node_id, node in self.nodes.items():
            age = current_time - node.created_time
            last_access_age = current_time - node.last_accessed
            
            # Remove if very old and not recently accessed
            if age > 86400 * 30 and last_access_age > 86400 * 7 and node.access_count < 3:  # 30 days old, 7 days since access, low access count
                nodes_to_remove.append(node_id)
        
        # Remove oldest nodes first
        nodes_to_remove.sort(key=lambda nid: self.nodes[nid].created_time)
        
        # Remove up to 10% of nodes
        removal_limit = min(len(nodes_to_remove), self.max_nodes // 10)
        
        for node_id in nodes_to_remove[:removal_limit]:
            self._remove_node(node_id)
        
        self.stats['last_cleanup'] = current_time
        
        if removal_limit > 0:
            print(f"Knowledge graph cleanup: removed {removal_limit} old nodes")
    
    def _remove_node(self, node_id: str) -> None:
        """Remove a node and clean up indexes."""
        if node_id not in self.nodes:
            return
        
        node = self.nodes[node_id]
        
        # Remove from indexes
        self.type_index[node.node_type].discard(node_id)
        
        for keyword, node_ids in self.content_keywords.items():
            node_ids.discard(node_id)
        
        # Remove relationships
        for connected_id in self.relationship_graph.get(node_id, set()):
            self.relationship_graph[connected_id].discard(node_id)
        
        del self.relationship_graph[node_id]
        
        # Remove node
        del self.nodes[node_id]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge graph statistics."""
        return {
            'total_nodes': len(self.nodes),
            'total_patterns': len(self.patterns),
            'total_relationships': sum(len(connections) for connections in self.relationship_graph.values()) // 2,
            'node_types': {node_type: len(node_ids) for node_type, node_ids in self.type_index.items()},
            'pattern_types': {pattern_type: len(pattern_ids) for pattern_type, pattern_ids in self.pattern_index.items()},
            'last_cleanup': self.stats['last_cleanup'],
            'average_connections_per_node': sum(len(connections) for connections in self.relationship_graph.values()) / len(self.nodes) if self.nodes else 0
        }


class ContinuousLearningSystem:
    """
    System for continuous learning and knowledge accumulation
    integrated with the Universal Knowledge Graph.
    """
    
    def __init__(self, knowledge_graph: UniversalKnowledgeGraph):
        self.knowledge_graph = knowledge_graph
        self.learning_sessions: List[Dict[str, Any]] = []
        self.pattern_recognition_threshold = 3  # Minimum occurrences to form a pattern
    
    async def learn_from_task_execution(
        self,
        task: str,
        agents_used: List[str],
        execution_results: Dict[str, Any],
        success_metrics: Dict[str, float]
    ) -> None:
        """Learn from a completed task execution."""
        
        # Create task node
        task_node_id = self.knowledge_graph.add_knowledge_node(
            node_type="task",
            content={
                "task_description": task,
                "agents_used": agents_used,
                "success_score": success_metrics.get('success_score', 0.5),
                "execution_summary": execution_results.get('synthesis', ''),
                "agent_count": len(agents_used)
            },
            metadata={
                "timestamp": time.time(),
                "execution_time": execution_results.get('execution_time', 0),
                "complexity_level": execution_results.get('complexity_level', 3)
            }
        )
        
        # Learn collaboration patterns
        if len(agents_used) > 1:
            await self._learn_collaboration_patterns(agents_used, success_metrics)
        
        # Learn task success patterns
        if success_metrics.get('success_score', 0) > 0.7:
            await self._learn_success_patterns(task, agents_used, execution_results)
        
        # Update learning session
        session = {
            'timestamp': time.time(),
            'task_node_id': task_node_id,
            'success_score': success_metrics.get('success_score', 0.5),
            'learning_points': []
        }
        
        self.learning_sessions.append(session)
        
        # Maintain session history
        if len(self.learning_sessions) > 1000:
            self.learning_sessions = self.learning_sessions[-1000:]
    
    async def _learn_collaboration_patterns(
        self,
        agents_used: List[str],
        success_metrics: Dict[str, float]
    ) -> None:
        """Learn patterns from agent collaborations."""
        
        success_score = success_metrics.get('success_score', 0.5)
        
        # Extract MBTI types from agent IDs
        mbti_types = []
        for agent_id in agents_used:
            try:
                mbti_type = agent_id.split('_')[0]
                if len(mbti_type) == 4:  # Valid MBTI type
                    mbti_types.append(mbti_type)
            except:
                pass
        
        if len(mbti_types) > 1:
            # Create collaboration pattern
            pattern_data = {
                'mbti_combination': sorted(mbti_types),
                'agent_count': len(agents_used),
                'success_score': success_score,
                'collaboration_type': 'multi_mbti'
            }
            
            self.knowledge_graph.add_learning_pattern(
                pattern_type="collaboration",
                pattern_data=pattern_data,
                confidence_score=min(1.0, success_score),
                evidence_count=1
            )
    
    async def _learn_success_patterns(
        self,
        task: str,
        agents_used: List[str],
        execution_results: Dict[str, Any]
    ) -> None:
        """Learn patterns from successful executions."""
        
        # Extract key characteristics of successful approach
        success_characteristics = {
            'task_keywords': self.knowledge_graph._extract_keywords(task),
            'agent_count': len(agents_used),
            'approach_indicators': self._extract_approach_indicators(execution_results),
            'synthesis_quality': len(execution_results.get('synthesis', '')) > 500
        }
        
        self.knowledge_graph.add_learning_pattern(
            pattern_type="success",
            pattern_data=success_characteristics,
            confidence_score=0.8,
            evidence_count=1
        )
    
    def _extract_approach_indicators(self, execution_results: Dict[str, Any]) -> List[str]:
        """Extract approach indicators from execution results."""
        indicators = []
        
        synthesis = execution_results.get('synthesis', '').lower()
        
        # Look for approach keywords
        approach_keywords = {
            'analytical': ['analyze', 'data', 'systematic', 'logical'],
            'creative': ['innovative', 'creative', 'brainstorm', 'imagine'],
            'collaborative': ['together', 'team', 'consensus', 'diverse'],
            'strategic': ['long-term', 'strategic', 'plan', 'vision'],
            'practical': ['practical', 'implement', 'real-world', 'actionable']
        }
        
        for approach, keywords in approach_keywords.items():
            if any(keyword in synthesis for keyword in keywords):
                indicators.append(approach)
        
        return indicators
    
    def get_learning_recommendations(self, context: Dict[str, Any]) -> List[str]:
        """Get learning-based recommendations for a given context."""
        
        recommendations = []
        
        # Get insights from knowledge graph
        if 'task' in context:
            insights = self.knowledge_graph.get_insights_for_task(context['task'])
            recommendations.extend(insights.get('recommendations', []))
        
        # Add pattern-based recommendations
        recent_patterns = self._analyze_recent_learning_patterns()
        recommendations.extend(recent_patterns)
        
        return recommendations[:5]  # Limit to top 5
    
    def _analyze_recent_learning_patterns(self) -> List[str]:
        """Analyze recent learning patterns for recommendations."""
        recommendations = []
        
        # Analyze recent successful sessions
        recent_sessions = self.learning_sessions[-20:] if self.learning_sessions else []
        successful_sessions = [s for s in recent_sessions if s['success_score'] > 0.7]
        
        if len(successful_sessions) >= 3:
            recommendations.append("Recent successful patterns suggest maintaining current agent selection approach")
        
        # Analyze collaboration patterns
        collaboration_patterns = self.knowledge_graph.find_patterns_by_type("collaboration", min_confidence=0.6)
        if collaboration_patterns:
            best_pattern = collaboration_patterns[0]
            mbti_combo = best_pattern.pattern_data.get('mbti_combination', [])
            if mbti_combo:
                recommendations.append(f"High-performing MBTI combination: {', '.join(mbti_combo)}")
        
        return recommendations


# Aliases for backward compatibility
GraphDatabase = UniversalKnowledgeGraph
VectorDatabase = UniversalKnowledgeGraph  # Using same implementation for simplicity
ConceptNode = KnowledgeNode