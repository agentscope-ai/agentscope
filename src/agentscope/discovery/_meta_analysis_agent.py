# -*- coding: utf-8 -*-
"""MetaAnalysisAgent - Confidence assessment and gap analysis."""
import statistics
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict, Counter

from ..agent import ReActAgent
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit
from ._state import ExplorationState, TemporaryInsight


class MetaAnalysisAgent(ReActAgent):
    """
    Meta-analysis agent for confidence assessment and knowledge gap analysis.
    
    Evaluates the overall quality and completeness of discovery results,
    identifies knowledge gaps, and provides confidence assessments.
    """
    
    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Optional[Toolkit] = None,
        memory: Optional[MemoryBase] = None,
        confidence_threshold: float = 0.7,
        gap_threshold: float = 0.5,
    ) -> None:
        """Initialize the MetaAnalysisAgent."""
        sys_prompt = """You are the MetaAnalysisAgent. Your role is to:
1. Assess confidence and quality of discovery results
2. Identify knowledge gaps and areas needing exploration
3. Evaluate completeness and coherence of insights
4. Provide meta-level analysis of discovery process"""
        
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=memory or InMemoryMemory(),
            max_iters=6,
        )
        
        self.confidence_threshold = confidence_threshold
        self.gap_threshold = gap_threshold
        
        # Analysis tracking
        self.analysis_history: List[Dict[str, Any]] = []
        self.identified_gaps: List[str] = []
        
        self.register_state("confidence_threshold")
        self.register_state("gap_threshold")
    
    async def perform_meta_analysis(
        self,
        discoveries: List[Dict[str, Any]],
        insights: List[TemporaryInsight],
        exploration_state: ExplorationState,
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Perform comprehensive meta-analysis of discovery results."""
        meta_analysis = {
            "confidence_assessment": {},
            "knowledge_gaps": [],
            "quality_metrics": {},
            "completeness_analysis": {},
            "coherence_assessment": {},
            "recommendations": [],
        }
        
        # Confidence assessment
        meta_analysis["confidence_assessment"] = await self._assess_confidence(
            discoveries, insights, exploration_state
        )
        
        # Knowledge gap analysis
        meta_analysis["knowledge_gaps"] = await self._identify_knowledge_gaps(
            discoveries, insights, context
        )
        
        # Quality metrics
        meta_analysis["quality_metrics"] = self._calculate_quality_metrics(
            discoveries, insights
        )
        
        # Completeness analysis
        meta_analysis["completeness_analysis"] = self._analyze_completeness(
            discoveries, insights, exploration_state, context
        )
        
        # Coherence assessment
        meta_analysis["coherence_assessment"] = self._assess_coherence(
            discoveries, insights
        )
        
        # Generate recommendations
        meta_analysis["recommendations"] = self._generate_recommendations(
            meta_analysis, exploration_state
        )
        
        # Store analysis
        self.analysis_history.append(meta_analysis)
        
        return meta_analysis
    
    async def evaluate_discovery_session(
        self,
        exploration_state: ExplorationState,
        final_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Evaluate overall discovery session performance."""
        evaluation = {
            "session_score": 0.0,
            "efficiency_metrics": {},
            "discovery_effectiveness": {},
            "budget_utilization": {},
            "goal_achievement": {},
        }
        
        # Calculate session score
        evaluation["session_score"] = self._calculate_session_score(
            exploration_state, final_results
        )
        
        # Efficiency metrics
        evaluation["efficiency_metrics"] = self._analyze_efficiency(exploration_state)
        
        # Discovery effectiveness
        evaluation["discovery_effectiveness"] = self._evaluate_effectiveness(final_results)
        
        # Budget utilization
        evaluation["budget_utilization"] = self._analyze_budget_utilization(exploration_state)
        
        # Goal achievement
        evaluation["goal_achievement"] = self._assess_goal_achievement(
            exploration_state, final_results
        )
        
        return evaluation
    
    async def _assess_confidence(
        self,
        discoveries: List[Dict[str, Any]],
        insights: List[TemporaryInsight],
        exploration_state: ExplorationState,
    ) -> Dict[str, Any]:
        """Assess confidence in discovery results."""
        confidence_assessment = {
            "overall_score": 0.0,
            "discovery_confidence": {},
            "insight_confidence": {},
            "evidence_strength": {},
            "verification_status": {},
        }
        
        # Assess discovery confidence
        if discoveries:
            discovery_confidences = []
            for discovery in discoveries:
                conf = discovery.get("confidence", 0.5)
                discovery_confidences.append(conf)
            
            confidence_assessment["discovery_confidence"] = {
                "mean": statistics.mean(discovery_confidences),
                "median": statistics.median(discovery_confidences),
                "min": min(discovery_confidences),
                "max": max(discovery_confidences),
                "count": len(discovery_confidences),
            }
        
        # Assess insight confidence
        if insights:
            insight_confidences = [
                getattr(insight, 'confidence', 0.5) for insight in insights
            ]
            confidence_assessment["insight_confidence"] = {
                "mean": statistics.mean(insight_confidences),
                "median": statistics.median(insight_confidences),
                "high_confidence_count": sum(1 for c in insight_confidences if c >= self.confidence_threshold),
            }
        
        # Evidence strength assessment
        evidence_strength = self._assess_evidence_strength(discoveries)
        confidence_assessment["evidence_strength"] = evidence_strength
        
        # Overall confidence score
        discovery_conf = confidence_assessment["discovery_confidence"].get("mean", 0.5)
        insight_conf = confidence_assessment["insight_confidence"].get("mean", 0.5)
        evidence_conf = evidence_strength.get("average_strength", 0.5)
        
        confidence_assessment["overall_score"] = (discovery_conf + insight_conf + evidence_conf) / 3
        
        return confidence_assessment
    
    async def _identify_knowledge_gaps(
        self,
        discoveries: List[Dict[str, Any]],
        insights: List[TemporaryInsight],
        context: Optional[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Identify knowledge gaps in discovery results."""
        knowledge_gaps = []
        
        # Analyze concept coverage
        covered_concepts = set()
        for discovery in discoveries:
            content = discovery.get("content", "")
            concepts = self._extract_concepts(content)
            covered_concepts.update(concepts)
        
        for insight in insights:
            related_concepts = getattr(insight, 'related_concepts', [])
            covered_concepts.update(related_concepts)
        
        # Check against expected concepts from context
        if context and "focus_areas" in context:
            focus_areas = context["focus_areas"]
            for area in focus_areas:
                if area not in covered_concepts:
                    knowledge_gaps.append({
                        "type": "missing_focus_area",
                        "description": f"Limited coverage of focus area: {area}",
                        "priority": "high",
                        "area": area,
                    })
        
        # Identify conceptual gaps
        conceptual_gaps = self._identify_conceptual_gaps(discoveries, insights)
        knowledge_gaps.extend(conceptual_gaps)
        
        # Identify methodological gaps
        methodological_gaps = self._identify_methodological_gaps(discoveries)
        knowledge_gaps.extend(methodological_gaps)
        
        # Store identified gaps
        self.identified_gaps.extend([gap["description"] for gap in knowledge_gaps])
        
        return knowledge_gaps
    
    def _calculate_quality_metrics(
        self,
        discoveries: List[Dict[str, Any]],
        insights: List[TemporaryInsight],
    ) -> Dict[str, Any]:
        """Calculate quality metrics for discoveries and insights."""
        quality_metrics = {
            "discovery_quality": {},
            "insight_quality": {},
            "overall_quality": 0.0,
        }
        
        # Discovery quality metrics
        if discoveries:
            novelty_scores = [d.get("novelty_score", 0.5) for d in discoveries]
            confidence_scores = [d.get("confidence", 0.5) for d in discoveries]
            
            quality_metrics["discovery_quality"] = {
                "average_novelty": statistics.mean(novelty_scores),
                "average_confidence": statistics.mean(confidence_scores),
                "quality_score": statistics.mean([
                    (n + c) / 2 for n, c in zip(novelty_scores, confidence_scores)
                ]),
                "high_quality_count": sum(1 for n, c in zip(novelty_scores, confidence_scores) 
                                        if (n + c) / 2 >= 0.7),
            }
        
        # Insight quality metrics
        if insights:
            insight_novelty = [getattr(i, 'novelty_score', 0.5) for i in insights]
            insight_confidence = [getattr(i, 'confidence', 0.5) for i in insights]
            
            quality_metrics["insight_quality"] = {
                "average_novelty": statistics.mean(insight_novelty),
                "average_confidence": statistics.mean(insight_confidence),
                "quality_score": statistics.mean([
                    (n + c) / 2 for n, c in zip(insight_novelty, insight_confidence)
                ]),
            }
        
        # Overall quality
        discovery_quality = quality_metrics["discovery_quality"].get("quality_score", 0.5)
        insight_quality = quality_metrics["insight_quality"].get("quality_score", 0.5)
        quality_metrics["overall_quality"] = (discovery_quality + insight_quality) / 2
        
        return quality_metrics
    
    def _analyze_completeness(
        self,
        discoveries: List[Dict[str, Any]],
        insights: List[TemporaryInsight],
        exploration_state: ExplorationState,
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Analyze completeness of exploration."""
        completeness_analysis = {
            "exploration_coverage": 0.0,
            "concept_coverage": {},
            "depth_analysis": {},
            "breadth_analysis": {},
        }
        
        # Calculate exploration coverage
        visited_concepts = len(exploration_state.visited_concepts)
        frontier_concepts = len(exploration_state.exploration_frontier)
        
        if visited_concepts + frontier_concepts > 0:
            completeness_analysis["exploration_coverage"] = visited_concepts / (visited_concepts + frontier_concepts)
        
        # Concept coverage analysis
        if context and "focus_areas" in context:
            focus_areas = context["focus_areas"]
            covered_areas = set()
            
            for discovery in discoveries:
                content = discovery.get("content", "").lower()
                for area in focus_areas:
                    if area.lower() in content:
                        covered_areas.add(area)
            
            completeness_analysis["concept_coverage"] = {
                "total_focus_areas": len(focus_areas),
                "covered_areas": len(covered_areas),
                "coverage_ratio": len(covered_areas) / len(focus_areas) if focus_areas else 0,
                "uncovered_areas": list(set(focus_areas) - covered_areas),
            }
        
        # Depth analysis
        completeness_analysis["depth_analysis"] = {
            "average_evidence_per_discovery": len(discoveries) / max(1, len(insights)),
            "exploration_depth": exploration_state.current_loop,
            "deep_exploration_indicators": len([d for d in discoveries if d.get("confidence", 0) > 0.8]),
        }
        
        # Breadth analysis
        all_concepts = set()
        for discovery in discoveries:
            concepts = self._extract_concepts(discovery.get("content", ""))
            all_concepts.update(concepts)
        
        completeness_analysis["breadth_analysis"] = {
            "unique_concepts_explored": len(all_concepts),
            "concept_diversity": len(all_concepts) / max(1, len(discoveries)),
        }
        
        return completeness_analysis
    
    def _assess_coherence(
        self,
        discoveries: List[Dict[str, Any]],
        insights: List[TemporaryInsight],
    ) -> Dict[str, Any]:
        """Assess coherence of discovery results."""
        coherence_assessment = {
            "logical_consistency": 0.0,
            "thematic_coherence": 0.0,
            "contradiction_analysis": {},
            "overall_coherence": 0.0,
        }
        
        # Check for contradictions
        contradictions = []
        for i, discovery1 in enumerate(discoveries):
            for j, discovery2 in enumerate(discoveries[i+1:], i+1):
                if self._detect_contradiction(discovery1, discovery2):
                    contradictions.append((i, j))
        
        coherence_assessment["contradiction_analysis"] = {
            "contradiction_count": len(contradictions),
            "contradiction_pairs": contradictions,
            "consistency_ratio": 1.0 - (len(contradictions) / max(1, len(discoveries) * (len(discoveries) - 1) / 2)),
        }
        
        # Assess thematic coherence
        coherence_assessment["thematic_coherence"] = self._assess_thematic_coherence(discoveries)
        
        # Logical consistency
        coherence_assessment["logical_consistency"] = coherence_assessment["contradiction_analysis"]["consistency_ratio"]
        
        # Overall coherence
        coherence_assessment["overall_coherence"] = (
            coherence_assessment["logical_consistency"] + 
            coherence_assessment["thematic_coherence"]
        ) / 2
        
        return coherence_assessment
    
    def _generate_recommendations(
        self,
        meta_analysis: Dict[str, Any],
        exploration_state: ExplorationState,
    ) -> List[Dict[str, Any]]:
        """Generate recommendations for improving discovery process."""
        recommendations = []
        
        # Confidence-based recommendations
        overall_confidence = meta_analysis["confidence_assessment"]["overall_score"]
        if overall_confidence < self.confidence_threshold:
            recommendations.append({
                "type": "confidence_improvement",
                "priority": "high",
                "description": "Seek additional verification for low-confidence discoveries",
                "action": "verification",
            })
        
        # Gap-based recommendations
        knowledge_gaps = meta_analysis["knowledge_gaps"]
        if knowledge_gaps:
            high_priority_gaps = [g for g in knowledge_gaps if g.get("priority") == "high"]
            if high_priority_gaps:
                recommendations.append({
                    "type": "gap_addressing",
                    "priority": "high",
                    "description": f"Address {len(high_priority_gaps)} high-priority knowledge gaps",
                    "action": "exploration",
                })
        
        # Quality-based recommendations
        overall_quality = meta_analysis["quality_metrics"]["overall_quality"]
        if overall_quality < 0.6:
            recommendations.append({
                "type": "quality_improvement",
                "priority": "medium",
                "description": "Focus on higher-quality evidence and insights",
                "action": "refinement",
            })
        
        # Completeness-based recommendations
        coverage = meta_analysis["completeness_analysis"].get("exploration_coverage", 0)
        if coverage < 0.7:
            recommendations.append({
                "type": "coverage_expansion",
                "priority": "medium",
                "description": "Expand exploration to improve coverage",
                "action": "exploration",
            })
        
        return recommendations
    
    def _calculate_session_score(
        self,
        exploration_state: ExplorationState,
        final_results: Dict[str, Any],
    ) -> float:
        """Calculate overall session score."""
        factors = []
        
        # Discovery count factor
        discoveries = final_results.get("discoveries", [])
        discovery_factor = min(1.0, len(discoveries) / 10.0)  # Normalize to 10 discoveries
        factors.append(discovery_factor)
        
        # Quality factor
        if discoveries:
            quality_scores = [d.get("confidence", 0.5) for d in discoveries]
            quality_factor = statistics.mean(quality_scores)
            factors.append(quality_factor)
        
        # Efficiency factor (discoveries per loop)
        if exploration_state.current_loop > 0:
            efficiency_factor = min(1.0, len(discoveries) / exploration_state.current_loop)
            factors.append(efficiency_factor)
        
        # Budget utilization factor
        budget_utilization = 1.0 - (exploration_state.budget_info.tokens_remaining / 
                                   exploration_state.budget_info.token_budget)
        utilization_factor = min(1.0, budget_utilization * 2)  # Optimal at 50% utilization
        factors.append(utilization_factor)
        
        return statistics.mean(factors) if factors else 0.0
    
    def _analyze_efficiency(self, exploration_state: ExplorationState) -> Dict[str, Any]:
        """Analyze exploration efficiency."""
        return {
            "loops_completed": exploration_state.current_loop,
            "concepts_per_loop": len(exploration_state.visited_concepts) / max(1, exploration_state.current_loop),
            "budget_efficiency": {
                "token_utilization": 1.0 - (exploration_state.budget_info.tokens_remaining / 
                                           exploration_state.budget_info.token_budget),
                "time_utilization": 1.0 - (exploration_state.budget_info.time_remaining / 3600.0),
            },
        }
    
    def _evaluate_effectiveness(self, final_results: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate discovery effectiveness."""
        discoveries = final_results.get("discoveries", [])
        insights = final_results.get("insights", [])
        
        return {
            "discovery_count": len(discoveries),
            "insight_count": len(insights),
            "high_confidence_discoveries": len([d for d in discoveries if d.get("confidence", 0) > 0.8]),
            "novel_discoveries": len([d for d in discoveries if d.get("novelty_score", 0) > 0.7]),
        }
    
    def _analyze_budget_utilization(self, exploration_state: ExplorationState) -> Dict[str, Any]:
        """Analyze budget utilization."""
        budget_info = exploration_state.budget_info
        
        return {
            "token_used_percentage": ((budget_info.token_budget - budget_info.tokens_remaining) / 
                                    budget_info.token_budget) * 100,
            "time_used_percentage": ((3600.0 - budget_info.time_remaining) / 3600.0) * 100,
            "loops_used_percentage": ((budget_info.max_loops - budget_info.loops_remaining) / 
                                    budget_info.max_loops) * 100,
        }
    
    def _assess_goal_achievement(
        self,
        exploration_state: ExplorationState,
        final_results: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Assess achievement of exploration goals."""
        return {
            "exploration_completed": exploration_state.current_loop >= 1,
            "discoveries_made": len(final_results.get("discoveries", [])) > 0,
            "insights_generated": len(final_results.get("insights", [])) > 0,
            "knowledge_expanded": len(exploration_state.visited_concepts) > 0,
        }
    
    def _extract_concepts(self, text: str) -> List[str]:
        """Extract concepts from text."""
        import re
        words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
        return [word for word in words if len(word) > 2][:10]
    
    def _assess_evidence_strength(self, discoveries: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Assess strength of evidence supporting discoveries."""
        if not discoveries:
            return {"average_strength": 0.0, "strong_evidence_count": 0}
        
        strength_scores = []
        for discovery in discoveries:
            sources = discovery.get("sources", [])
            confidence = discovery.get("confidence", 0.5)
            
            # Simple strength calculation
            source_strength = min(1.0, len(sources) / 3.0)  # Normalize to 3 sources
            strength = (source_strength + confidence) / 2
            strength_scores.append(strength)
        
        return {
            "average_strength": statistics.mean(strength_scores),
            "strong_evidence_count": sum(1 for s in strength_scores if s > 0.7),
        }
    
    def _identify_conceptual_gaps(
        self,
        discoveries: List[Dict[str, Any]],
        insights: List[TemporaryInsight],
    ) -> List[Dict[str, Any]]:
        """Identify conceptual gaps in knowledge."""
        gaps = []
        
        # Simple gap identification - missing connections between concepts
        all_concepts = set()
        for discovery in discoveries:
            concepts = self._extract_concepts(discovery.get("content", ""))
            all_concepts.update(concepts)
        
        # Check for isolated concepts (concepts with no connections)
        concept_connections = defaultdict(int)
        for discovery in discoveries:
            concepts = self._extract_concepts(discovery.get("content", ""))
            for i, concept1 in enumerate(concepts):
                for concept2 in concepts[i+1:]:
                    concept_connections[concept1] += 1
                    concept_connections[concept2] += 1
        
        isolated_concepts = [c for c in all_concepts if concept_connections[c] == 0]
        for concept in isolated_concepts:
            gaps.append({
                "type": "conceptual_gap",
                "description": f"Isolated concept with no connections: {concept}",
                "priority": "medium",
                "concept": concept,
            })
        
        return gaps
    
    def _identify_methodological_gaps(self, discoveries: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Identify methodological gaps in discovery process."""
        gaps = []
        
        # Check for lack of verification
        unverified_count = sum(1 for d in discoveries if not d.get("verified", False))
        if unverified_count > len(discoveries) * 0.5:
            gaps.append({
                "type": "methodological_gap",
                "description": "High number of unverified discoveries",
                "priority": "high",
                "category": "verification",
            })
        
        return gaps
    
    def _detect_contradiction(self, discovery1: Dict[str, Any], discovery2: Dict[str, Any]) -> bool:
        """Detect contradictions between discoveries."""
        content1 = discovery1.get("content", "").lower()
        content2 = discovery2.get("content", "").lower()
        
        contradiction_pairs = [
            ("increase", "decrease"), ("positive", "negative"),
            ("effective", "ineffective"), ("support", "oppose"),
        ]
        
        for positive, negative in contradiction_pairs:
            if ((positive in content1 and negative in content2) or
                (negative in content1 and positive in content2)):
                return True
        
        return False
    
    def _assess_thematic_coherence(self, discoveries: List[Dict[str, Any]]) -> float:
        """Assess thematic coherence across discoveries."""
        if len(discoveries) < 2:
            return 1.0
        
        # Simple coherence based on concept overlap
        all_concepts = []
        for discovery in discoveries:
            concepts = self._extract_concepts(discovery.get("content", ""))
            all_concepts.append(set(concepts))
        
        # Calculate average pairwise overlap
        overlaps = []
        for i, concepts1 in enumerate(all_concepts):
            for concepts2 in all_concepts[i+1:]:
                if concepts1 and concepts2:
                    overlap = len(concepts1 & concepts2) / len(concepts1 | concepts2)
                    overlaps.append(overlap)
        
        return statistics.mean(overlaps) if overlaps else 0.0