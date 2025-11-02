# -*- coding: utf-8 -*-
"""KnowledgeGraphAgent - Local knowledge management with graph operations."""
import os
import time
import hashlib
from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path

from ..agent import ReActAgent
from ..model import ChatModelBase
from ..formatter import FormatterBase
from ..memory import MemoryBase, InMemoryMemory
from ..tool import Toolkit
from ..message import Msg
from ._knowledge_infrastructure import (
    VectorDatabase, GraphDatabase, DocumentChunk, ConceptNode
)
from ._discovery_tools import AnalysisTool


class KnowledgeGraphAgent(ReActAgent):
    """
    Local knowledge management with graph operations.
    
    Manages personal knowledge graphs, performs entity extraction,
    and provides graph-based analysis capabilities.
    """
    
    def __init__(
        self,
        name: str,
        model: ChatModelBase,
        formatter: FormatterBase,
        toolkit: Optional[Toolkit] = None,
        memory: Optional[MemoryBase] = None,
        storage_base_path: str = "./knowledge_storage",
        chunk_size: int = 512,
        chunk_overlap: int = 50,
    ) -> None:
        """
        Initialize the KnowledgeGraphAgent.
        
        Args:
            name: Name of the agent
            model: Language model for analysis
            formatter: Message formatter
            toolkit: Optional toolkit
            memory: Optional memory
            storage_base_path: Base path for storing knowledge data
            chunk_size: Size of document chunks for processing
            chunk_overlap: Overlap between chunks
        """
        sys_prompt = self._create_knowledge_prompt()
        
        super().__init__(
            name=name,
            sys_prompt=sys_prompt,
            model=model,
            formatter=formatter,
            toolkit=toolkit or Toolkit(),
            memory=memory or InMemoryMemory(),
            max_iters=15,
        )
        
        self.storage_base_path = storage_base_path
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        
        # Initialize knowledge infrastructure
        os.makedirs(storage_base_path, exist_ok=True)
        
        self.vector_db = VectorDatabase(
            storage_path=os.path.join(storage_base_path, "vector_db.json"),
            use_faiss=True,
        )
        
        self.graph_db = GraphDatabase(
            storage_path=os.path.join(storage_base_path, "graph_db.json")
        )
        
        self.analysis_tool = AnalysisTool()
        
        # Register knowledge management tools
        self._register_knowledge_tools()
        
        # Register state
        self.register_state("storage_base_path")
        self.register_state("chunk_size")
        self.register_state("chunk_overlap")
    
    async def initialize_knowledge_base(
        self,
        knowledge_base_path: str,
        initial_idea: str,
        focus_areas: List[str],
    ) -> Dict[str, Any]:
        """
        Initialize knowledge base from local files.
        
        Args:
            knowledge_base_path: Path to knowledge base directory
            initial_idea: Starting concept/idea
            focus_areas: Areas to focus processing on
            
        Returns:
            Initialization results with seed concepts
        """
        if not os.path.exists(knowledge_base_path):
            raise ValueError(f"Knowledge base path does not exist: {knowledge_base_path}")
        
        # Process documents in the knowledge base
        processed_files = []
        total_chunks = 0
        
        for root, dirs, files in os.walk(knowledge_base_path):
            for file in files:
                if self._should_process_file(file):
                    file_path = os.path.join(root, file)
                    try:
                        chunks = await self._process_document(file_path)
                        if chunks:
                            self.vector_db.add_document_chunks(chunks)
                            total_chunks += len(chunks)
                            processed_files.append(file_path)
                    except Exception as e:
                        print(f"Error processing {file_path}: {e}")
        
        # Extract concepts from processed documents
        concepts_extracted = await self._extract_concepts_from_chunks()
        
        # Build initial knowledge graph
        await self._build_initial_graph(concepts_extracted)
        
        # Identify seed concepts based on initial idea and focus areas
        seed_concepts = await self._identify_seed_concepts(initial_idea, focus_areas)
        
        return {
            "status": "initialized",
            "processed_files": len(processed_files),
            "total_chunks": total_chunks,
            "concepts_extracted": len(concepts_extracted),
            "seed_concepts": seed_concepts,
            "knowledge_graph": self.graph_db.graph,
        }
    
    async def search_knowledge_base(
        self,
        query: str,
        search_type: str = "semantic",
        max_results: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        Search the knowledge base.
        
        Args:
            query: Search query
            search_type: "semantic", "graph", or "hybrid"
            max_results: Maximum results to return
            
        Returns:
            Search results with relevance scores
        """
        results = []
        
        if search_type in ["semantic", "hybrid"]:
            # Semantic search using vector database
            semantic_results = self.vector_db.search_similar(
                query, top_k=max_results, min_similarity=0.3
            )
            
            for chunk, similarity in semantic_results:
                results.append({
                    "type": "semantic",
                    "content": chunk.content,
                    "source": chunk.source_file,
                    "score": similarity,
                    "metadata": chunk.metadata,
                })
        
        if search_type in ["graph", "hybrid"]:
            # Graph-based search
            query_concepts = await self._extract_concepts_from_text(query)
            
            for concept_name in query_concepts[:3]:  # Top 3 concepts from query
                matching_concepts = self.graph_db.find_concepts(
                    name_pattern=concept_name,
                    min_confidence=0.5,
                )
                
                for concept in matching_concepts[:5]:
                    related = self.graph_db.find_related_concepts(
                        concept.id, max_distance=2
                    )
                    
                    results.append({
                        "type": "graph",
                        "concept": concept.name,
                        "concept_type": concept.concept_type,
                        "confidence": concept.confidence,
                        "related_concepts": [r[0].name for r in related[:5]],
                        "score": concept.confidence,
                    })
        
        # Sort by score and limit results
        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:max_results]
    
    async def add_new_knowledge(
        self,
        content: str,
        source: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Add new knowledge to the graph.
        
        Args:
            content: Knowledge content
            source: Source identifier
            metadata: Additional metadata
            
        Returns:
            Results of knowledge addition
        """
        # Create document chunk
        chunk_id = hashlib.md5(f"{source}_{content}".encode()).hexdigest()
        chunk = DocumentChunk(
            id=chunk_id,
            content=content,
            source_file=source,
            chunk_index=0,
            metadata=metadata or {},
        )
        
        # Add to vector database
        self.vector_db.add_document_chunks([chunk])
        
        # Extract and add concepts
        new_concepts = await self._extract_concepts_from_text(content)
        concepts_added = 0
        
        for concept_name in new_concepts:
            if not any(c.name == concept_name for c in self.graph_db.concepts.values()):
                concept = ConceptNode(
                    id=hashlib.md5(concept_name.encode()).hexdigest(),
                    name=concept_name,
                    concept_type="extracted",
                    confidence=0.7,
                    frequency=1,
                    sources=[source],
                    properties={"source_content": content[:200]},
                )
                
                self.graph_db.add_concept(concept)
                concepts_added += 1
        
        return {
            "chunk_added": True,
            "concepts_added": concepts_added,
            "new_concepts": new_concepts,
        }
    
    async def analyze_knowledge_gaps(self) -> Dict[str, Any]:
        """Analyze gaps in the knowledge graph."""
        # Get graph statistics
        stats = self.graph_db.get_statistics()
        
        # Find isolated concepts (low connectivity)
        isolated_concepts = []
        for concept_id, concept in self.graph_db.concepts.items():
            if self.graph_db.graph.degree(concept_id) <= 1:
                isolated_concepts.append(concept.name)
        
        # Find potential missing links
        missing_links = self.graph_db.predict_missing_links(top_k=20)
        
        # Identify sparse areas (low concept density)
        communities = self.graph_db.detect_communities()
        sparse_areas = [
            f"Community {i}" for i, community in enumerate(communities)
            if len(community) < 3
        ]
        
        return {
            "total_concepts": stats["num_concepts"],
            "total_relationships": stats["num_relationships"],
            "graph_density": stats["density"],
            "isolated_concepts": isolated_concepts[:10],
            "potential_missing_links": [
                f"{self.graph_db.concepts[source].name} -> {self.graph_db.concepts[target].name}"
                for source, target, score in missing_links[:10]
                if source in self.graph_db.concepts and target in self.graph_db.concepts
            ],
            "sparse_areas": sparse_areas,
            "connectivity_distribution": self._analyze_connectivity_distribution(),
        }
    
    async def get_concept_neighborhood(
        self,
        concept_name: str,
        radius: int = 2,
    ) -> Dict[str, Any]:
        """Get the neighborhood around a concept."""
        # Find concept by name
        matching_concepts = self.graph_db.find_concepts(name_pattern=concept_name)
        
        if not matching_concepts:
            return {"error": f"Concept '{concept_name}' not found"}
        
        concept = matching_concepts[0]
        
        # Get related concepts
        related = self.graph_db.find_related_concepts(
            concept.id, max_distance=radius
        )
        
        # Build neighborhood structure
        neighborhood = {
            "center_concept": {
                "name": concept.name,
                "type": concept.concept_type,
                "confidence": concept.confidence,
                "properties": concept.properties,
            },
            "related_concepts": [],
            "connections": [],
        }
        
        for related_concept, distance, weight in related:
            neighborhood["related_concepts"].append({
                "name": related_concept.name,
                "type": related_concept.concept_type,
                "distance": distance,
                "connection_strength": weight,
                "confidence": related_concept.confidence,
            })
        
        # Get direct connections
        if concept.id in self.graph_db.graph:
            for neighbor_id in self.graph_db.graph.neighbors(concept.id):
                if neighbor_id in self.graph_db.concepts:
                    neighbor = self.graph_db.concepts[neighbor_id]
                    edge_data = self.graph_db.graph[concept.id][neighbor_id]
                    
                    neighborhood["connections"].append({
                        "target": neighbor.name,
                        "relationship_type": edge_data.get("relationship_type", "related"),
                        "weight": edge_data.get("weight", 1.0),
                    })
        
        return neighborhood
    
    async def _process_document(self, file_path: str) -> List[DocumentChunk]:
        """Process a document into chunks."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
        except:
            # Try different encodings
            try:
                with open(file_path, 'r', encoding='latin-1') as f:
                    content = f.read()
            except:
                return []
        
        # Split into chunks
        chunks = []
        words = content.split()
        
        for i in range(0, len(words), self.chunk_size - self.chunk_overlap):
            chunk_words = words[i:i + self.chunk_size]
            chunk_content = ' '.join(chunk_words)
            
            if len(chunk_content.strip()) > 50:  # Minimum chunk size
                chunk_id = hashlib.md5(f"{file_path}_{i}".encode()).hexdigest()
                
                chunk = DocumentChunk(
                    id=chunk_id,
                    content=chunk_content,
                    source_file=file_path,
                    chunk_index=i // (self.chunk_size - self.chunk_overlap),
                    metadata={
                        "file_extension": Path(file_path).suffix,
                        "file_size": os.path.getsize(file_path),
                        "processing_timestamp": time.time(),
                    }
                )
                chunks.append(chunk)
        
        return chunks
    
    async def _extract_concepts_from_chunks(self) -> List[str]:
        """Extract concepts from all document chunks."""
        concepts = set()
        
        for chunk in self.vector_db.chunks:
            chunk_concepts = await self._extract_concepts_from_text(chunk.content)
            concepts.update(chunk_concepts)
        
        return list(concepts)
    
    async def _extract_concepts_from_text(self, text: str) -> List[str]:
        """Extract concepts from text using analysis tools."""
        # Extract entities
        entities = self.analysis_tool.extract_entities(text)
        entity_names = [entity["text"] for entity in entities]
        
        # Extract keywords
        keywords = self.analysis_tool.extract_keywords(text, max_keywords=10)
        keyword_names = [keyword[0] for keyword in keywords if keyword[1] > 1]
        
        # Combine and deduplicate
        concepts = list(set(entity_names + keyword_names))
        
        # Filter out very short or common words
        filtered_concepts = [
            concept for concept in concepts
            if len(concept) > 2 and concept.lower() not in {"the", "and", "for", "are", "but", "not", "you", "all", "can", "had", "her", "was", "one", "our", "out", "day", "get", "has", "him", "his", "how", "man", "new", "now", "old", "see", "two", "way", "who", "boy", "did", "its", "let", "put", "say", "she", "too", "use"}
        ]
        
        return filtered_concepts[:20]  # Limit to top 20 concepts
    
    async def _build_initial_graph(self, concepts: List[str]) -> None:
        """Build initial knowledge graph from extracted concepts."""
        # Create concept nodes
        for concept_name in concepts:
            concept_id = hashlib.md5(concept_name.encode()).hexdigest()
            
            if concept_id not in self.graph_db.concepts:
                concept = ConceptNode(
                    id=concept_id,
                    name=concept_name,
                    concept_type="extracted",
                    confidence=0.6,
                    frequency=1,
                    sources=["initial_extraction"],
                    properties={},
                )
                
                self.graph_db.add_concept(concept)
        
        # Add relationships based on co-occurrence
        await self._add_cooccurrence_relationships(concepts)
    
    async def _add_cooccurrence_relationships(self, concepts: List[str]) -> None:
        """Add relationships based on concept co-occurrence."""
        # Simple co-occurrence analysis
        concept_pairs = []
        
        for chunk in self.vector_db.chunks:
            chunk_concepts = await self._extract_concepts_from_text(chunk.content)
            
            # Add relationships for concepts appearing together
            for i, concept_a in enumerate(chunk_concepts):
                for concept_b in chunk_concepts[i+1:]:
                    concept_pairs.append((concept_a, concept_b))
        
        # Count co-occurrences and add relationships
        cooccurrence_counts = {}
        for pair in concept_pairs:
            cooccurrence_counts[pair] = cooccurrence_counts.get(pair, 0) + 1
        
        # Add relationships for frequently co-occurring concepts
        for (concept_a, concept_b), count in cooccurrence_counts.items():
            if count >= 2:  # Minimum co-occurrence threshold
                concept_a_id = hashlib.md5(concept_a.encode()).hexdigest()
                concept_b_id = hashlib.md5(concept_b.encode()).hexdigest()
                
                if concept_a_id in self.graph_db.concepts and concept_b_id in self.graph_db.concepts:
                    # Normalize weight based on frequency
                    weight = min(1.0, count / 5.0)
                    
                    self.graph_db.add_relationship(
                        concept_a_id,
                        concept_b_id,
                        "co_occurrence",
                        weight=weight,
                        properties={"count": count},
                    )
    
    async def _identify_seed_concepts(
        self,
        initial_idea: str,
        focus_areas: List[str],
    ) -> List[str]:
        """Identify seed concepts for exploration."""
        seed_concepts = set()
        
        # Extract concepts from initial idea
        idea_concepts = await self._extract_concepts_from_text(initial_idea)
        seed_concepts.update(idea_concepts)
        
        # Add concepts from focus areas
        for focus_area in focus_areas:
            focus_concepts = await self._extract_concepts_from_text(focus_area)
            seed_concepts.update(focus_concepts)
        
        # Find highly connected concepts in the graph
        if self.graph_db.graph.number_of_nodes() > 0:
            centrality = self.graph_db.calculate_centrality_measures()
            
            # Add top central concepts
            top_central = sorted(
                centrality.items(),
                key=lambda x: x[1]["degree"] + x[1]["betweenness"],
                reverse=True
            )[:5]
            
            for concept_id, _ in top_central:
                if concept_id in self.graph_db.concepts:
                    seed_concepts.add(self.graph_db.concepts[concept_id].name)
        
        return list(seed_concepts)
    
    def _should_process_file(self, filename: str) -> bool:
        """Check if file should be processed."""
        valid_extensions = {'.txt', '.md', '.rst', '.py', '.js', '.html', '.xml', '.json', '.csv'}
        return Path(filename).suffix.lower() in valid_extensions
    
    def _analyze_connectivity_distribution(self) -> Dict[str, int]:
        """Analyze the distribution of node connectivity."""
        degrees = dict(self.graph_db.graph.degree())
        distribution = {}
        
        for degree in degrees.values():
            distribution[str(degree)] = distribution.get(str(degree), 0) + 1
        
        return distribution
    
    def _register_knowledge_tools(self) -> None:
        """Register knowledge management tools."""
        # Would register specific tools for knowledge operations
        pass
    
    def _create_knowledge_prompt(self) -> str:
        """Create system prompt for knowledge management."""
        return """You are the KnowledgeGraphAgent in an Agent Discovery System. Your role is to:

1. Manage local knowledge graphs and vector databases
2. Extract concepts and entities from documents
3. Build and maintain knowledge relationships
4. Provide semantic and graph-based search capabilities
5. Identify knowledge gaps and opportunities for discovery

Key capabilities:
- Document processing and chunking
- Concept extraction and relationship mapping
- Graph analysis and centrality measures
- Knowledge gap identification
- Semantic similarity search

Focus on building comprehensive, accurate knowledge representations."""