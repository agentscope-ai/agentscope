# -*- coding: utf-8 -*-
"""Tiny Town Main Entry Point (Beautified)"""
import sys
import os
import pygame
import threading
import math
from world import TinyWorld
from resident_agent import Resident
from agentscope.model import OpenAIChatModel

# Constants
WIDTH, HEIGHT = 1200, 800
BG_COLOR = (30, 30, 30)
ASSET_DIR = os.path.join(os.path.dirname(__file__), "assets")


def remove_white_halo(image, threshold=220):
    """Aggressively remove white/near-white background pixels."""
    image = image.convert_alpha()
    width, height = image.get_size()
    for x in range(width):
        for y in range(height):
            # Get RGBA
            r, g, b, a = image.get_at((x, y))
            # Check if pixel is "bright enough" to be considered background/halo
            if r > threshold and g > threshold and b > threshold:
                image.set_at((x, y), (0, 0, 0, 0)) # Full transparency
    return image

def load_and_scale(filename, size=None):
    try:
        path = os.path.join(ASSET_DIR, filename)
        img = pygame.image.load(path)
        # Process white background removal pixel-perfectly
        img = remove_white_halo(img, threshold=200) # Threshold 200 handles anti-aliasing
        
        if size:
            img = pygame.transform.scale(img, size)
        return img
    except Exception as e:
        print(f"Failed to load {filename}: {e}")
        # Fallback surface
        surf = pygame.Surface(size or (64, 64))
        surf.fill((255, 0, 255))
        return surf

