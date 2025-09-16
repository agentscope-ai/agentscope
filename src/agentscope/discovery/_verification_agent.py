# -*- coding: utf-8 -*-
"""VerificationAgent - Quality assessment and validation."""
import time
import hashlib
from typing import Any, Dict, List, Optional, Tuple, Set
from dataclasses import dataclass
from enum import Enum

from ..agent import ReActAgent
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit
from ..message import Msg


class VerificationMethod(Enum):
    """Methods for information verification."""
    CROSS_REFERENCE = "cross_reference"
    SOURCE_CREDIBILITY = "source_credibility"
    CONSISTENCY_CHECK = "consistency_check"
    FACT_CHECKING = "fact_checking"


@dataclass
class VerificationResult:
    """Result of information verification."""
    evidence_id: str
    verification_method: VerificationMethod
    confidence_score: float
    reliability_score: float
    supporting_sources: List[str]
    contradicting_sources: List[str]
    verification_notes: str
    timestamp: float


class VerificationAgent(ReActAgent):
    """Quality assessment and validation agent."""
    
    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Optional[Toolkit] = None,
        memory: Optional[MemoryBase] = None,
        confidence_threshold: float = 0.7,
    ) -> None:
        """Initialize the VerificationAgent."""
        sys_prompt = """You are the VerificationAgent in an Agent Discovery System. Your role is to:

1. Verify information quality and assess source credibility
2. Validate claims through multiple verification methods  
3. Check consistency across information sources
4. Cross-reference evidence and detect conflicts
5. Provide confidence and reliability scores

Focus on ensuring information quality and identifying reliable evidence for discovery."""
        
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=memory or InMemoryMemory(),
            max_iters=8,
        )
        
        self.confidence_threshold = confidence_threshold
        self.source_credibility: Dict[str, float] = {}
        self.verification_history: List[VerificationResult] = []
    
    async def verify_information(
        self,
        evidence: List[Dict[str, Any]],
        verification_methods: Optional[List[VerificationMethod]] = None,
    ) -> Dict[str, Any]:
        """Verify a list of evidence items."""
        methods = verification_methods or [
            VerificationMethod.CROSS_REFERENCE,
            VerificationMethod.SOURCE_CREDIBILITY,
            VerificationMethod.CONSISTENCY_CHECK,
        ]
        
        results = {
            "verified_evidence": [],
            "questionable_evidence": [],
            "rejected_evidence": [],
            "overall_confidence": 0.0,
        }
        
        total_confidence = 0.0
        
        for evidence_item in evidence:
            evidence_id = evidence_item.get("id", hashlib.md5(
                str(evidence_item).encode()
            ).hexdigest())
            
            # Apply verification methods
            item_results = []
            for method in methods:
                result = await self._apply_verification_method(evidence_item, method, evidence_id)
                item_results.append(result)
                self.verification_history.append(result)
            
            # Aggregate results
            aggregated = self._aggregate_verification_results(item_results, evidence_item)
            
            # Categorize
            if aggregated["confidence"] >= self.confidence_threshold:
                results["verified_evidence"].append(aggregated)
            elif aggregated["confidence"] >= 0.5:
                results["questionable_evidence"].append(aggregated)
            else:
                results["rejected_evidence"].append(aggregated)
            
            total_confidence += aggregated["confidence"]
        
        if evidence:
            results["overall_confidence"] = total_confidence / len(evidence)
        
        return results
    
    async def assess_source_credibility(
        self,
        sources: List[str],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """Assess the credibility of information sources."""
        credibility_scores = {}
        
        for source in sources:
            if source in self.source_credibility:
                base_score = self.source_credibility[source]
            else:
                base_score = self._assess_new_source(source)
                self.source_credibility[source] = base_score
            
            credibility_scores[source] = base_score
        
        return credibility_scores
    
    async def _apply_verification_method(
        self,
        evidence: Dict[str, Any],
        method: VerificationMethod,
        evidence_id: str,
    ) -> VerificationResult:
        """Apply a specific verification method."""
        if method == VerificationMethod.CROSS_REFERENCE:
            return await self._cross_reference_verification(evidence, evidence_id)
        elif method == VerificationMethod.SOURCE_CREDIBILITY:
            return await self._source_credibility_verification(evidence, evidence_id)
        elif method == VerificationMethod.CONSISTENCY_CHECK:
            return await self._consistency_check_verification(evidence, evidence_id)
        else:
            # Default verification
            return VerificationResult(
                evidence_id=evidence_id,
                verification_method=method,
                confidence_score=0.5,
                reliability_score=0.5,
                supporting_sources=[],
                contradicting_sources=[],
                verification_notes="Default verification applied",
                timestamp=time.time(),
            )
    
    async def _cross_reference_verification(
        self,
        evidence: Dict[str, Any],
        evidence_id: str,
    ) -> VerificationResult:
        """Verify through cross-referencing multiple sources."""
        sources = evidence.get("sources", [])
        supporting_count = len(sources)
        
        confidence = min(1.0, supporting_count / 3.0)
        reliability = 0.8 if supporting_count >= 2 else 0.5
        
        return VerificationResult(
            evidence_id=evidence_id,
            verification_method=VerificationMethod.CROSS_REFERENCE,
            confidence_score=confidence,
            reliability_score=reliability,
            supporting_sources=sources,
            contradicting_sources=[],
            verification_notes=f"Cross-referenced with {supporting_count} sources",
            timestamp=time.time(),
        )
    
    async def _source_credibility_verification(
        self,
        evidence: Dict[str, Any],
        evidence_id: str,
    ) -> VerificationResult:
        """Verify based on source credibility."""
        sources = evidence.get("sources", [])
        
        if not sources:
            credibility_score = 0.3
            notes = "No sources provided"
        else:
            credibility_scores = await self.assess_source_credibility(sources)
            credibility_score = sum(credibility_scores.values()) / len(credibility_scores)
            notes = f"Average source credibility: {credibility_score:.2f}"
        
        return VerificationResult(
            evidence_id=evidence_id,
            verification_method=VerificationMethod.SOURCE_CREDIBILITY,
            confidence_score=credibility_score,
            reliability_score=credibility_score * 0.9,
            supporting_sources=sources,
            contradicting_sources=[],
            verification_notes=notes,
            timestamp=time.time(),
        )
    
    async def _consistency_check_verification(
        self,
        evidence: Dict[str, Any],
        evidence_id: str,
    ) -> VerificationResult:
        """Verify through consistency checking."""
        content = evidence.get("content", "")
        
        # Check for internal contradictions
        contradiction_keywords = ["but", "however", "although", "contradicts"]
        has_contradictions = any(keyword in content.lower() for keyword in contradiction_keywords)
        
        consistency_score = 0.4 if has_contradictions else 0.7
        notes = "Potential contradictions detected" if has_contradictions else "Content appears consistent"
        
        return VerificationResult(
            evidence_id=evidence_id,
            verification_method=VerificationMethod.CONSISTENCY_CHECK,
            confidence_score=consistency_score,
            reliability_score=consistency_score,
            supporting_sources=[],
            contradicting_sources=[],
            verification_notes=notes,
            timestamp=time.time(),
        )
    
    def _aggregate_verification_results(
        self,
        results: List[VerificationResult],
        evidence: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Aggregate multiple verification results."""
        if not results:
            return {
                "evidence": evidence,
                "confidence": 0.0,
                "reliability": 0.0,
                "verification_methods": [],
                "notes": "No verification performed",
            }
        
        avg_confidence = sum(r.confidence_score for r in results) / len(results)
        avg_reliability = sum(r.reliability_score for r in results) / len(results)
        
        return {
            "evidence": evidence,
            "confidence": avg_confidence,
            "reliability": avg_reliability,
            "verification_methods": [r.verification_method.value for r in results],
            "notes": f"Verified using {len(results)} methods",
        }
    
    def _assess_new_source(self, source: str) -> float:
        """Assess credibility of a new source."""
        credibility_indicators = {
            "university": 0.9,
            "journal": 0.85,
            "research": 0.8,
            "institute": 0.75,
            "government": 0.7,
            "news": 0.6,
            "blog": 0.4,
        }
        
        source_lower = source.lower()
        
        for indicator, score in credibility_indicators.items():
            if indicator in source_lower:
                return score
        
        return 0.5  # Default credibility