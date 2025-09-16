# -*- coding: utf-8 -*-
"""WebSearchAgent - External information gathering."""
import asyncio
import time
import json
from typing import Any, Dict, List, Optional, Set
from urllib.parse import quote_plus
from dataclasses import dataclass

from ..agent import ReActAgent
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit
from ..message import Msg
from ._message import DiscoveryMessage, MessageType
from ._discovery_tools import SearchResult


@dataclass
class SearchSource:
    """Configuration for a search source."""
    
    name: str
    base_url: str
    api_key: Optional[str]
    rate_limit: float  # seconds between requests
    max_results: int
    cost_per_query: float
    reliability_score: float


class WebSearchAgent(ReActAgent):
    """
    External information gathering agent.
    
    Searches external sources for relevant information to expand
    the knowledge base and validate local knowledge.
    """
    
    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Optional[Toolkit] = None,
        memory: Optional[MemoryBase] = None,
        search_sources: Optional[Dict[str, SearchSource]] = None,
        default_max_results: int = 10,
        search_timeout: float = 30.0,
    ) -> None:
        """
        Initialize the WebSearchAgent.
        
        Args:
            name: Name of the agent
            model: Language model for processing results
            formatter: Message formatter
            toolkit: Optional toolkit
            memory: Optional memory
            search_sources: Available search sources
            default_max_results: Default maximum results per search
            search_timeout: Timeout for search operations
        """
        sys_prompt = self._create_search_prompt()
        
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=memory or InMemoryMemory(),
            max_iters=10,
        )
        
        self.search_sources = search_sources or self._get_default_sources()
        self.default_max_results = default_max_results
        self.search_timeout = search_timeout
        
        # Rate limiting tracking
        self.last_request_time: Dict[str, float] = {}
        
        # Search statistics
        self.search_stats = {
            "total_searches": 0,
            "successful_searches": 0,
            "total_results": 0,
            "average_relevance": 0.0,
        }
        
        # Register search tools
        self._register_search_tools()
        
        # Register state
        self.register_state("default_max_results")
        self.register_state("search_timeout")
    
    async def search_information(
        self,
        queries: List[str],
        sources: Optional[List[str]] = None,
        max_results_per_query: Optional[int] = None,
        diversify_results: bool = True,
    ) -> Dict[str, Any]:
        """
        Search for information using multiple queries and sources.
        
        Args:
            queries: List of search queries
            sources: Specific sources to use (if None, use all available)
            max_results_per_query: Maximum results per query
            diversify_results: Whether to diversify results across sources
            
        Returns:
            Aggregated search results and metadata
        """
        max_results = max_results_per_query or self.default_max_results
        sources_to_use = sources or list(self.search_sources.keys())
        
        all_results = []
        search_metadata = {
            "queries_processed": 0,
            "sources_used": [],
            "total_results": 0,
            "search_time": 0,
            "cost_incurred": 0.0,
        }
        
        start_time = time.time()
        
        try:
            # Process queries
            for query in queries:
                query_results = await self._process_single_query(
                    query, sources_to_use, max_results, diversify_results
                )
                
                all_results.extend(query_results["results"])
                search_metadata["queries_processed"] += 1
                search_metadata["cost_incurred"] += query_results["cost"]
                search_metadata["sources_used"].extend(query_results["sources_used"])
            
            # Remove duplicates and rank results
            unique_results = self._deduplicate_results(all_results)
            ranked_results = await self._rank_results(unique_results, queries)
            
            # Update statistics
            search_metadata["total_results"] = len(ranked_results)
            search_metadata["search_time"] = time.time() - start_time
            search_metadata["sources_used"] = list(set(search_metadata["sources_used"]))
            
            self._update_search_stats(search_metadata, ranked_results)
            
            return {
                "results": ranked_results,
                "metadata": search_metadata,
                "evidence": self._extract_evidence(ranked_results),
                "source_reliability": self._assess_source_reliability(ranked_results),
            }
            
        except Exception as e:
            return {
                "results": [],
                "metadata": search_metadata,
                "error": str(e),
                "evidence": [],
                "source_reliability": {},
            }
    
    async def search_with_context(
        self,
        query: str,
        context_concepts: List[str],
        exploration_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Search with context from current exploration state.
        
        Args:
            query: Main search query
            context_concepts: Related concepts for context
            exploration_state: Current exploration state
            
        Returns:
            Contextualized search results
        """
        # Enhance query with context
        enhanced_queries = await self._enhance_query_with_context(
            query, context_concepts, exploration_state
        )
        
        # Perform search with enhanced queries
        search_results = await self.search_information(
            queries=enhanced_queries,
            max_results_per_query=8,
        )
        
        # Add context analysis
        search_results["context_analysis"] = await self._analyze_context_relevance(
            search_results["results"], context_concepts
        )
        
        return search_results
    
    async def validate_external_knowledge(
        self,
        local_knowledge: Dict[str, Any],
        validation_queries: List[str],
    ) -> Dict[str, Any]:
        """
        Validate local knowledge against external sources.
        
        Args:
            local_knowledge: Knowledge to validate
            validation_queries: Queries for validation
            
        Returns:
            Validation results with confidence scores
        """
        validation_results = {
            "validated_claims": [],
            "conflicting_information": [],
            "supporting_evidence": [],
            "confidence_scores": {},
        }
        
        # Search for validation information
        search_results = await self.search_information(
            queries=validation_queries,
            max_results_per_query=5,
        )
        
        # Compare local knowledge with search results
        for result in search_results["results"]:
            comparison = await self._compare_with_local_knowledge(
                result, local_knowledge
            )
            
            if comparison["supports"]:
                validation_results["supporting_evidence"].append({
                    "claim": comparison["claim"],
                    "evidence": result.content,
                    "source": result.source,
                    "confidence": comparison["confidence"],
                })
            elif comparison["conflicts"]:
                validation_results["conflicting_information"].append({
                    "claim": comparison["claim"],
                    "conflicting_evidence": result.content,
                    "source": result.source,
                    "conflict_severity": comparison["severity"],
                })
        
        # Calculate overall confidence scores
        validation_results["confidence_scores"] = self._calculate_validation_confidence(
            validation_results
        )
        
        return validation_results
    
    async def discover_related_information(
        self,
        seed_concepts: List[str],
        discovery_depth: int = 2,
        max_results: int = 20,
    ) -> Dict[str, Any]:
        """
        Discover information related to seed concepts.
        
        Args:
            seed_concepts: Starting concepts for discovery
            discovery_depth: How many levels deep to explore
            max_results: Maximum total results to return
            
        Returns:
            Related information and concept map
        """
        discovered_info = {
            "related_concepts": set(),
            "information_clusters": [],
            "discovery_path": [],
            "results": [],
        }
        
        current_concepts = set(seed_concepts)
        
        for depth in range(discovery_depth):
            level_queries = []
            
            # Generate queries for current level
            for concept in current_concepts:
                level_queries.extend(await self._generate_related_queries(concept))
            
            # Search for related information
            level_results = await self.search_information(
                queries=level_queries[:10],  # Limit queries per level
                max_results_per_query=3,
            )
            
            # Extract new concepts from results
            new_concepts = set()
            for result in level_results["results"]:
                extracted_concepts = await self._extract_concepts_from_result(result)
                new_concepts.update(extracted_concepts)
            
            # Update discovery state
            discovered_info["related_concepts"].update(new_concepts)
            discovered_info["results"].extend(level_results["results"])
            discovered_info["discovery_path"].append({
                "depth": depth,
                "concepts_explored": list(current_concepts),
                "new_concepts_found": list(new_concepts),
                "results_count": len(level_results["results"]),
            })
            
            # Prepare for next level
            current_concepts = new_concepts - set(seed_concepts)
            if not current_concepts:
                break
        
        # Cluster related information
        discovered_info["information_clusters"] = await self._cluster_information(
            discovered_info["results"]
        )
        
        # Limit total results
        discovered_info["results"] = discovered_info["results"][:max_results]
        discovered_info["related_concepts"] = list(discovered_info["related_concepts"])
        
        return discovered_info
    
    async def _process_single_query(
        self,
        query: str,
        sources: List[str],
        max_results: int,
        diversify: bool,
    ) -> Dict[str, Any]:
        """Process a single search query across multiple sources."""
        results = []
        sources_used = []
        total_cost = 0.0
        
        results_per_source = max_results // len(sources) if diversify else max_results
        
        for source_name in sources:
            if source_name not in self.search_sources:
                continue
            
            source = self.search_sources[source_name]
            
            # Apply rate limiting
            await self._apply_rate_limiting(source_name, source.rate_limit)
            
            try:
                # Perform search (mock implementation)
                source_results = await self._search_single_source(
                    query, source, results_per_source
                )
                
                results.extend(source_results)
                sources_used.append(source_name)
                total_cost += source.cost_per_query
                
            except Exception as e:
                print(f"Search failed for source {source_name}: {e}")
                continue
        
        return {
            "results": results,
            "sources_used": sources_used,
            "cost": total_cost,
        }
    
    async def _search_single_source(
        self,
        query: str,
        source: SearchSource,
        max_results: int,
    ) -> List[SearchResult]:
        """Search a single source (mock implementation)."""
        # Mock search results
        results = []
        
        for i in range(min(max_results, 3)):  # Mock: return up to 3 results
            result = SearchResult(
                title=f"Search result {i+1} for '{query}' from {source.name}",
                content=f"Mock content from {source.name} about '{query}'. This would contain relevant information found through the search API.",
                url=f"{source.base_url}/result_{i+1}",
                source=source.name,
                relevance_score=source.reliability_score * (1.0 - i * 0.1),
                timestamp=time.time(),
                metadata={
                    "query": query,
                    "rank": i + 1,
                    "source_reliability": source.reliability_score,
                },
            )
            results.append(result)
        
        return results
    
    async def _apply_rate_limiting(self, source_name: str, rate_limit: float) -> None:
        """Apply rate limiting for a source."""
        if source_name in self.last_request_time:
            time_since_last = time.time() - self.last_request_time[source_name]
            if time_since_last < rate_limit:
                await asyncio.sleep(rate_limit - time_since_last)
        
        self.last_request_time[source_name] = time.time()
    
    def _deduplicate_results(self, results: List[SearchResult]) -> List[SearchResult]:
        """Remove duplicate search results."""
        seen_urls = set()
        unique_results = []
        
        for result in results:
            if result.url not in seen_urls:
                seen_urls.add(result.url)
                unique_results.append(result)
        
        return unique_results
    
    async def _rank_results(
        self,
        results: List[SearchResult],
        queries: List[str],
    ) -> List[SearchResult]:
        """Rank results by relevance and quality."""
        # Sort by relevance score and source reliability
        return sorted(
            results,
            key=lambda r: r.relevance_score * r.metadata.get("source_reliability", 0.5),
            reverse=True,
        )
    
    def _extract_evidence(self, results: List[SearchResult]) -> List[Dict[str, Any]]:
        """Extract evidence from search results."""
        evidence = []
        
        for result in results[:10]:  # Top 10 results
            evidence.append({
                "content": result.content,
                "source": result.source,
                "url": result.url,
                "relevance": result.relevance_score,
                "reliability": result.metadata.get("source_reliability", 0.5),
                "timestamp": result.timestamp,
            })
        
        return evidence
    
    def _assess_source_reliability(
        self,
        results: List[SearchResult],
    ) -> Dict[str, float]:
        """Assess reliability of sources."""
        source_scores = {}
        source_counts = {}
        
        for result in results:
            source = result.source
            reliability = result.metadata.get("source_reliability", 0.5)
            
            if source not in source_scores:
                source_scores[source] = 0.0
                source_counts[source] = 0
            
            source_scores[source] += reliability
            source_counts[source] += 1
        
        # Calculate average reliability per source
        for source in source_scores:
            source_scores[source] /= source_counts[source]
        
        return source_scores
    
    async def _enhance_query_with_context(
        self,
        query: str,
        context_concepts: List[str],
        exploration_state: Dict[str, Any],
    ) -> List[str]:
        """Enhance query with contextual information."""
        enhanced_queries = [query]  # Original query
        
        # Add context-enhanced queries
        for concept in context_concepts[:3]:  # Top 3 context concepts
            enhanced_queries.append(f"{query} {concept}")
            enhanced_queries.append(f"{concept} related to {query}")
        
        # Add exploration-specific queries
        if "focus_areas" in exploration_state:
            for focus in exploration_state["focus_areas"][:2]:
                enhanced_queries.append(f"{query} in {focus}")
        
        return enhanced_queries[:8]  # Limit total queries
    
    async def _analyze_context_relevance(
        self,
        results: List[SearchResult],
        context_concepts: List[str],
    ) -> Dict[str, Any]:
        """Analyze relevance of results to context."""
        relevance_analysis = {
            "context_matches": {},
            "average_context_relevance": 0.0,
            "highly_relevant_results": [],
        }
        
        total_relevance = 0.0
        
        for result in results:
            result_relevance = 0.0
            matched_concepts = []
            
            # Check for context concept matches
            content_lower = result.content.lower()
            for concept in context_concepts:
                if concept.lower() in content_lower:
                    result_relevance += 0.2
                    matched_concepts.append(concept)
            
            relevance_analysis["context_matches"][result.url] = matched_concepts
            
            if result_relevance > 0.4:
                relevance_analysis["highly_relevant_results"].append(result.url)
            
            total_relevance += result_relevance
        
        if results:
            relevance_analysis["average_context_relevance"] = total_relevance / len(results)
        
        return relevance_analysis
    
    async def _compare_with_local_knowledge(
        self,
        result: SearchResult,
        local_knowledge: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Compare search result with local knowledge."""
        # Mock comparison logic
        comparison = {
            "supports": False,
            "conflicts": False,
            "claim": "",
            "confidence": 0.5,
            "severity": 0.0,
        }
        
        # Simple keyword-based comparison
        result_content = result.content.lower()
        
        for claim, details in local_knowledge.items():
            if isinstance(details, dict) and "content" in details:
                local_content = details["content"].lower()
                
                # Check for supporting keywords
                if any(word in result_content for word in local_content.split()[:5]):
                    comparison["supports"] = True
                    comparison["claim"] = claim
                    comparison["confidence"] = 0.7
                    break
        
        return comparison
    
    def _calculate_validation_confidence(
        self,
        validation_results: Dict[str, Any],
    ) -> Dict[str, float]:
        """Calculate confidence scores for validation."""
        confidence_scores = {}
        
        # Base confidence on supporting vs conflicting evidence
        supporting_count = len(validation_results["supporting_evidence"])
        conflicting_count = len(validation_results["conflicting_information"])
        
        if supporting_count + conflicting_count > 0:
            support_ratio = supporting_count / (supporting_count + conflicting_count)
            base_confidence = support_ratio
        else:
            base_confidence = 0.5
        
        confidence_scores["overall"] = base_confidence
        confidence_scores["evidence_strength"] = min(1.0, supporting_count / 3.0)
        confidence_scores["conflict_severity"] = min(1.0, conflicting_count / 2.0)
        
        return confidence_scores
    
    async def _generate_related_queries(self, concept: str) -> List[str]:
        """Generate queries to find information related to a concept."""
        return [
            f"what is {concept}",
            f"{concept} applications",
            f"{concept} research",
            f"{concept} examples",
        ]
    
    async def _extract_concepts_from_result(self, result: SearchResult) -> List[str]:
        """Extract concepts from a search result."""
        # Mock concept extraction
        words = result.content.split()
        concepts = []
        
        for word in words:
            if len(word) > 4 and word[0].isupper():
                concepts.append(word)
        
        return concepts[:5]  # Return top 5 extracted concepts
    
    async def _cluster_information(
        self,
        results: List[SearchResult],
    ) -> List[Dict[str, Any]]:
        """Cluster related information."""
        # Mock clustering based on source
        clusters = {}
        
        for result in results:
            source = result.source
            if source not in clusters:
                clusters[source] = {
                    "cluster_name": f"{source}_cluster",
                    "results": [],
                    "common_themes": [],
                }
            
            clusters[source]["results"].append(result.url)
        
        return list(clusters.values())
    
    def _update_search_stats(
        self,
        metadata: Dict[str, Any],
        results: List[SearchResult],
    ) -> None:
        """Update search statistics."""
        self.search_stats["total_searches"] += metadata["queries_processed"]
        self.search_stats["successful_searches"] += 1 if results else 0
        self.search_stats["total_results"] += len(results)
        
        if results:
            avg_relevance = sum(r.relevance_score for r in results) / len(results)
            current_avg = self.search_stats["average_relevance"]
            total_searches = self.search_stats["total_searches"]
            
            # Update running average
            self.search_stats["average_relevance"] = (
                (current_avg * (total_searches - 1) + avg_relevance) / total_searches
            )
    
    def _get_default_sources(self) -> Dict[str, SearchSource]:
        """Get default search sources configuration."""
        return {
            "web_search": SearchSource(
                name="web_search",
                base_url="https://api.searchengine.com",
                api_key=None,
                rate_limit=1.0,
                max_results=10,
                cost_per_query=0.1,
                reliability_score=0.7,
            ),
            "academic": SearchSource(
                name="academic",
                base_url="https://api.academicsearch.com",
                api_key=None,
                rate_limit=2.0,
                max_results=5,
                cost_per_query=0.3,
                reliability_score=0.9,
            ),
            "news": SearchSource(
                name="news",
                base_url="https://api.newsapi.com",
                api_key=None,
                rate_limit=1.5,
                max_results=8,
                cost_per_query=0.15,
                reliability_score=0.6,
            ),
        }
    
    def _register_search_tools(self) -> None:
        """Register search-specific tools."""
        # Tools would be registered here in full implementation
        pass
    
    def _create_search_prompt(self) -> str:
        """Create system prompt for web search operations."""
        return """You are the WebSearchAgent in an Agent Discovery System. Your role is to:

1. Search external sources for relevant information
2. Validate local knowledge against external sources  
3. Discover related information and concepts
4. Assess source reliability and information quality
5. Extract evidence to support or contradict hypotheses

Key capabilities:
- Multi-source search with rate limiting and cost control
- Result deduplication and relevance ranking
- Context-aware query enhancement
- Evidence extraction and source reliability assessment
- Related concept discovery and information clustering

Focus on finding high-quality, relevant information while managing search costs and respecting API limits."""