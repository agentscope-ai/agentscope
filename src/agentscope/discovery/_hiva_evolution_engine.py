# -*- coding: utf-8 -*-
"""
HiVA Evolution Engine - Core learning and adaptation engine for MBTI Agent Networks
"""

import asyncio
import time
import networkx as nx
from typing import Dict, List, Any, Optional, Set
from dataclasses import dataclass
from enum import Enum
from collections import defaultdict, deque


class EvolutionType(Enum):
    """Types of evolution supported by HiVA."""
    SEMANTIC = "semantic"
    TOPOLOGICAL = "topological"
    USER_PATTERN = "user_pattern"
    CAPABILITY = "capability"


@dataclass
class EvolutionEvent:
    """Represents an evolution event in the system."""
    event_id: str
    timestamp: float
    evolution_type: EvolutionType
    description: str
    impact_score: float
    metadata: Dict[str, Any]


class HiVAEvolutionEngine:
    """Core HiVA Evolution Engine for continuous learning and adaptation."""
    
    def __init__(self, learning_rate: float = 0.1, evolution_frequency: float = 300.0):
        self.learning_rate = learning_rate
        self.evolution_frequency = evolution_frequency
        
        # Evolution tracking
        self.evolution_history: List[EvolutionEvent] = []
        self.agent_network = defaultdict(set)  # Agent connections
        self.collaboration_strengths = defaultdict(float)  # Collaboration effectiveness
        self.user_preferences = defaultdict(dict)  # User pattern learning
        self.semantic_patterns = defaultdict(list)  # Task patterns and solutions
        
        # Learning state
        self.learning_active = False
        self.evolution_task: Optional[asyncio.Task] = None
    
    async def start_evolution_engine(self) -> None:
        """Start the continuous evolution engine."""
        if self.learning_active:
            return
        
        self.learning_active = True
        self.evolution_task = asyncio.create_task(self._evolution_loop())
        print("HiVA Evolution Engine started - continuous learning activated")
    
    async def stop_evolution_engine(self) -> None:
        """Stop the evolution engine."""
        self.learning_active = False
        if self.evolution_task:
            self.evolution_task.cancel()
            try:
                await self.evolution_task
            except asyncio.CancelledError:
                pass
        print("HiVA Evolution Engine stopped")
    
    async def _evolution_loop(self) -> None:
        """Main evolution loop."""
        while self.learning_active:
            try:
                await self._perform_evolution_cycle()
                await asyncio.sleep(self.evolution_frequency)
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Evolution cycle error: {e}")
                await asyncio.sleep(60)
    
    async def _perform_evolution_cycle(self) -> None:
        """Perform a complete evolution cycle."""
        # Semantic evolution - learn from successful patterns
        await self._semantic_evolution()
        
        # Topological evolution - optimize agent connections
        await self._topological_evolution()
        
        # User pattern evolution - adapt to user preferences
        await self._user_pattern_evolution()
        
        # Record cycle
        event = EvolutionEvent(
            event_id=f"cycle_{int(time.time())}",
            timestamp=time.time(),
            evolution_type=EvolutionType.CAPABILITY,
            description="Evolution cycle completed",
            impact_score=0.5,
            metadata={'cycle_time': time.time()}
        )
        self.evolution_history.append(event)
        
        # Keep history manageable
        if len(self.evolution_history) > 1000:
            self.evolution_history = self.evolution_history[-1000:]
    
    async def _semantic_evolution(self) -> None:
        """Evolve semantic understanding from successful patterns."""
        # Analyze recent successful patterns
        recent_events = [e for e in self.evolution_history[-100:] if e.impact_score > 0.7]
        
        if len(recent_events) >= 5:
            # Extract successful patterns
            pattern_themes = self._extract_semantic_patterns(recent_events)
            
            # Store learned patterns
            for theme in pattern_themes:
                self.semantic_patterns['successful_approaches'].append(theme)
            
            print(f"Semantic evolution: Learned {len(pattern_themes)} new patterns")
    
    async def _topological_evolution(self) -> None:
        """Optimize agent network topology."""
        if len(self.agent_network) < 2:
            return
        
        # Calculate current network efficiency
        current_efficiency = self._calculate_network_efficiency()
        
        # Generate topology improvements
        improvements = self._generate_topology_improvements()
        
        if improvements:
            best_improvement = max(improvements, key=lambda x: x['predicted_gain'])
            if best_improvement['predicted_gain'] > 0.1:
                await self._apply_topology_improvement(best_improvement)
                print(f"Topology evolution: Applied {best_improvement['type']}")
    
    async def _user_pattern_evolution(self) -> None:
        """Adapt to user patterns and preferences."""
        if not self.user_preferences:
            return
        
        # Analyze user preference trends
        preference_trends = self._analyze_preference_trends()
        
        # Generate adaptation strategies
        adaptations = self._generate_user_adaptations(preference_trends)
        
        if adaptations:
            print(f"User pattern evolution: {len(adaptations)} adaptations identified")
    
    def _extract_semantic_patterns(self, events: List[EvolutionEvent]) -> List[Dict[str, Any]]:
        """Extract semantic patterns from successful events."""
        patterns = []
        
        # Group events by metadata themes
        theme_groups = defaultdict(list)
        for event in events:
            for key, value in event.metadata.items():
                if isinstance(value, str):
                    theme_groups[key].append(value)
        
        # Identify frequent successful patterns
        for theme_type, values in theme_groups.items():
            if len(values) >= 3:
                unique_values = list(set(values))
                if len(unique_values) > 1:
                    patterns.append({
                        'theme_type': theme_type,
                        'successful_values': unique_values,
                        'frequency': len(values),
                        'confidence': len(values) / len(events)
                    })
        
        return patterns[:5]  # Top 5 patterns
    
    def _calculate_network_efficiency(self) -> float:
        """Calculate current agent network efficiency."""
        if len(self.agent_network) < 2:
            return 0.0
        
        # Create NetworkX graph
        G = nx.Graph()
        for agent, connections in self.agent_network.items():
            G.add_node(agent)
            for connected_agent in connections:
                weight = self.collaboration_strengths.get((agent, connected_agent), 0.5)
                G.add_edge(agent, connected_agent, weight=weight)
        
        if G.number_of_edges() == 0:
            return 0.0
        
        try:
            # Global efficiency metric
            efficiency = nx.global_efficiency(G)
            return min(1.0, efficiency)
        except:
            return 0.5
    
    def _generate_topology_improvements(self) -> List[Dict[str, Any]]:
        """Generate potential topology improvements."""
        improvements = []
        
        agents = list(self.agent_network.keys())
        
        # Suggest high-value connections
        for i, agent1 in enumerate(agents):
            for agent2 in agents[i+1:]:
                if agent2 not in self.agent_network[agent1]:
                    predicted_strength = self._predict_collaboration_strength(agent1, agent2)
                    if predicted_strength > 0.7:
                        improvements.append({
                            'type': 'add_connection',
                            'agents': [agent1, agent2],
                            'predicted_gain': predicted_strength * 0.1
                        })
        
        # Suggest removing weak connections
        for agent1, connections in self.agent_network.items():
            for agent2 in connections:
                strength = self.collaboration_strengths.get((agent1, agent2), 0.5)
                if strength < 0.3:
                    improvements.append({
                        'type': 'remove_connection',
                        'agents': [agent1, agent2],
                        'predicted_gain': 0.05
                    })
        
        return improvements
    
    def _predict_collaboration_strength(self, agent1: str, agent2: str) -> float:
        """Predict collaboration strength between agents."""
        # Simple MBTI compatibility prediction
        try:
            mbti1 = agent1.split('_')[0] if '_' in agent1 else None
            mbti2 = agent2.split('_')[0] if '_' in agent2 else None
            
            if mbti1 and mbti2:
                if mbti1 == mbti2:
                    return 0.6  # Same type
                else:
                    return 0.7  # Different types often complement
        except:
            pass
        
        return 0.5  # Default
    
    async def _apply_topology_improvement(self, improvement: Dict[str, Any]) -> None:
        """Apply a topology improvement."""
        if improvement['type'] == 'add_connection':
            agent1, agent2 = improvement['agents']
            self.agent_network[agent1].add(agent2)
            self.agent_network[agent2].add(agent1)
            self.collaboration_strengths[(agent1, agent2)] = 0.7
        
        elif improvement['type'] == 'remove_connection':
            agent1, agent2 = improvement['agents']
            self.agent_network[agent1].discard(agent2)
            self.agent_network[agent2].discard(agent1)
            self.collaboration_strengths.pop((agent1, agent2), None)
    
    def _analyze_preference_trends(self) -> Dict[str, Any]:
        """Analyze user preference trends."""
        trends = {}
        
        for user_id, preferences in self.user_preferences.items():
            for category, value in preferences.items():
                if category not in trends:
                    trends[category] = []
                trends[category].append(value)
        
        return trends
    
    def _generate_user_adaptations(self, trends: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Generate adaptations based on user trends."""
        adaptations = []
        
        for category, values in trends.items():
            if len(values) >= 3:
                # Simple trend detection
                if isinstance(values[0], (int, float)):
                    recent_avg = sum(values[-3:]) / 3
                    older_avg = sum(values[:-3]) / len(values[:-3]) if len(values) > 3 else recent_avg
                    
                    if recent_avg > older_avg * 1.1:
                        adaptations.append({
                            'category': category,
                            'trend': 'increasing',
                            'recommendation': f'Increase emphasis on {category}'
                        })
        
        return adaptations
    
    # Public interface methods
    
    async def record_task_execution(
        self,
        task: str,
        agents_used: List[str],
        execution_result: Dict[str, Any]
    ) -> None:
        """Record a task execution for learning."""
        
        # Update agent network
        for agent in agents_used:
            if agent not in self.agent_network:
                self.agent_network[agent] = set()
        
        # Record collaborations
        for i, agent1 in enumerate(agents_used):
            for agent2 in agents_used[i+1:]:
                self.agent_network[agent1].add(agent2)
                self.agent_network[agent2].add(agent1)
                
                # Update collaboration strength
                success_score = execution_result.get('success_score', 0.5)
                current_strength = self.collaboration_strengths.get((agent1, agent2), 0.5)
                new_strength = current_strength * 0.8 + success_score * 0.2
                self.collaboration_strengths[(agent1, agent2)] = new_strength
        
        # Record execution event
        event = EvolutionEvent(
            event_id=f"task_{int(time.time())}",
            timestamp=time.time(),
            evolution_type=EvolutionType.SEMANTIC,
            description=f"Task execution: {task[:100]}...",
            impact_score=execution_result.get('success_score', 0.5),
            metadata={
                'agent_count': len(agents_used),
                'task_type': 'user_task',
                'agents': agents_used
            }
        )
        
        self.evolution_history.append(event)
    
    async def record_user_feedback(self, user_id: str, feedback: Dict[str, Any]) -> None:
        """Record user feedback for pattern learning."""
        
        # Update user preferences
        if user_id not in self.user_preferences:
            self.user_preferences[user_id] = {}
        
        for key, value in feedback.items():
            if key in self.user_preferences[user_id] and isinstance(value, (int, float)):
                # Moving average for numeric values
                old_value = self.user_preferences[user_id][key]
                if isinstance(old_value, (int, float)):
                    self.user_preferences[user_id][key] = old_value * 0.7 + value * 0.3
                else:
                    self.user_preferences[user_id][key] = value
            else:
                self.user_preferences[user_id][key] = value
        
        # Record user event
        event = EvolutionEvent(
            event_id=f"user_{user_id}_{int(time.time())}",
            timestamp=time.time(),
            evolution_type=EvolutionType.USER_PATTERN,
            description=f"User feedback from {user_id}",
            impact_score=0.6,
            metadata={'user_id': user_id, 'feedback_keys': list(feedback.keys())}
        )
        
        self.evolution_history.append(event)
    
    def get_evolution_summary(self) -> Dict[str, Any]:
        """Get a summary of evolution activities."""
        return {
            'total_events': len(self.evolution_history),
            'network_size': len(self.agent_network),
            'network_efficiency': self._calculate_network_efficiency(),
            'learning_active': self.learning_active,
            'user_patterns': len(self.user_preferences),
            'semantic_patterns': len(self.semantic_patterns),
            'collaboration_pairs': len(self.collaboration_strengths)
        }
    
    def get_network_insights(self) -> Dict[str, Any]:
        """Get insights about the agent network."""
        if not self.agent_network:
            return {'message': 'No network data available'}
        
        # Calculate network metrics
        total_connections = sum(len(connections) for connections in self.agent_network.values()) // 2
        avg_connections = total_connections / len(self.agent_network) if self.agent_network else 0
        
        # Find best collaborations
        best_collaborations = sorted(
            self.collaboration_strengths.items(),
            key=lambda x: x[1],
            reverse=True
        )[:5]
        
        return {
            'agents': list(self.agent_network.keys()),
            'total_connections': total_connections,
            'average_connections_per_agent': avg_connections,
            'network_efficiency': self._calculate_network_efficiency(),
            'best_collaborations': [
                {'agents': agents, 'strength': strength}
                for agents, strength in best_collaborations
            ]
        }