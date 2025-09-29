# -*- coding: utf-8 -*-
"""InsightGeneratorAgent - Pattern recognition and hypothesis generation."""
import re
import time
from typing import Any, Dict, List, Optional, Set
from collections import defaultdict, Counter

from ..agent import ReActAgent
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit
from ._state import TemporaryInsight
from ._discovery_tools import HypothesisGeneratorTool, ConnectionGeneratorTool


class InsightGeneratorAgent(ReActAgent):
    """Pattern recognition and hypothesis generation agent."""
    
    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Optional[Toolkit] = None,
        memory: Optional[MemoryBase] = None,
        novelty_threshold: float = 0.6,
        confidence_threshold: float = 0.5,
    ) -> None:
        """Initialize the InsightGeneratorAgent."""
        sys_prompt = """You are the InsightGeneratorAgent. Generate novel insights through:
1. Pattern identification across evidence
2. Hypothesis generation and connection discovery
3. Cross-evidence analysis and synthesis
4. Research question formulation"""
        
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=memory or InMemoryMemory(),
            max_iters=8,
        )
        
        self.novelty_threshold = novelty_threshold
        self.confidence_threshold = confidence_threshold
        self.hypothesis_tool = HypothesisGeneratorTool()
        self.connection_tool = ConnectionGeneratorTool()
        self.generated_insights: List[TemporaryInsight] = []
        
        self.register_state("novelty_threshold")
        self.register_state("confidence_threshold")
    
    async def generate_insights(
        self,
        evidence_collection: List[Dict[str, Any]],
        existing_knowledge: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate insights from evidence collection."""
        insight_results = {
            "insights": [],
            "patterns": [],
            "hypotheses": [],
            "connections": [],
            "novelty_scores": {},
            "confidence_scores": {},
        }
        
        # Pattern detection
        patterns = await self._detect_patterns(evidence_collection)
        insight_results["patterns"] = patterns
        
        # Generate insights from patterns
        pattern_insights = await self._generate_pattern_insights(patterns)
        insight_results["insights"].extend(pattern_insights)
        
        # Cross-evidence analysis
        cross_insights = await self._analyze_cross_evidence(evidence_collection)
        insight_results["insights"].extend(cross_insights)
        
        # Knowledge integration
        integration_insights = await self._generate_integration_insights(
            evidence_collection, existing_knowledge
        )
        insight_results["insights"].extend(integration_insights)
        
        # Generate hypotheses
        hypotheses = await self._generate_hypotheses(insight_results["insights"], context)
        insight_results["hypotheses"] = hypotheses
        
        # Discover connections
        connections = await self._discover_connections(evidence_collection, insight_results["insights"])
        insight_results["connections"] = connections
        
        # Quality metrics
        insight_results["novelty_scores"] = self._calculate_novelty_scores(insight_results["insights"])
        insight_results["confidence_scores"] = self._calculate_confidence_scores(insight_results["insights"])
        
        # Filter quality insights
        insight_results["insights"] = self._filter_quality_insights(insight_results["insights"])
        
        return insight_results
    
    async def synthesize_insights(
        self,
        insight_collection: List[TemporaryInsight],
        synthesis_goal: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Synthesize multiple insights into coherent understanding."""
        synthesis_results = {
            "synthesized_insights": [],
            "insight_clusters": [],
            "meta_patterns": [],
            "synthesis_confidence": 0.0,
        }
        
        if not insight_collection:
            return synthesis_results
        
        # Cluster related insights
        clusters = self._cluster_insights(insight_collection)
        synthesis_results["insight_clusters"] = clusters
        
        # Generate meta-patterns
        meta_patterns = await self._identify_meta_patterns(clusters)
        synthesis_results["meta_patterns"] = meta_patterns
        
        # Synthesize clusters
        for cluster in clusters:
            synthesized = await self._synthesize_cluster(cluster, synthesis_goal)
            if synthesized:
                synthesis_results["synthesized_insights"].append(synthesized)
        
        # Calculate confidence
        if synthesis_results["synthesized_insights"]:
            confidences = [s["confidence"] for s in synthesis_results["synthesized_insights"]]
            synthesis_results["synthesis_confidence"] = sum(confidences) / len(confidences)
        
        return synthesis_results
    
    async def generate_research_questions(
        self,
        insights: List[Dict[str, Any]],
        knowledge_gaps: List[str],
        focus_areas: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Generate research questions from insights and gaps."""
        research_questions = []
        
        # Questions from insights
        for insight in insights:
            questions = self._extract_questions_from_insight(insight)
            research_questions.extend(questions)
        
        # Questions from gaps
        for gap in knowledge_gaps:
            questions = self._generate_gap_questions(gap, focus_areas)
            research_questions.extend(questions)
        
        # Prioritize questions
        prioritized_questions = self._prioritize_research_questions(research_questions, focus_areas)
        return prioritized_questions[:10]
    
    async def _detect_patterns(self, evidence_collection: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Detect patterns across evidence."""
        patterns = []
        
        # Frequency patterns
        concept_counts = Counter()
        for evidence in evidence_collection:
            concepts = self._extract_concepts(evidence.get("content", ""))
            concept_counts.update(concepts)
        
        frequent_concepts = concept_counts.most_common(10)
        if frequent_concepts:
            patterns.append({
                "type": "frequency_pattern",
                "description": "Frequently mentioned concepts",
                "concepts": frequent_concepts,
                "strength": min(1.0, len(frequent_concepts) / 20.0),
            })
        
        # Co-occurrence patterns
        cooccurrence_patterns = self._find_cooccurrence_patterns(evidence_collection)
        patterns.extend(cooccurrence_patterns)
        
        return patterns
    
    def _find_cooccurrence_patterns(self, evidence_collection: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Find co-occurrence patterns between concepts."""
        cooccurrence_map = defaultdict(Counter)
        patterns = []
        
        for evidence in evidence_collection:
            concepts = self._extract_concepts(evidence.get("content", ""))
            for i, concept1 in enumerate(concepts):
                for j, concept2 in enumerate(concepts):
                    if i != j:
                        cooccurrence_map[concept1][concept2] += 1
        
        # Identify strong patterns
        for concept1, cooccurrences in cooccurrence_map.items():
            for concept2, count in cooccurrences.most_common(3):
                if count >= 2:
                    patterns.append({
                        "type": "cooccurrence_pattern",
                        "description": f"Co-occurrence between {concept1} and {concept2}",
                        "concept_pair": (concept1, concept2),
                        "frequency": count,
                        "strength": min(1.0, count / 10.0),
                    })
        
        return patterns
    
    async def _generate_pattern_insights(self, patterns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate insights from patterns."""
        insights = []
        
        for pattern in patterns:
            if pattern["type"] == "frequency_pattern":
                concepts = pattern["concepts"]
                if concepts:
                    top_concept, frequency = concepts[0]
                    insights.append({
                        "type": "frequency_insight",
                        "insight_text": f"'{top_concept}' is central (appears {frequency} times)",
                        "novelty_score": 0.4,
                        "confidence": min(1.0, frequency / 10.0),
                        "concepts": [c[0] for c in concepts[:5]],
                    })
            
            elif pattern["type"] == "cooccurrence_pattern":
                concept1, concept2 = pattern["concept_pair"]
                frequency = pattern["frequency"]
                insights.append({
                    "type": "cooccurrence_insight", 
                    "insight_text": f"Strong relationship: '{concept1}' ↔ '{concept2}' ({frequency}x)",
                    "novelty_score": 0.6,
                    "confidence": min(1.0, frequency / 5.0),
                    "concepts": [concept1, concept2],
                })
        
        return insights
    
    async def _analyze_cross_evidence(self, evidence_collection: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Analyze relationships across evidence."""
        cross_insights = []
        
        # Simple contradiction detection
        for i, evidence1 in enumerate(evidence_collection):
            for j, evidence2 in enumerate(evidence_collection[i+1:], i+1):
                content1 = evidence1.get("content", "").lower()
                content2 = evidence2.get("content", "").lower()
                
                if self._detect_contradiction(content1, content2):
                    cross_insights.append({
                        "type": "contradiction_insight",
                        "insight_text": f"Contradiction between evidence {i} and {j}",
                        "novelty_score": 0.8,
                        "confidence": 0.7,
                        "concepts": [],
                    })
        
        return cross_insights
    
    async def _generate_integration_insights(
        self, evidence_collection: List[Dict[str, Any]], existing_knowledge: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Generate insights by integrating evidence with knowledge."""
        integration_insights = []
        
        for evidence in evidence_collection:
            concepts = self._extract_concepts(evidence.get("content", ""))
            
            for concept in concepts:
                if concept not in existing_knowledge:
                    integration_insights.append({
                        "type": "novel_concept_insight",
                        "insight_text": f"New concept '{concept}' identified",
                        "novelty_score": 0.9,
                        "confidence": 0.7,
                        "concepts": [concept],
                    })
        
        return integration_insights
    
    async def _generate_hypotheses(self, insights: List[Dict[str, Any]], context: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Generate hypotheses from insights."""
        hypotheses = []
        
        for insight in insights:
            insight_hypotheses = self.hypothesis_tool.generate_hypotheses(
                insight["insight_text"], context or {}
            )
            
            for hypothesis in insight_hypotheses:
                hypotheses.append({
                    "hypothesis": hypothesis["hypothesis"],
                    "confidence": hypothesis["confidence"],
                    "supporting_insight": insight["insight_text"],
                    "concepts": insight.get("concepts", []),
                })
        
        return hypotheses
    
    async def _discover_connections(self, evidence_collection: List[Dict[str, Any]], insights: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Discover connections between concepts."""
        connections = []
        
        # Collect all concepts
        all_concepts = set()
        for evidence in evidence_collection:
            all_concepts.update(self._extract_concepts(evidence.get("content", "")))
        for insight in insights:
            all_concepts.update(insight.get("concepts", []))
        
        # Generate connections
        concept_list = list(all_concepts)
        for i, concept1 in enumerate(concept_list):
            for concept2 in concept_list[i+1:]:
                connection = self.connection_tool.generate_connection(
                    concept1, concept2, {"evidence": evidence_collection}
                )
                
                if connection and connection["strength"] > 0.3:
                    connections.append({
                        "concept1": concept1,
                        "concept2": concept2,
                        "connection_type": connection["connection_type"],
                        "strength": connection["strength"],
                        "description": connection["description"],
                    })
        
        connections.sort(key=lambda x: x["strength"], reverse=True)
        return connections[:20]
    
    def _extract_concepts(self, text: str) -> List[str]:
        """Extract concepts from text."""
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        common_words = {'The', 'This', 'That', 'These', 'Those', 'And', 'Or', 'But'}
        concepts = [word for word in words if word not in common_words and len(word) > 2]
        return concepts[:10]
    
    def _detect_contradiction(self, content1: str, content2: str) -> bool:
        """Detect contradictions between content."""
        contradiction_pairs = [
            ("increase", "decrease"), ("positive", "negative"),
            ("effective", "ineffective"), ("true", "false"),
        ]
        
        for positive, negative in contradiction_pairs:
            if ((positive in content1 and negative in content2) or
                (negative in content1 and positive in content2)):
                return True
        return False
    
    def _calculate_novelty_scores(self, insights: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate novelty scores."""
        novelty_scores = {}
        for i, insight in enumerate(insights):
            base_novelty = insight.get("novelty_score", 0.5)
            if insight.get("type") == "contradiction_insight":
                base_novelty += 0.2
            elif insight.get("type") == "novel_concept_insight":
                base_novelty += 0.3
            novelty_scores[f"insight_{i}"] = min(1.0, max(0.0, base_novelty))
        return novelty_scores
    
    def _calculate_confidence_scores(self, insights: List[Dict[str, Any]]) -> Dict[str, float]:
        """Calculate confidence scores."""
        confidence_scores = {}
        for i, insight in enumerate(insights):
            confidence_scores[f"insight_{i}"] = insight.get("confidence", 0.5)
        return confidence_scores
    
    def _filter_quality_insights(self, insights: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter high-quality insights."""
        quality_insights = []
        for insight in insights:
            novelty = insight.get("novelty_score", 0.5)
            confidence = insight.get("confidence", 0.5)
            
            if novelty >= self.novelty_threshold and confidence >= self.confidence_threshold:
                insight["quality_score"] = (novelty + confidence) / 2
                quality_insights.append(insight)
        
        quality_insights.sort(key=lambda x: x.get("quality_score", 0), reverse=True)
        return quality_insights
    
    def _cluster_insights(self, insights: List[TemporaryInsight]) -> List[Dict[str, Any]]:
        """Cluster related insights."""
        clusters = []
        used_insights = set()
        
        for i, insight1 in enumerate(insights):
            if i in used_insights:
                continue
            
            cluster = [insight1]
            used_insights.add(i)
            
            for j, insight2 in enumerate(insights):
                if j <= i or j in used_insights:
                    continue
                
                concepts1 = set(getattr(insight1, 'related_concepts', []))
                concepts2 = set(getattr(insight2, 'related_concepts', []))
                
                if concepts1 and concepts2:
                    similarity = len(concepts1 & concepts2) / len(concepts1 | concepts2)
                    if similarity > 0.3:
                        cluster.append(insight2)
                        used_insights.add(j)
            
            if len(cluster) > 1:
                clusters.append({
                    "cluster_id": f"cluster_{len(clusters)}",
                    "insights": cluster,
                    "size": len(cluster),
                })
        
        return clusters
    
    async def _identify_meta_patterns(self, clusters: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Identify meta-patterns across clusters."""
        meta_patterns = []
        
        if len(clusters) >= 2:
            meta_patterns.append({
                "type": "multi_cluster_pattern",
                "description": f"Multiple insight clusters identified ({len(clusters)})",
                "cluster_count": len(clusters),
            })
        
        return meta_patterns
    
    async def _synthesize_cluster(self, cluster: Dict[str, Any], synthesis_goal: Optional[str]) -> Optional[Dict[str, Any]]:
        """Synthesize insights within a cluster."""
        insights = cluster["insights"]
        if len(insights) < 2:
            return None
        
        combined_text = ". ".join([getattr(insight, 'insight', str(insight)) for insight in insights])
        confidences = [getattr(insight, 'confidence', 0.5) for insight in insights]
        
        return {
            "synthesized_text": f"Cluster synthesis: {combined_text[:200]}...",
            "source_insights": len(insights),
            "confidence": sum(confidences) / len(confidences),
            "synthesis_goal": synthesis_goal,
        }
    
    def _extract_questions_from_insight(self, insight: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Extract research questions from insight."""
        concepts = insight.get("concepts", [])
        questions = []
        
        if concepts:
            questions.append({
                "question": f"How does {concepts[0]} relate to other concepts?",
                "type": "relationship",
                "priority": "medium",
                "concepts": [concepts[0]],
            })
        
        return questions
    
    def _generate_gap_questions(self, gap: str, focus_areas: Optional[List[str]]) -> List[Dict[str, Any]]:
        """Generate questions for knowledge gaps."""
        questions = [{
            "question": f"What information addresses the gap: {gap}?",
            "type": "information_need",
            "priority": "high",
            "concepts": [gap],
        }]
        
        return questions
    
    def _prioritize_research_questions(self, questions: List[Dict[str, Any]], focus_areas: Optional[List[str]]) -> List[Dict[str, Any]]:
        """Prioritize research questions."""
        # Simple prioritization by priority field
        priority_order = {"high": 3, "medium": 2, "low": 1}
        questions.sort(key=lambda q: priority_order.get(q.get("priority", "low"), 1), reverse=True)
        return questions