class Renderer:
    def __init__(self, screen):
        self.screen = screen
        self.font = pygame.font.SysFont("Microsoft YaHei", 14) # Better font
        self.title_font = pygame.font.SysFont("Microsoft YaHei", 18, bold=True)
        
        # Load Assets
        self.grass_tile = load_and_scale("terraria_grass.png", (64, 64)) 
        self.road_tile = load_and_scale("terraria_road.png", (64, 64))
        
        # Architecture Sprites
        # HQ: 120x120
        self.hq_img = load_and_scale("hub_hq.png", (128, 128))
        self.lab_img = load_and_scale("research_lab.png", (100, 100))
        self.studio_img = load_and_scale("game_studio.png", (100, 100))
        self.data_img = load_and_scale("data_center.png", (100, 100))
        
        # Amenities
        self.shop_img = load_and_scale("shop_sprite.png", (80, 80)) # Use for Cafeteria
        self.house_img = load_and_scale("house_sprite.png", (80, 80)) # Dorms
        
        # Status Icons (Load Sheet and Slice)
        try:
            sheet_path = os.path.join(ASSET_DIR, "status_icons.png")
            sheet = pygame.image.load(sheet_path).convert_alpha()
            # Dimensions: 4 icons, assumed 32x32 or equal width
            # Actually generated might be larger. Let's assume 4 equal chunks horizontally
            w = sheet.get_width() // 4
            h = sheet.get_height()
            
            self.icons = {
                "Thinking": sheet.subsurface((0, 0, w, h)),
                "EAT": sheet.subsurface((w, 0, w, h)),
                "WORK": sheet.subsurface((w*2, 0, w, h)),
                "SLEEP": sheet.subsurface((w*3, 0, w, h)),
                "Moving": None # No icon or use boots if generated
            }
            # Scale to reasonable size for head
            for k, v in self.icons.items():
                if v: self.icons[k] = pygame.transform.scale(v, (32, 32))
                
        except Exception as e:
            print(f"Failed to load icons: {e}")
            self.icons = {}

        # Agent Sprites
        self.agent_sprites = {
            "Alice": load_and_scale("alice.png", (64, 64)),
            "Bob": load_and_scale("bob.png", (64, 64)),
            "Charlie": load_and_scale("charlie.png", (64, 64))
        }
        self.default_agent_img = load_and_scale("villager_sprite.png", (48, 48))

    def draw_world(self, world):
        # 1. Background (Grid Based Map)
        tile_size = 64
        cols = WIDTH // tile_size + 1
        rows = HEIGHT // tile_size + 1
        
        # Define simplistic road logic: 
        # Main vertical avenue at x=600 (center)
        # Horizontal streets at y=200, 400, 600
        
        for r in range(rows):
            for c in range(cols):
                x = c * tile_size
                y = r * tile_size
                
                # Default grass
                tile = self.grass_tile
                
                # Procedural Roads
                # Vertical Main Street
                if 550 < x < 650:
                    tile = self.road_tile
                    
                # Horizontal Streets connecting key areas
                if 150 < y < 250 or 550 < y < 650:
                    tile = self.road_tile
                    
                # Render tile
                self.screen.blit(tile, (x, y))

        # 2. Locations
        for loc in world.locations.values():
            # Decide sprite based on name
            # Decide sprite based on name
            if "HQ" in loc.name: sprite = self.hq_img
            elif "Research" in loc.name: sprite = self.lab_img
            elif "Game" in loc.name: sprite = self.studio_img
            elif "Data" in loc.name: sprite = self.data_img
            elif "Cafeteria" in loc.name: sprite = self.shop_img
            elif "Home" in loc.name: sprite = self.house_img
            else: sprite = self.house_img
                
            # Center sprite on logic position
            rect = sprite.get_rect(center=(loc.x, loc.y))
            self.screen.blit(sprite, rect)
            
            # Draw Name Label
            self.draw_label(loc.name, loc.x, loc.y - 50, color=(255, 255, 255), icon=True)

        # 3. Agents
        for agent in world.agents:
            # Tint the villager sprite? 
            # Simple way: Multiply blend mode or just draw a colored circle under it
            # Let's draw the sprite.
            
            # To tint properly in Pygame without heavy per-frame ops:
            # We just draw the sprite as is for now. Or create cached tinted versions.
            # For this demo, let's just draw the unique sprite.
            # Be "Amazing" -> Add a small colored aura/shadow
            
            # Shadow
            pygame.draw.ellipse(self.screen, (0,0,0, 100), (agent.x - 15, agent.y + 15, 30, 10))
            
            # Sprite
            sprite = self.agent_sprites.get(agent.name, self.default_agent_img)
            rect = sprite.get_rect(center=(int(agent.x), int(agent.y)))
            self.screen.blit(sprite, rect)
            
            # Name Label (with background for readability)
            self.draw_label(agent.name, agent.x, agent.y - 40, color=agent.color)
            
            # Thought Bubble
            if agent.thought:
                self.draw_bubble(agent.thought, agent.x, agent.y - 60)
            
            # Status Icon (Enterprise Feature)
            self.draw_status_icon(agent, agent.x, agent.y)

        # 4. HUD
        time_grad = int((world.time_of_day / 24.0) * 255)
        # Night overlay?
        if world.time_of_day < 6 or world.time_of_day > 20:
            overlay = pygame.Surface((WIDTH, HEIGHT))
            overlay.set_alpha(100) # Darkness
            overlay.fill((0, 0, 50))
            self.screen.blit(overlay, (0,0))

        time_str = f"Time: {world.time_of_day:.1f}H"
        t_surf = self.title_font.render(time_str, True, (255, 255, 255))
        pygame.draw.rect(self.screen, (0,0,0,150), (10, 10, t_surf.get_width()+10, 30), border_radius=5)
        self.screen.blit(t_surf, (15, 15))

    def draw_label(self, text, x, y, color=(255,255,255), icon=False):
        surf = self.font.render(text, True, (255, 255, 255))
        bg_rect = surf.get_rect(center=(x, y)).inflate(10, 4)
        pygame.draw.rect(self.screen, (0, 0, 0, 150), bg_rect, border_radius=4)
        if icon: # Border matches type color
             pygame.draw.rect(self.screen, color, bg_rect, width=1, border_radius=4)
        self.screen.blit(surf, surf.get_rect(center=(x, y)))

    def draw_status_icon(self, agent, x, y):
        """Draws a status icon above the agent based on current_action."""
        icon_img = None
        if "Thinking" in agent.current_action: icon_img = self.icons.get("Thinking")
        elif "Moving" in agent.current_action: icon_img = self.icons.get("Moving")
        elif "EAT" in agent.current_action: icon_img = self.icons.get("EAT")
        elif "SLEEP" in agent.current_action: icon_img = self.icons.get("SLEEP")
        elif "WORK" in agent.current_action: icon_img = self.icons.get("WORK")
        
        if icon_img:
            # Bounce animation
            bounce = int(math.sin(pygame.time.get_ticks() / 200) * 3)
            # Draw shadow
            # shadow_mask = pygame.mask.from_surface(icon_img).to_surface(setcolor=(0,0,0,100), unsetcolor=(0,0,0,0)) 
            # Simple shadow: just black rect or circle? No, let's just draw icon.
            # actually drawing shadow for sprite is expensive if masking per frame.
            # Just draw the sprite bouncing.
            # Move to side (right shoulder) to avoid overlap with bubble
            rect = icon_img.get_rect(center=(x + 35, y - 40 + bounce))
            self.screen.blit(icon_img, rect)

    def draw_bubble(self, text, x, y):
        # Truncate
        if len(text) > 30: text = text[:28] + ".."
        surf = self.font.render(text, True, (0, 0, 0))
        padding = 8
        bg_rect = surf.get_rect(bottomleft=(x + 10, y)).inflate(padding*2, padding*2)
        
        # Balloon shape
        pygame.draw.rect(self.screen, (255, 255, 255), bg_rect, border_radius=8)
        pygame.draw.polygon(self.screen, (255, 255, 255), [(x, y), (x+15, y), (x+15, y+10)]) # Tail
        self.screen.blit(surf, surf.get_rect(center=bg_rect.center))


