# -*- coding: utf-8 -*-
"""Neo-Modern Asset generation for Gomoku."""
import pygame
import random

def create_modern_board(width, height):
    """Generates a Dark Modern / Sci-Fi background."""
    surface = pygame.Surface((width, height))
    # Deep Dark Blue/Slate background
    surface.fill((20, 25, 35))
    
    # Subtle digital noise or grid texture
    for _ in range(100):
        rect = (random.randint(0, width), random.randint(0, height), random.randint(2, 5), random.randint(2, 5))
        color = (30, 40, 50)
        pygame.draw.rect(surface, color, rect)
        
    return surface

def create_neon_stone(radius, color_type):
    """Generates a glowing neon stone."""
    size = radius * 2
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    center = (radius, radius)
    
    if color_type == 'black':
        # Player: Cyan/Blue Neon
        core_color = (0, 255, 255) 
        glow_color = (0, 200, 255, 100)
        outer_glow = (0, 100, 200, 30)
    else: 
        # AI: Magenta/Pink Neon
        core_color = (255, 0, 255)
        glow_color = (255, 0, 200, 100)
        outer_glow = (200, 0, 100, 30)

    # Layered drawing for Glow Effect
    # 1. Outer faint glow
    pygame.draw.circle(surface, outer_glow, center, radius)
    # 2. Inner strong glow
    pygame.draw.circle(surface, glow_color, center, int(radius * 0.8))
    # 3. Bright Core
    pygame.draw.circle(surface, core_color, center, int(radius * 0.5))
    # 4. White hot center
    pygame.draw.circle(surface, (255, 255, 255), center, int(radius * 0.2))
    
    return surface

def draw_neon_grid(surface, width, height, margin, cell_size, grid_size):
    """Draws the grid lines with a neon glow."""
    line_color = (0, 100, 100) # Dark Teal
    bright_line = (50, 150, 150)
    
    for i in range(grid_size):
        # Coordinates
        x = margin + i * cell_size
        y = margin + i * cell_size
        
        # Horizontal
        start_h = (margin, y)
        end_h = (width - margin, y)
        pygame.draw.line(surface, line_color, start_h, end_h, 3) # Glow
        pygame.draw.line(surface, bright_line, start_h, end_h, 1) # Core
        
        # Vertical
        start_v = (x, margin)
        end_v = (x, height - margin)
        pygame.draw.line(surface, line_color, start_v, end_v, 3)
        pygame.draw.line(surface, bright_line, start_v, end_v, 1)
        
    # Draw Dot Points (Tengen and stars)
    points = [3, 7, 11]
    for r in points:
        for c in points:
            cx = margin + c * cell_size
            cy = margin + r * cell_size
            pygame.draw.circle(surface, bright_line, (cx, cy), 5)
