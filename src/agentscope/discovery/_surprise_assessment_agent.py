# -*- coding: utf-8 -*-
"""SurpriseAssessmentAgent - Bayesian surprise calculation."""
import math
import time
from typing import Any, Dict, List, Optional, Tuple
from collections import defaultdict

from ..agent import ReActAgent
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit
from ..message import Msg
from ._state import SurpriseEvent, ExplorationState
from ._discovery_tools import BayesianSurpriseTool


class SurpriseAssessmentAgent(ReActAgent):
    """
    Bayesian surprise calculation agent.
    
    Calculates Bayesian surprise using KL divergence to identify
    paradigm-shifting discoveries and eureka moments.
    """
    
    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Optional[Toolkit] = None,
        memory: Optional[MemoryBase] = None,
        surprise_threshold: float = 0.7,
        paradigm_shift_threshold: float = 0.9,
    ) -> None:
        """Initialize the SurpriseAssessmentAgent."""
        sys_prompt = """You are the SurpriseAssessmentAgent in an Agent Discovery System. Your role is to:

1. Calculate Bayesian surprise using KL divergence
2. Identify paradigm-shifting discoveries and eureka moments
3. Assess information gain and belief updates
4. Detect contradictions with existing knowledge
5. Quantify the impact of new evidence on knowledge models

Focus on identifying high-surprise events that represent significant discoveries or paradigm shifts."""
        
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=memory or InMemoryMemory(),
            max_iters=6,
        )
        
        self.surprise_threshold = surprise_threshold
        self.paradigm_shift_threshold = paradigm_shift_threshold
        
        # Prior belief distributions
        self.prior_beliefs: Dict[str, Dict[str, float]] = {}
        
        # Surprise calculation tool
        self.surprise_tool = BayesianSurpriseTool()
        
        # Surprise event history
        self.surprise_events: List[SurpriseEvent] = []
        
        self.register_state("surprise_threshold")
        self.register_state("paradigm_shift_threshold")
    
    async def assess_surprise(
        self,
        new_evidence: List[Dict[str, Any]],
        current_beliefs: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Assess surprise level of new evidence.
        
        Args:
            new_evidence: New evidence to assess
            current_beliefs: Current belief state
            context: Additional context for assessment
            
        Returns:
            Surprise assessment results
        """
        surprise_results = {
            "surprise_events": [],
            "average_surprise": 0.0,
            "paradigm_shifts": [],
            "belief_updates": {},
            "surprise_distribution": {},
        }
        
        total_surprise = 0.0
        
        for evidence in new_evidence:
            # Calculate surprise for each evidence item
            surprise_assessment = await self._calculate_evidence_surprise(
                evidence, current_beliefs, context
            )
            
            # Create surprise event if threshold exceeded
            if surprise_assessment["surprise_score"] >= self.surprise_threshold:
                surprise_event = self._create_surprise_event(
                    evidence, surprise_assessment
                )
                surprise_results["surprise_events"].append(surprise_event)
                self.surprise_events.append(surprise_event)
                
                # Check for paradigm shift
                if surprise_assessment["surprise_score"] >= self.paradigm_shift_threshold:
                    surprise_results["paradigm_shifts"].append(surprise_event)
            
            total_surprise += surprise_assessment["surprise_score"]
        
        # Calculate averages and distributions
        if new_evidence:
            surprise_results["average_surprise"] = total_surprise / len(new_evidence)
        
        surprise_results["belief_updates"] = await self._calculate_belief_updates(
            new_evidence, current_beliefs
        )
        
        surprise_results["surprise_distribution"] = self._analyze_surprise_distribution(
            surprise_results["surprise_events"]
        )
        
        return surprise_results
    
    async def assess_working_memory(
        self,
        working_memory: List[Any],
        exploration_state: Optional[ExplorationState] = None,
    ) -> Dict[str, Any]:
        """Assess surprise in working memory insights."""
        working_memory_assessment = {
            "high_surprise_insights": [],
            "potential_breakthroughs": [],
            "surprise_clusters": [],
            "overall_surprise_level": 0.0,
        }
        
        total_surprise = 0.0
        insight_count = 0
        
        for insight in working_memory:
            if hasattr(insight, 'novelty_score') and hasattr(insight, 'confidence'):
                # Calculate surprise based on novelty and confidence
                surprise_score = self._calculate_insight_surprise(insight)
                
                if surprise_score >= self.surprise_threshold:
                    working_memory_assessment["high_surprise_insights"].append({
                        "insight_id": insight.id if hasattr(insight, 'id') else "unknown",
                        "insight_text": insight.insight if hasattr(insight, 'insight') else str(insight),
                        "surprise_score": surprise_score,
                        "novelty_score": insight.novelty_score if hasattr(insight, 'novelty_score') else 0.5,
                        "confidence": insight.confidence if hasattr(insight, 'confidence') else 0.5,
                    })
                
                if surprise_score >= self.paradigm_shift_threshold:
                    working_memory_assessment["potential_breakthroughs"].append({
                        "insight_id": insight.id if hasattr(insight, 'id') else "unknown",
                        "breakthrough_potential": surprise_score,
                    })
                
                total_surprise += surprise_score
                insight_count += 1
        
        if insight_count > 0:
            working_memory_assessment["overall_surprise_level"] = total_surprise / insight_count
        
        # Cluster high-surprise insights
        working_memory_assessment["surprise_clusters"] = self._cluster_surprise_insights(
            working_memory_assessment["high_surprise_insights"]
        )
        
        return working_memory_assessment
    
    async def calculate_information_gain(
        self,
        prior_distribution: Dict[str, float],
        posterior_distribution: Dict[str, float],
    ) -> Dict[str, float]:
        """Calculate information gain metrics."""
        # Calculate KL divergence
        kl_divergence = self.surprise_tool.calculate_kl_divergence(
            prior_distribution, posterior_distribution
        )
        
        # Calculate entropy change
        prior_entropy = self._calculate_entropy(prior_distribution)
        posterior_entropy = self._calculate_entropy(posterior_distribution)
        entropy_change = posterior_entropy - prior_entropy
        
        # Calculate mutual information
        mutual_information = prior_entropy - posterior_entropy
        
        return {
            "kl_divergence": kl_divergence,
            "entropy_change": entropy_change,
            "mutual_information": mutual_information,
            "information_gain": kl_divergence,  # KL divergence as information gain
            "uncertainty_reduction": -entropy_change,
        }
    
    async def detect_paradigm_shifts(
        self,
        belief_history: List[Dict[str, Any]],
        evidence_stream: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Detect paradigm shifts in belief evolution."""
        paradigm_analysis = {
            "detected_shifts": [],
            "shift_points": [],
            "paradigm_stability": 0.0,
            "major_transitions": [],
        }
        
        if len(belief_history) < 2:
            return paradigm_analysis
        
        # Analyze belief transitions
        for i in range(1, len(belief_history)):
            prev_beliefs = belief_history[i-1]
            curr_beliefs = belief_history[i]
            
            # Calculate transition surprise
            transition_surprise = await self._calculate_transition_surprise(
                prev_beliefs, curr_beliefs
            )
            
            if transition_surprise >= self.paradigm_shift_threshold:
                shift_point = {
                    "timestamp": curr_beliefs.get("timestamp", time.time()),
                    "transition_index": i,
                    "surprise_magnitude": transition_surprise,
                    "belief_change": self._analyze_belief_change(prev_beliefs, curr_beliefs),
                }
                
                paradigm_analysis["detected_shifts"].append(shift_point)
                paradigm_analysis["shift_points"].append(i)
        
        # Calculate paradigm stability
        if len(belief_history) > 1:
            total_stability = 0.0
            for i in range(1, len(belief_history)):
                stability = 1.0 - await self._calculate_transition_surprise(
                    belief_history[i-1], belief_history[i]
                )
                total_stability += max(0.0, stability)
            
            paradigm_analysis["paradigm_stability"] = total_stability / (len(belief_history) - 1)
        
        return paradigm_analysis
    
    async def _calculate_evidence_surprise(
        self,
        evidence: Dict[str, Any],
        current_beliefs: Dict[str, Any],
        context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Calculate surprise for a single evidence item."""
        evidence_content = evidence.get("content", "")
        
        # Use BayesianSurpriseTool for assessment
        surprise_assessment = self.surprise_tool.assess_surprise_level(
            evidence_content, current_beliefs
        )
        
        # Enhance with context-specific analysis
        if context:
            context_surprise = await self._assess_contextual_surprise(
                evidence, context
            )
            surprise_assessment["contextual_surprise"] = context_surprise
            
            # Combine scores
            base_surprise = surprise_assessment["overall_surprise"]
            combined_surprise = (base_surprise + context_surprise) / 2
            surprise_assessment["overall_surprise"] = combined_surprise
        
        return surprise_assessment
    
    async def _assess_contextual_surprise(
        self,
        evidence: Dict[str, Any],
        context: Dict[str, Any],
    ) -> float:
        """Assess surprise in specific context."""
        contextual_factors = {
            "domain_relevance": 0.0,
            "temporal_relevance": 0.0,
            "expectation_violation": 0.0,
        }
        
        evidence_content = evidence.get("content", "").lower()
        
        # Check domain relevance surprise
        expected_domains = context.get("expected_domains", [])
        for domain in expected_domains:
            if domain.lower() not in evidence_content:
                contextual_factors["domain_relevance"] += 0.3
        
        # Check temporal relevance
        evidence_timestamp = evidence.get("timestamp", time.time())
        expected_timeframe = context.get("expected_timeframe", {})
        if expected_timeframe:
            start_time = expected_timeframe.get("start", 0)
            end_time = expected_timeframe.get("end", time.time())
            
            if not (start_time <= evidence_timestamp <= end_time):
                contextual_factors["temporal_relevance"] = 0.4
        
        # Check expectation violations
        expectations = context.get("expectations", [])
        for expectation in expectations:
            if self._violates_expectation(evidence_content, expectation):
                contextual_factors["expectation_violation"] += 0.5
        
        # Combine contextual factors
        total_contextual_surprise = sum(contextual_factors.values()) / len(contextual_factors)
        return min(1.0, total_contextual_surprise)
    
    def _violates_expectation(self, content: str, expectation: str) -> bool:
        """Check if content violates an expectation."""
        expectation_lower = expectation.lower()
        content_lower = content.lower()
        
        # Simple contradiction detection
        contradiction_pairs = [
            ("increase", "decrease"),
            ("positive", "negative"),
            ("support", "oppose"),
            ("effective", "ineffective"),
        ]
        
        for positive, negative in contradiction_pairs:
            if ((positive in expectation_lower and negative in content_lower) or
                (negative in expectation_lower and positive in content_lower)):
                return True
        
        return False
    
    def _create_surprise_event(
        self,
        evidence: Dict[str, Any],
        surprise_assessment: Dict[str, Any],
    ) -> SurpriseEvent:
        """Create a SurpriseEvent from evidence and assessment."""
        return SurpriseEvent(
            content=evidence.get("content", ""),
            source=evidence.get("source", "unknown"),
            surprise_score=surprise_assessment["overall_surprise"],
            kl_divergence=surprise_assessment.get("kl_divergence", 0.0),
            related_concepts=self._extract_related_concepts(evidence),
            paradigm_shift=surprise_assessment["overall_surprise"] >= self.paradigm_shift_threshold,
            validation_status="pending",
            impact_assessment=surprise_assessment.get("indicators", {}),
        )
    
    def _extract_related_concepts(self, evidence: Dict[str, Any]) -> List[str]:
        """Extract related concepts from evidence."""
        content = evidence.get("content", "")
        words = content.split()
        
        # Simple concept extraction - capitalized words longer than 3 characters
        concepts = []
        for word in words:
            clean_word = word.strip(".,!?;:")
            if len(clean_word) > 3 and clean_word[0].isupper():
                concepts.append(clean_word)
        
        return concepts[:5]  # Return top 5 concepts
    
    async def _calculate_belief_updates(
        self,
        new_evidence: List[Dict[str, Any]],
        current_beliefs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Calculate how beliefs should be updated."""
        belief_updates = {
            "strengthened_beliefs": [],
            "weakened_beliefs": [],
            "new_beliefs": [],
            "contradicted_beliefs": [],
        }
        
        for evidence in new_evidence:
            content = evidence.get("content", "").lower()
            
            # Simple belief update logic
            for belief_key, belief_value in current_beliefs.items():
                if isinstance(belief_value, dict) and "content" in belief_value:
                    belief_content = belief_value["content"].lower()
                    
                    # Check for supporting evidence
                    common_words = set(content.split()) & set(belief_content.split())
                    if len(common_words) >= 2:
                        belief_updates["strengthened_beliefs"].append({
                            "belief": belief_key,
                            "support_strength": len(common_words) / 10.0,
                        })
                    
                    # Check for contradicting evidence
                    if self._detect_contradiction(content, belief_content):
                        belief_updates["contradicted_beliefs"].append({
                            "belief": belief_key,
                            "contradiction_strength": 0.7,
                        })
        
        return belief_updates
    
    def _detect_contradiction(self, content1: str, content2: str) -> bool:
        """Detect contradiction between two content strings."""
        contradiction_pairs = [
            ("increase", "decrease"),
            ("positive", "negative"),
            ("effective", "ineffective"),
            ("true", "false"),
        ]
        
        for positive, negative in contradiction_pairs:
            if ((positive in content1 and negative in content2) or
                (negative in content1 and positive in content2)):
                return True
        
        return False
    
    def _analyze_surprise_distribution(
        self,
        surprise_events: List[SurpriseEvent],
    ) -> Dict[str, Any]:
        """Analyze distribution of surprise scores."""
        if not surprise_events:
            return {"distribution": "no_events"}
        
        scores = [event.surprise_score for event in surprise_events]
        
        return {
            "min_surprise": min(scores),
            "max_surprise": max(scores),
            "mean_surprise": sum(scores) / len(scores),
            "high_surprise_count": sum(1 for s in scores if s >= self.surprise_threshold),
            "paradigm_shift_count": sum(1 for s in scores if s >= self.paradigm_shift_threshold),
        }
    
    def _calculate_insight_surprise(self, insight: Any) -> float:
        """Calculate surprise score for an insight."""
        novelty = getattr(insight, 'novelty_score', 0.5)
        confidence = getattr(insight, 'confidence', 0.5)
        
        # Surprise is high when novelty is high and confidence is also high
        # (confident about something novel is surprising)
        surprise_score = novelty * confidence
        
        # Boost for very high novelty
        if novelty > 0.8:
            surprise_score += 0.2
        
        return min(1.0, surprise_score)
    
    def _cluster_surprise_insights(
        self,
        insights: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Cluster insights by surprise characteristics."""
        if not insights:
            return []
        
        # Simple clustering by surprise score ranges
        clusters = {
            "breakthrough": [],
            "high_surprise": [],
            "moderate_surprise": [],
        }
        
        for insight in insights:
            score = insight["surprise_score"]
            if score >= self.paradigm_shift_threshold:
                clusters["breakthrough"].append(insight)
            elif score >= 0.8:
                clusters["high_surprise"].append(insight)
            else:
                clusters["moderate_surprise"].append(insight)
        
        # Convert to list format
        cluster_list = []
        for cluster_name, cluster_insights in clusters.items():
            if cluster_insights:
                cluster_list.append({
                    "cluster_type": cluster_name,
                    "insight_count": len(cluster_insights),
                    "average_surprise": sum(i["surprise_score"] for i in cluster_insights) / len(cluster_insights),
                    "insights": cluster_insights,
                })
        
        return cluster_list
    
    async def _calculate_transition_surprise(
        self,
        prev_beliefs: Dict[str, Any],
        curr_beliefs: Dict[str, Any],
    ) -> float:
        """Calculate surprise in belief transition."""
        # Mock implementation - would use proper Bayesian updating
        
        # Count belief changes
        changes = 0
        total_beliefs = 0
        
        all_keys = set(prev_beliefs.keys()) | set(curr_beliefs.keys())
        
        for key in all_keys:
            total_beliefs += 1
            
            prev_val = prev_beliefs.get(key, 0.5)
            curr_val = curr_beliefs.get(key, 0.5)
            
            # Simple change detection
            if isinstance(prev_val, (int, float)) and isinstance(curr_val, (int, float)):
                change_magnitude = abs(curr_val - prev_val)
                if change_magnitude > 0.3:  # Significant change threshold
                    changes += 1
        
        if total_beliefs == 0:
            return 0.0
        
        return changes / total_beliefs
    
    def _analyze_belief_change(
        self,
        prev_beliefs: Dict[str, Any],
        curr_beliefs: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Analyze the nature of belief changes."""
        changes = {
            "major_updates": [],
            "new_beliefs": [],
            "removed_beliefs": [],
            "stability_score": 0.0,
        }
        
        # Find new beliefs
        for key in curr_beliefs:
            if key not in prev_beliefs:
                changes["new_beliefs"].append(key)
        
        # Find removed beliefs
        for key in prev_beliefs:
            if key not in curr_beliefs:
                changes["removed_beliefs"].append(key)
        
        # Find major updates
        for key in prev_beliefs:
            if key in curr_beliefs:
                prev_val = prev_beliefs[key]
                curr_val = curr_beliefs[key]
                
                if isinstance(prev_val, (int, float)) and isinstance(curr_val, (int, float)):
                    change_magnitude = abs(curr_val - prev_val)
                    if change_magnitude > 0.5:
                        changes["major_updates"].append({
                            "belief": key,
                            "change_magnitude": change_magnitude,
                        })
        
        # Calculate stability
        total_keys = len(set(prev_beliefs.keys()) | set(curr_beliefs.keys()))
        if total_keys > 0:
            stable_keys = len(set(prev_beliefs.keys()) & set(curr_beliefs.keys()))
            changes["stability_score"] = stable_keys / total_keys
        
        return changes
    
    def _calculate_entropy(self, distribution: Dict[str, float]) -> float:
        """Calculate entropy of a probability distribution."""
        if not distribution:
            return 0.0
        
        # Normalize distribution
        total = sum(distribution.values())
        if total == 0:
            return 0.0
        
        normalized = {k: v/total for k, v in distribution.items()}
        
        # Calculate entropy
        entropy = 0.0
        for prob in normalized.values():
            if prob > 0:
                entropy -= prob * math.log2(prob)
        
        return entropy