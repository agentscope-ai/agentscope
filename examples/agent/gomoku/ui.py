# -*- coding: utf-8 -*-
"""UI Components for Gomoku."""
import pygame

class Button:
    def __init__(self, x, y, width, height, text, font, action=None, bg_color=(100, 100, 100), hover_color=(150, 150, 150), text_color=(255, 255, 255)):
        self.rect = pygame.Rect(x, y, width, height)
        self.text = text
        self.font = font
        self.action = action
        self.bg_color = bg_color
        self.hover_color = hover_color
        self.text_color = text_color
        self.is_hovered = False

    def update(self, mouse_pos):
        self.is_hovered = self.rect.collidepoint(mouse_pos)

    def draw(self, surface):
        color = self.hover_color if self.is_hovered else self.bg_color
        
        # Draw shadow
        shadow_rect = self.rect.copy()
        shadow_rect.x += 3
        shadow_rect.y += 3
        pygame.draw.rect(surface, (0, 0, 0, 100), shadow_rect, border_radius=10)
        
        # Draw button
        pygame.draw.rect(surface, color, self.rect, border_radius=10)
        
        # Draw text
        text_surf = self.font.render(self.text, True, self.text_color)
        text_rect = text_surf.get_rect(center=self.rect.center)
        surface.blit(text_surf, text_rect)

    def check_click(self, mouse_pos):
        if self.rect.collidepoint(mouse_pos) and self.action:
            self.action()
            return True
        return False

def draw_text_with_shadow(surface, text, font, color, pos, shadow_offset=(2, 2), shadow_color=(0, 0, 0, 100)):
    shadow_surf = font.render(text, True, shadow_color)
    text_surf = font.render(text, True, color)
    
    rect = text_surf.get_rect(center=pos)
    shadow_rect = rect.copy()
    shadow_rect.x += shadow_offset[0]
    shadow_rect.y += shadow_offset[1]
    
    surface.blit(shadow_surf, shadow_rect)
    surface.blit(text_surf, rect)
