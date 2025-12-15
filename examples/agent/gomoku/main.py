# -*- coding: utf-8 -*-
"""Desktop Gomoku Game (Modern UI) using Pygame."""
import pygame
import sys
import os
import time
from enum import Enum
from agent import GomokuAgentWrapper
from ui import Button, draw_text_with_shadow
from assets import create_modern_board, create_neon_stone, draw_neon_grid

# Constants
WIDTH, HEIGHT = 900, 900
GRID_SIZE = 15
CELL_SIZE = 50
MARGIN = 75

class GameState(Enum):
    MENU = 1
    DIFFICULTY = 2
    PLAYING = 3
    GAMEOVER = 4

class GomokuGame:
    def __init__(self):
        pygame.init()
        self.screen = pygame.display.set_mode((WIDTH, HEIGHT))
        pygame.display.set_caption("Cyber-Gomoku ♟️")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("Segoe UI", 24)
        self.title_font = pygame.font.SysFont("Segoe UI", 64, bold=True)
        
        # API Key (for commentary if enabled)
        self.api_key = os.environ.get("DASHSCOPE_API_KEY") or "sk-33241ff45e3a454986732123b5e7214c"
        
        # Assets
        print("Generating Neon assets...")
        self.board_texture = create_modern_board(WIDTH, HEIGHT)
        self.black_stone = create_neon_stone(CELL_SIZE // 2 - 4, 'black') # Player
        self.white_stone = create_neon_stone(CELL_SIZE // 2 - 4, 'white') # AI
        
        # Pre-render grid on board texture
        draw_neon_grid(self.board_texture, WIDTH, HEIGHT, MARGIN, CELL_SIZE, GRID_SIZE)
        
        # Game Data
        self.state = GameState.MENU
        self.grid = []
        self.turn = 'X'
        self.ai = None
        self.winner = None
        self.game_mode = "classic"
        self.difficulty = "normal"
        self.stone_times = {}
        self.last_move = None
        
        # UI Elements
        self.init_ui()

    def init_ui(self):
        center_x = WIDTH // 2 - 100
        # Style constants
        btn_bg = (30, 40, 50)
        btn_hover = (50, 70, 90)
        btn_text = (0, 255, 255) # Cyan text
        
        # Menu Buttons
        self.btn_start = Button(center_x, 300, 200, 50, "START", self.font, 
                                bg_color=btn_bg, hover_color=btn_hover, text_color=btn_text,
                                action=lambda: self.set_state(GameState.DIFFICULTY))
        self.btn_flash = Button(center_x, 400, 200, 50, "FLASH MODE", self.font,
                                bg_color=btn_bg, hover_color=btn_hover, text_color=btn_text,
                                action=self.start_flash_mode)
        self.btn_exit = Button(center_x, 500, 200, 50, "EXIT", self.font,
                               bg_color=btn_bg, hover_color=btn_hover, text_color=btn_text,
                               action=self.quit_game)
        
        # Difficulty Buttons
        self.btn_easy = Button(center_x, 300, 200, 50, "EASY", self.font,
                               bg_color=btn_bg, hover_color=btn_hover, text_color=(0, 255, 0),
                               action=lambda: self.start_game("easy"))
        self.btn_normal = Button(center_x, 400, 200, 50, "NORMAL", self.font,
                                bg_color=btn_bg, hover_color=btn_hover, text_color=(255, 255, 0),
                                action=lambda: self.start_game("normal"))
        self.btn_hard = Button(center_x, 500, 200, 50, "HARD", self.font,
                               bg_color=btn_bg, hover_color=btn_hover, text_color=(255, 0, 0),
                               action=lambda: self.start_game("hard"))
        
        # Game Over Buttons
        self.btn_retry = Button(center_x, 600, 200, 50, "RETRY", self.font,
                                bg_color=btn_bg, hover_color=btn_hover, text_color=btn_text,
                                action=lambda: self.set_state(GameState.MENU))

    def set_state(self, new_state):
        self.state = new_state
        
    def start_flash_mode(self):
        self.game_mode = "flash"
        self.set_state(GameState.DIFFICULTY)
        
    def start_game(self, diff):
        self.difficulty = diff
        self.ai = GomokuAgentWrapper(self.api_key, difficulty=diff)
        self.grid = [['.' for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]
        self.turn = 'X'
        self.winner = None
        self.stone_times = {}
        self.last_move = None
        self.state = GameState.PLAYING

    def quit_game(self):
        pygame.quit()
        sys.exit()

    def run(self):
        while True:
            mouse_pos = pygame.mouse.get_pos()
            
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.quit_game()
                if event.type == pygame.MOUSEBUTTONDOWN:
                    self.handle_click(mouse_pos)
            
            if self.state == GameState.MENU:
                self.draw_menu(mouse_pos)
            elif self.state == GameState.DIFFICULTY:
                self.draw_difficulty(mouse_pos)
            elif self.state == GameState.PLAYING:
                self.draw_game()
                self.update_game_logic()
            elif self.state == GameState.GAMEOVER:
                self.draw_game_over(mouse_pos)
            
            pygame.display.flip()
            self.clock.tick(60) # Smooth 60 FPS

    def handle_click(self, pos):
        if self.state == GameState.MENU:
            for btn in [self.btn_start, self.btn_flash, self.btn_exit]:
                if btn.check_click(pos): break
        elif self.state == GameState.DIFFICULTY:
            for btn in [self.btn_easy, self.btn_normal, self.btn_hard]:
                if btn.check_click(pos): break
        elif self.state == GameState.GAMEOVER:
             if self.btn_retry.check_click(pos): pass
        elif self.state == GameState.PLAYING:
            if self.turn == 'X':
                mx, my = pos
                c = round((mx - MARGIN) / CELL_SIZE)
                r = round((my - MARGIN) / CELL_SIZE)
                self.place_stone(r, c, 'X')

    def place_stone(self, r, c, player):
        if 0 <= r < GRID_SIZE and 0 <= c < GRID_SIZE and self.grid[r][c] == '.':
            self.grid[r][c] = player
            self.last_move = (r, c)
            self.stone_times[(r, c)] = time.time()
            
            if self.check_win(r, c, player):
                self.winner = "Player" if player == 'X' else "AI"
                self.state = GameState.GAMEOVER
            else:
                self.turn = 'O' if player == 'X' else 'X'
            return True
        return False

    def update_game_logic(self):
        if self.turn == 'O':
            # Minimax is fast, but let's give it a tiny delay so it doesn't feel robotic (100ms)
            pygame.time.wait(200) 
            pygame.event.pump()
            
            r, c = self.ai.get_move(self.grid)
            self.place_stone(r, c, 'O')

    def check_win(self, r, c, player):
        directions = [(0, 1), (1, 0), (1, 1), (1, -1)]
        for dr, dc in directions:
            count = 1
            for i in range(1, 5):
                nr, nc = r + dr * i, c + dc * i
                if 0 <= nr < GRID_SIZE and 0 <= nc < GRID_SIZE and self.grid[nr][nc] == player:
                    count += 1
                else: break
            for i in range(1, 5):
                nr, nc = r - dr * i, c - dc * i
                if 0 <= nr < GRID_SIZE and 0 <= nc < GRID_SIZE and self.grid[nr][nc] == player:
                    count += 1
                else: break
            if count >= 5: return True
        return False

    def draw_menu(self, mouse_pos):
        self.screen.blit(self.board_texture, (0, 0))
        # Title Glow
        draw_text_with_shadow(self.screen, "CYBER GOMOKU", self.title_font, (0, 255, 255), (WIDTH//2, 180), shadow_color=(0, 100, 200))
        for btn in [self.btn_start, self.btn_flash, self.btn_exit]:
            btn.update(mouse_pos)
            btn.draw(self.screen)

    def draw_difficulty(self, mouse_pos):
        self.screen.blit(self.board_texture, (0, 0))
        draw_text_with_shadow(self.screen, "SELECT DIFFICULTY", self.title_font, (255, 255, 255), (WIDTH//2, 180))
        for btn in [self.btn_easy, self.btn_normal, self.btn_hard]:
            btn.update(mouse_pos)
            btn.draw(self.screen)

    def draw_game(self):
        self.screen.blit(self.board_texture, (0, 0))
        
        current_time = time.time()
        for r in range(GRID_SIZE):
            for c in range(GRID_SIZE):
                stone = self.grid[r][c]
                if stone != '.':
                    if self.game_mode == "flash":
                         age = current_time - self.stone_times.get((r, c), 0)
                         if age > 2.0: continue
                    
                    x = MARGIN + c * CELL_SIZE
                    y = MARGIN + r * CELL_SIZE
                    img = self.black_stone if stone == 'X' else self.white_stone
                    # Center the stone image
                    offset = img.get_width() // 2
                    self.screen.blit(img, (x - offset, y - offset))
        
        # Last Move Marker
        if self.last_move:
             r, c = self.last_move
             x = MARGIN + c * CELL_SIZE
             y = MARGIN + r * CELL_SIZE
             pygame.draw.circle(self.screen, (255, 0, 0), (x, y), 5)

        # Status
        status = "PLAYER TURN" if self.turn == 'X' else "AI THINKING"
        color = (0, 255, 255) if self.turn == 'X' else (255, 0, 255)
        draw_text_with_shadow(self.screen, status, self.font, color, (WIDTH//2, 30))

    def draw_game_over(self, mouse_pos):
        self.draw_game()
        s = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        s.fill((0, 0, 0, 200)) # Darker overlay
        self.screen.blit(s, (0, 0))
        
        color = (0, 255, 255) if self.winner == "Player" else (255, 0, 0)
        draw_text_with_shadow(self.screen, f"{self.winner.upper()} WINS!", self.title_font, color, (WIDTH//2, HEIGHT//2 - 50))
        
        self.btn_retry.update(mouse_pos)
        self.btn_retry.draw(self.screen)

if __name__ == "__main__":
    game = GomokuGame()
    game.run()