def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Tiny Town (Beautified) ✨")
    clock = pygame.time.Clock()
    
    renderer = Renderer(screen)
    
    # Init AI Model
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("DASHSCOPE_API_KEY")
    if not api_key:
        print("⚠️  Warning: DEEPSEEK_API_KEY not found in environment.")
        # Fallback or Exit? Tiny Town needs it for agents.
        # We'll allow empty to crash gracefully later or prompt user
        print("Please set DEEPSEEK_API_KEY to run the agent simulation.")
    model = OpenAIChatModel(
        model_name="deepseek-chat",
        api_key=api_key,
        client_kwargs={"base_url": "https://api.deepseek.com"}
    )
    
    # Init World
    world = TinyWorld(WIDTH, HEIGHT)
    
    # Create Residents (using world's random color generation or explicit)
    # Create Staff (AgentScope Team)
    alice = Resident("Alice", "Smart AI Researcher. Focuses on Deep Research.", world, model, (255, 100, 100))
    bob = Resident("Bob", "Game Developer. Loves coding and arcade games.", world, model, (100, 100, 255))
    charlie = Resident("Charlie", "Data Analyst. Obsessed with SQL and Big Data.", world, model, (100, 255, 100))
    
    world.agents = [alice, bob, charlie]
    
    print("Tiny Town Started! Press Ctrl+C to exit.")
    
    print("Tiny Town Started! Press Ctrl+C to exit.")
    
    selected_agent = None
    running = True
    while running:
        dt = clock.tick(60)
        
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1: # Left Click
                    mx, my = pygame.mouse.get_pos()
                    clicked = False
                    for agent in world.agents:
                         # Simple distance check for click
                         if math.hypot(agent.x - mx, agent.y - my) < 40:
                             selected_agent = agent
                             clicked = True
                             break
                    if not clicked:
                        selected_agent = None

        world.update()
        for agent in world.agents:
             agent.update()

        renderer.draw_world(world)
        
        # Draw Inspector Overlay
        if selected_agent:
            # Dim background
            s = pygame.Surface((WIDTH, HEIGHT))
            s.set_alpha(100)
            s.fill((0,0,0))
            screen.blit(s, (0,0))
            
            # Panel
            panel_rect = pygame.Rect(WIDTH//2 - 200, HEIGHT//2 - 150, 400, 300)
            pygame.draw.rect(screen, (40, 40, 50), panel_rect, border_radius=12)
            pygame.draw.rect(screen, (255, 255, 255), panel_rect, width=2, border_radius=12)
            
            # Content
            font_lg = pygame.font.SysFont("Microsoft YaHei", 24, bold=True)
            font_md = pygame.font.SysFont("Microsoft YaHei", 18)
            font_sm = pygame.font.SysFont("Microsoft YaHei", 14)
            
            # Head
            name_surf = font_lg.render(f"🕵️ {selected_agent.name}", True, (255, 200, 100))
            screen.blit(name_surf, (panel_rect.x + 20, panel_rect.y + 20))
            
            role_surf = font_sm.render(selected_agent.personality[:45] + "...", True, (200, 200, 200))
            screen.blit(role_surf, (panel_rect.x + 20, panel_rect.y + 55))
            
            # Stats (Graphical Bars)
            # Hunger
            h_surf = font_md.render("Hunger:", True, (255, 255, 255))
            screen.blit(h_surf, (panel_rect.x + 20, panel_rect.y + 90))
            pygame.draw.rect(screen, (100, 100, 100), (panel_rect.x + 100, panel_rect.y + 95, 200, 15))
            pygame.draw.rect(screen, (255, 100, 100), (panel_rect.x + 100, panel_rect.y + 95, 2 * selected_agent.hunger, 15))
            
            # Energy
            e_surf = font_md.render("Energy:", True, (255, 255, 255))
            screen.blit(e_surf, (panel_rect.x + 20, panel_rect.y + 120))
            pygame.draw.rect(screen, (100, 100, 100), (panel_rect.x + 100, panel_rect.y + 125, 200, 15))
            pygame.draw.rect(screen, (100, 255, 100), (panel_rect.x + 100, panel_rect.y + 125, 2 * selected_agent.energy, 15))
            
            # Thought Trace (The "Brain")
            t_surf = font_md.render("🧠 Current Thought Process:", True, (100, 200, 255))
            screen.blit(t_surf, (panel_rect.x + 20, panel_rect.y + 160))
            
            thought_text = selected_agent.thought
            # Multi-line wrap simple
            words = thought_text.split()
            lines = []
            curr_line = ""
            for w in words:
                if len(curr_line) + len(w) < 45:
                    curr_line += w + " "
                else:
                    lines.append(curr_line)
                    curr_line = w + " "
            lines.append(curr_line)
            
            for i, line in enumerate(lines[:4]): # Max 4 lines
                l_surf = font_sm.render(line, True, (220, 220, 220))
                screen.blit(l_surf, (panel_rect.x + 20, panel_rect.y + 190 + i*20))
            
            # Close hint
            hint_surf = font_sm.render("(Click anywhere else to close)", True, (150, 150, 150))
            screen.blit(hint_surf, (panel_rect.centerx - hint_surf.get_width()//2, panel_rect.bottom - 25))

        pygame.display.flip()

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
