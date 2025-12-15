# -*- coding: utf-8 -*-
"""AgentScope Arcade: Game Center Launcher."""
import pygame
import sys
import os
import glob
import subprocess
import threading
import time
from ui_components import InputBox, GameCard
from particles import ParticleSystem
from creator import GameCreatorAI

# Constants
WIDTH, HEIGHT = 900, 600
BG_COLOR = (10, 15, 20)

class Launcher:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("AgentScope Arcade 👾")
        self.clock = pygame.time.Clock()
        
        # Font Setup (Support Chinese with fallback)
        font_name = "Microsoft YaHei" 
        try:
             if not pygame.font.match_font(font_name):
                 font_name = "SimHei" 
                 if not pygame.font.match_font(font_name):
                     font_name = None 
        except:
             font_name = None
             
        self.font = pygame.font.SysFont(font_name, 24)
        self.small_font = pygame.font.SysFont(font_name, 18)
        self.title_font = pygame.font.SysFont(font_name, 48, bold=True)
        
        # Components
        # Components
        # Use DEEPSEEK_API_KEY as primary
        self.api_key = os.environ.get("DEEPSEEK_API_KEY") 
        if not self.api_key:
             print("⚠️ Warning: DEEPSEEK_API_KEY not found in environment.")
             self.api_key = "YOUR_API_KEY_HERE"
        
        self.creator = GameCreatorAI(self.api_key)
        self.particles = ParticleSystem()
        
        # State
        self.games = []
        self.load_games()
        self.creating = False
        self.active_tasks = [] # List of {"prompt": str, "status": str, "color": tuple}
        self.status_msg = "Ready to Play"
        
        # UI
        self.prompt_box = InputBox(WIDTH//2 - 200, HEIGHT//2, 400, 40, self.font)
        self.btn_create = pygame.Rect(WIDTH - 150, 50, 120, 40)
        
    def load_games(self):
        self.games = []
        # Find .py files in games/ subdirectory
        games_dir = os.path.join(os.path.dirname(__file__), 'games')
        if not os.path.exists(games_dir): os.makedirs(games_dir)
        
        files = glob.glob(os.path.join(games_dir, "*.py"))
        for i, f in enumerate(files):
            name = os.path.basename(f).replace(".py", "")
            if name == "__init__": continue
            # Layout cards in a grid or row
            x = 50 + (i % 3) * 220
            y = 150 + (i // 3) * 150
            self.games.append(GameCard(x, y, 200, 120, name.title(), f, self.font))

    def run(self):
        while True:
            dt = self.clock.tick(60) / 1000.0
            mouse_pos = pygame.mouse.get_pos()
            
            # Events
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    pygame.quit()
                    sys.exit()
                
                if self.creating:
                    res = self.prompt_box.handle_event(event)
                    if res: # Submitted
                        self.start_generation(res)
                
                # Check clicks if NOT creating (or clicking outside box?)
                # If creating, we might want to allow canceling by clicking outside
                if event.type == pygame.MOUSEBUTTONDOWN:
                    if self.creating:
                         # Click create button again to toggle off? or click X
                         if self.btn_create.collidepoint(event.pos):
                             self.creating = False
                             pygame.key.stop_text_input()
                    else:
                        if self.btn_create.collidepoint(event.pos):
                            self.creating = True
                            self.status_msg = "Describe your game..."
                            # Auto-focus and clear
                            self.prompt_box.text = ''
                            self.prompt_box.active = True
                            self.prompt_box.render_text() 
                            pygame.key.start_text_input() 
                            pygame.key.set_text_input_rect(self.prompt_box.rect)
                        
                        for game in self.games:
                            if game.check_click(event.pos):
                                self.launch_game(game.filename)
            
            # Update
            self.particles.emit_trail(mouse_pos[0], mouse_pos[1])
            self.particles.update(dt)
            if self.creating: self.prompt_box.update()
            
            # Draw
            self.draw()
            pygame.display.flip()

    def start_generation(self, prompt):
        # Add to tasks
        task = {
            "prompt": prompt, 
            "status": "Initializing...", 
            "color": (255, 255, 0),
            "progress": 0.0
        }
        self.active_tasks.append(task)
        
        self.creating = False # Close UI immediately
        pygame.key.stop_text_input()
        self.status_msg = f"Building '{prompt}'..."
        
        # Run in thread
        t = threading.Thread(target=self._generate_thread, args=(task,))
        t.start()

    def _generate_thread(self, task):
        try:
            prompt = task["prompt"]
            
            # We can't easily get fine-grained progress from creator.create_game unless we modify it 
            # to accept a callback or check shared state. 
            # For now, we simulate steps or update blindly.
            task["status"] = "Architecting..."
            
            # To show real progress, we'd need to modify GameCreatorAI to accept a callback.
            # But let's just do the call and update status afterwards for V1
            # Or better: Wrapper logic here.
            
            # Let's assume create_game takes a bit
            result = self.creator.create_game(prompt)
            
            if result.get("error"):
                task["status"] = f"Error: {result['error']}"
                task["color"] = (255, 0, 0)
                # Keep error visible for a while then remove?
                time.sleep(5)
                if task in self.active_tasks: self.active_tasks.remove(task)
            else:
                self.save_game(result["name"], result["code"])
                task["status"] = "Done!"
                task["color"] = (0, 255, 0)
                self.load_games() # Refresh list
                time.sleep(3)
                if task in self.active_tasks: self.active_tasks.remove(task)
                
        except Exception as e:
            task["status"] = "Crash!"
            print(e)
            time.sleep(5)
            if task in self.active_tasks: self.active_tasks.remove(task)

    def save_game(self, name, code):
        games_dir = os.path.join(os.path.dirname(__file__), 'games')
        path = os.path.join(games_dir, f"{name}.py")
        with open(path, "w", encoding='utf-8') as f:
            f.write(code)

    def launch_game(self, filepath):
        self.status_msg = f"Launching {os.path.basename(filepath)}..."
        subprocess.Popen([sys.executable, filepath])

    def draw(self):
        self.screen.fill(BG_COLOR)
        
        # Draw Particles
        self.particles.draw(self.screen)
        
        # Title
        t = self.title_font.render("GAME CENTER", True, (255, 255, 255))
        self.screen.blit(t, (50, 40))
        
        # Status
        s = self.font.render(self.status_msg, True, (0, 255, 200))
        self.screen.blit(s, (50, 100))
        
        # Create Button
        color = (0, 100, 0) if not self.creating else (50, 50, 50)
        pygame.draw.rect(self.screen, color, self.btn_create, border_radius=5)
        btn_txt = self.font.render("NEW +", True, (255, 255, 255))
        self.screen.blit(btn_txt, (self.btn_create.x + 25, self.btn_create.y + 5))
        
        # Task Queue (Bottom Right)
        if self.active_tasks:
            curr_y = HEIGHT - 20 - (len(self.active_tasks) * 30)
            for task in self.active_tasks:
                # Draw small status bar
                msg = f"{task['prompt'][:15]}..: {task['status']}"
                ts = self.small_font.render(msg, True, task['color'])
                self.screen.blit(ts, (WIDTH - 250, curr_y))
                
                # Spinning indicator or loading bar could go here
                curr_y += 30
        
        # Content
        if self.creating:
            # Overlay
            overlay = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            overlay.fill((0,0,0, 200)) # Darken
            self.screen.blit(overlay, (0,0))
            
            txt = self.font.render("Describe Game Idea:", True, (255, 255, 255))
            self.screen.blit(txt, (WIDTH//2 - 200, HEIGHT//2 - 40))
            self.prompt_box.draw(self.screen)
            
            # Hint to close
            close_hint = self.small_font.render("(Click NEW+ again to cancel)", True, (150, 150, 150))
            self.screen.blit(close_hint, (WIDTH//2 - 80, HEIGHT//2 + 50))
            
        else:
            # Game Grid
            mouse = pygame.mouse.get_pos()
            for game in self.games:
                game.check_hover(mouse)
                game.draw(self.screen)


if __name__ == "__main__":
    app = Launcher()
    app.run()
