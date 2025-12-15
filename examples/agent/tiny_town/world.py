# -*- coding: utf-8 -*-
"""Tiny Town World Definition"""
import random
from typing import List, Dict, Tuple
from dataclasses import dataclass

@dataclass
class Location:
    name: str
    x: int
    y: int
    color: Tuple[int, int, int]
    description: str

class TinyWorld:
    def __init__(self, width: int = 1200, height: int = 800):
        self.width = width
        self.height = height
        self.locations: Dict[str, Location] = {}
        self.agents = [] # List of Resident
        self.time_of_day = 8.0 # 0.0 - 24.0 (Starts at 8 AM)
        self.day_speed = 0.05 # Hours per tick
        
        self._init_map()

    def _init_map(self):
        """Create the AgentScope Hub layout."""
        # Core Departments (Enterprise)
        self.add_location("AgentScope HQ", 600, 400, (50, 100, 200), "The central command center.")
        self.add_location("Research Lab", 200, 200, (200, 200, 250), "Advanced AI research facility.")
        self.add_location("Game Studio", 1000, 200, (250, 50, 250), "Creative zone for new simulations.")
        self.add_location("Data Center", 200, 600, (50, 200, 50), "Massive server farm for data processing.")
        
        # Amenities
        self.add_location("Cafeteria", 1000, 600, (200, 150, 50), "Food and coffee for the team.")
        
        # Staff Quarters
        self.add_location("Alice's Home", 100, 100, (150, 100, 100), "Staff Dorm A.")
        self.add_location("Bob's Home", 1100, 100, (100, 100, 150), "Staff Dorm B.")
        self.add_location("Charlie's Home", 1100, 700, (150, 150, 100), "Staff Dorm C.")

    def add_location(self, name, x, y, color, desc):
        self.locations[name] = Location(name, x, y, color, desc)

    def update(self):
        """Update world state (Time)."""
        self.time_of_day += self.day_speed
        if self.time_of_day >= 24.0:
            self.time_of_day = 0.0

    def get_random_location(self):
        return random.choice(list(self.locations.values()))
