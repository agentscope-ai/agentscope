# -*- coding: utf-8 -*-
"""UI Components for Game Center."""
import pygame
import time

class InputBox:
    def __init__(self, x, y, w, h, font, text=''):
        self.rect = pygame.Rect(x, y, w, h)
        self.color_inactive = pygame.Color('lightskyblue3')
        self.color_active = pygame.Color('dodgerblue2')
        self.color = self.color_inactive
        self.text = text
        self.font = font
        self.render_text()
        self.active = False
        self.cursor_visible = True
        self.last_blink = time.time()

    def render_text(self):
        self.txt_surface = self.font.render(self.text, True, self.color)

    def handle_event(self, event):
        if event.type == pygame.MOUSEBUTTONDOWN:
            if self.rect.collidepoint(event.pos):
                self.active = not self.active
            else:
                self.active = False
            
            if self.active:
                pygame.key.start_text_input()
                pygame.key.set_text_input_rect(self.rect)
            else:
                pygame.key.stop_text_input()
                
            self.color = self.color_active if self.active else self.color_inactive
            self.render_text()
            
        if event.type == pygame.TEXTINPUT and self.active:
            self.text += event.text
            self.render_text()

        if event.type == pygame.KEYDOWN:
            if self.active:
                if event.key == pygame.K_RETURN:
                    return self.text # Trigger submit
                elif event.key == pygame.K_BACKSPACE:
                    self.text = self.text[:-1]
                # For non-IME input (standard English), we still use keydown, 
                # but TEXTINPUT handles unicode better. 
                # Careful not to double add. TEXTINPUT handles standard keys too in newer pygame.
                # If using TEXTINPUT, we usually ignore unicode from KEYDOWN for printable chars.
                # But simple InputBox often mixes them. 
                # Safe approach: If event has unicode and it's not handled by TEXTINPUT...
                # Actually, standard way: Use TEXTINPUT for text, KEYDOWN for control (Enter, Backspace).
                pass
                self.render_text()
        return None

    def update(self):
        width = max(200, self.txt_surface.get_width()+10)
        self.rect.w = width
        
        # Blink cursor
        if time.time() - self.last_blink > 0.5:
            self.cursor_visible = not self.cursor_visible
            self.last_blink = time.time()

    def draw(self, screen):
        # Draw background
        s = pygame.Surface((self.rect.w, self.rect.h))
        s.set_alpha(100) # Transparent bg
        s.fill((0, 0, 0))
        screen.blit(s, (self.rect.x, self.rect.y))
        
        # Draw text
        screen.blit(self.txt_surface, (self.rect.x+5, self.rect.y+5))
        
        # Draw Cursor
        if self.active and self.cursor_visible:
            cw = self.txt_surface.get_width()
            cx = self.rect.x + 5 + cw
            cy = self.rect.y + 5
            h = self.txt_surface.get_height()
            pygame.draw.line(screen, self.color, (cx, cy), (cx, cy+h), 2)
            
        # Draw border
        pygame.draw.rect(screen, self.color, self.rect, 2)

class GameCard:
    """A card representing a game in the carousel."""
    def __init__(self, x, y, w, h, title, filename, font):
        self.rect = pygame.Rect(x, y, w, h)
        self.title = title
        self.filename = filename
        self.font = font
        self.hovered = False
        
    def draw(self, screen):
        # Base
        color = (20, 30, 40)
        border_color = (0, 100, 100)
        
        if self.hovered:
            color = (30, 50, 70)
            border_color = (0, 255, 255) # Cyan Glow
            
        pygame.draw.rect(screen, color, self.rect, border_radius=10)
        pygame.draw.rect(screen, border_color, self.rect, 2, border_radius=10)
        
        # Title
        text = self.font.render(self.title, True, (255, 255, 255))
        text_rect = text.get_rect(center=self.rect.center)
        screen.blit(text, text_rect)
        
    def check_hover(self, pos):
        self.hovered = self.rect.collidepoint(pos)
        return self.hovered

    def check_click(self, pos):
        return self.rect.collidepoint(pos)
