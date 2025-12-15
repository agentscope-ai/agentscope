# -*- coding: utf-8 -*-
"""Tiny Town Resident Agent"""
import math
import random
import threading
import json
import os
from agentscope.agent import ReActAgent
from agentscope.message import Msg
from agentscope.model import OpenAIChatModel
from agentscope.formatter import DeepSeekChatFormatter

# System prompt for the residents
RESIDENT_SYSTEM_PROMPT = """You are a resident of Tiny Town.
You have a name, a personality, and current needs.

Locations: {locations}

Your State:
- Hunger: {hunger}/100 (High = Hungry)
- Energy: {energy}/100 (Low = Tired)
- Current Location: {current_loc_name}

RULES:
1. If Hunger > 80, go to Bakery and EAT.
2. If Energy < 20, go Home and SLEEP.
3. If at a location and needs are met, WORK or MINGLE, then move to another place.
4. Don't stay in one place forever.

Output ONLY a JSON command:
{{
    "action": "MOVE" | "EAT" | "SLEEP" | "WORK" | "WAIT",
    "target": "Location Name" (only for MOVE),
    "duration": 60 (frames, optional, default 60. 60 frames = 1 sec),
    "thought": "Internal monologue"
}}
"""

class Resident:
    def __init__(self, name: str, personality: str, world, model, avatar_color):
        self.name = name
        self.world = world
        self.personality = personality
        self.color = avatar_color
        
        # Physical State
        self.home_name = f"{name}'s Home"
        home = world.locations.get(self.home_name) or world.get_random_location()
        self.x = home.x
        self.y = home.y
        self.target_x = self.x
        self.target_y = self.y
        self.speed = 3.0 # Faster
        
        # Needs
        self.hunger = 50 
        self.energy = 80 
        self.current_action = "Idle"
        self.thought = "I just woke up."
        self.thought = "I just woke up."
        self.action_timer = 0
        self.time_stationary = 0
        
        # Memory
        import os
        self.load_memory()
        
        # Brain
        self.brain = ReActAgent(
            name=name,
            sys_prompt=RESIDENT_SYSTEM_PROMPT, # Format later
            model=model,
            formatter=DeepSeekChatFormatter(),
        )

        self.decision_timer = random.randint(0, 100) # De-sync startup
        self.decision_interval = 60 + random.randint(-10, 10) # Different tick rates

    def get_nearest_location_name(self):
        for name, loc in self.world.locations.items():
            dx = loc.x - self.x
            dy = loc.y - self.y
            if math.hypot(dx, dy) < 50:
                return name
        return "Street"

    def load_memory(self):
        """Load agent state from storage."""
        mem_path = os.path.join("memory", f"{self.name}.json")
        if os.path.exists(mem_path):
            try:
                with open(mem_path, "r") as f:
                    data = json.load(f)
                    self.x = data.get("x", self.x)
                    self.y = data.get("y", self.y)
                    self.target_x = data.get("tx", self.x)
                    self.target_y = data.get("ty", self.y)
                    self.hunger = data.get("hunger", 50)
                    self.energy = data.get("energy", 80)
                    self.thought = data.get("thought", "Waking up...")
                    print(f"[{self.name}] Memory loaded.")
            except Exception as e:
                print(f"[{self.name}] Failed to load memory: {e}")

    def save_memory(self):
        """Save agent state to storage."""
        if not os.path.exists("memory"):
            os.makedirs("memory")
            
        data = {
            "x": self.x,
            "y": self.y,
            "tx": self.target_x,
            "ty": self.target_y,
            "hunger": self.hunger,
            "energy": self.energy,
            "thought": self.thought
        }
        try:
            with open(os.path.join("memory", f"{self.name}.json"), "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"[{self.name}] Failed to save memory: {e}")

    def update(self):
        # 1. Needs update (Faster decay)
        if random.random() < 0.05: self.hunger = min(100, self.hunger + 1)
        if random.random() < 0.05: self.energy = max(0, self.energy - 1)
        
        # 2. Action Timer (Performing an action)
        if self.action_timer > 0:
            self.action_timer -= 1
            if self.action_timer <= 0:
                # Effects based on the action that just finished
                if self.current_action == "EAT": self.hunger = 0
                if self.current_action == "SLEEP": self.energy = 100
                
                # Action Finished
                self.current_action = "Idle"
                self.decision_timer = 0 # Think immediately
                self.save_memory() # Save after action
            return # Don't move while acting

        # 3. Movement
        # 3. Movement
        dx = self.target_x - self.x
        dy = self.target_y - self.y
        dist = math.hypot(dx, dy)
        
        # Separation Force (Prevent Stacking)
        sep_x, sep_y = 0, 0
        for agent in self.world.agents:
            if agent != self:
                diff_x = self.x - agent.x
                diff_y = self.y - agent.y
                d = math.hypot(diff_x, diff_y)
                if d < 60 and d > 0: # Too close!
                    # Repel
                    force = (60 - d) / 60.0 * 2.0 # Strength
                    sep_x += (diff_x / d) * force
                    sep_y += (diff_y / d) * force
        
        if dist > self.speed:
            move_x = (dx / dist) * self.speed
            move_y = (dy / dist) * self.speed
            
            # Apply Separation
            self.x += move_x + sep_x
            self.y += move_y + sep_y
            self.current_action = "Moving"
        else:
            # Even if arrived, keep separating
            if abs(sep_x) > 0.1 or abs(sep_y) > 0.1:
                self.x += sep_x
                self.y += sep_y
                
            if self.current_action == "Moving":
                 # Arrived
                self.current_action = "Idle"
                self.x = self.target_x
                self.y = self.target_y
                self.decision_timer = 0 # Think immediately upon arrival
                self.save_memory() # Save after moving

        # 4. Brain Tick
        # Check for stagnation/boredom
        if self.current_action in ["Idle", "WORK", "WAIT", "EAT", "SLEEP"]:
             self.time_stationary += 1
        else:
             self.time_stationary = 0
             
        # Forced Wander (If stuck for > 15 seconds / 900 frames)
        if self.time_stationary > 900:
             self.force_wander()
             return

        if self.decision_timer > 0:
            self.decision_timer -= 1
        elif self.current_action == "Idle":
            self.make_decision_async()
            self.decision_timer = self.decision_interval + random.randint(0, 30)
            self.save_memory() # Periodic save

    def force_wander(self):
        """Force the agent to move to a new random location."""
        self.thought = "I'm bored here. I need to move!"
        self.current_action = "Moving"
        self.time_stationary = 0
        
        # Pick a random location that is NOT the current one
        current_loc = self.get_nearest_location_name()
        choices = list(self.world.locations.keys())
        if current_loc in choices and len(choices) > 1:
            choices.remove(current_loc)
            
        target_name = random.choice(choices)
        loc = self.world.locations.get(target_name)
        
        # Dispersal
        offset_x = random.randint(-80, 80)
        offset_y = random.randint(30, 100)
        self.target_x = loc.x + offset_x
        self.target_y = loc.y + offset_y
        
        print(f"[{self.name}] Forced wander to {target_name}")

    def make_decision_async(self):
        t = threading.Thread(target=self.make_decision)
        t.start()

    def make_decision(self):
        # Prepare Prompt
        loc_name = self.get_nearest_location_name()
        
        
        # Personality Bias
        preferred = []
        if "Research" in self.personality: preferred = ["Research Lab", "Library"]
        if "Game" in self.personality: preferred = ["Game Studio", "Arcade"]
        if "Data" in self.personality: preferred = ["Data Center"]
        
        # Dynamic Prompt Injection
        sys_prompt = RESIDENT_SYSTEM_PROMPT.format(
            locations=", ".join(self.world.locations.keys()),
            hunger=self.hunger,
            energy=self.energy,
            current_loc_name=loc_name
        ) + f"\n[Preferences] You heavily prefer these locations: {', '.join(preferred)}. Go there if you are not hungry/tired."
        
        # We need to update the agent's sys_prompt potentially? 
        # ReActAgent stores it. Let's just send it in the user msg as context for now to be safe/stateless
        # Or ideally create a new agent or update its internal memory. 
        # For this loop, just appending State to context is enough if the system prompt is static.
        # But we formatted the system prompt in __init__. Let's re-format it here?
        # Actually ReActAgent doesn't expose easy sys_prompt update.
        # Let's put the State in the User Message.
        
        msg_content = f"""
        [Current State]
        Hunger: {self.hunger}/100
        Energy: {self.energy}/100
        Location: {loc_name}
        Surroundings: {", ".join(self.world.locations.keys())}
        Preferences: {", ".join(preferred)}
        
        What is your next move? Output JSON.
        """
        
        msg = Msg(name="System", content=msg_content, role="user")
        
        try:
            self.current_action = "Thinking..."
            import asyncio
            res = asyncio.run(self.brain(msg))
            
            content = res.content
            if isinstance(content, list):
                text_content = ""
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text_content += block.get("text", "")
                    elif isinstance(block, str):
                         text_content += block
                content = text_content
            
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]
            
            cmd = json.loads(content)
            self.thought = cmd.get("thought", "...")
            
            action = cmd.get("action")
            target = cmd.get("target")
            duration = cmd.get("duration", 60)
            
            if action == "MOVE":
                loc = self.world.locations.get(target)
                if loc:
                    # Increased dispersal radius (was 30, now 80)
                    offset_x = random.randint(-80, 80)
                    offset_y = random.randint(30, 100)
                    self.target_x = loc.x + offset_x
                    self.target_y = loc.y + offset_y
                    self.current_action = "Moving"
            
            elif action in ["EAT", "SLEEP", "WORK"]:
                self.current_action = action
                self.action_timer = int(duration)
            
            elif action == "WAIT":
                self.current_action = "Idle"
                self.energy += 2
                
        except Exception as e:
            print(f"{self.name} error: {e}")
            self.thought = "..."
            self.current_action = "Idle"
