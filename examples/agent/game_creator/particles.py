# -*- coding: utf-8 -*-
"""Advanced Particle System for Game Center (Neon/Glow Effects)."""
import pygame
import random
import math

class Particle:
    def __init__(self, x, y, velocity, color, size, life, decay=0.1, gravity=0):
        self.x = x
        self.y = y
        self.vx, self.vy = velocity
        self.color = list(color)
        self.size = size
        self.life = life
        self.max_life = life
        self.decay = decay
        self.gravity = gravity

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += self.gravity * dt
        self.life -= self.decay * dt
        self.size -= (self.decay * 3) * dt # Shrink over time
        if self.size < 0: self.size = 0

    def draw(self, surface):
        if self.life > 0 and self.size > 0:
            # Alpha based on life
            alpha = int((self.life / self.max_life) * 255)
            if alpha < 0: alpha = 0
            
            # Create a surface for the particle (to support per-pixel alpha if needed)
            # But for speed, direct circle with size
            
            # Simple soft circle
            s = pygame.Surface((int(self.size*2), int(self.size*2)), pygame.SRCALPHA)
            pygame.draw.circle(s, (*self.color, alpha), (int(self.size), int(self.size)), int(self.size))
            surface.blit(s, (int(self.x - self.size), int(self.y - self.size)))

class ParticleSystem:
    def __init__(self):
        self.particles = []

    def emit(self, x, y, count=1, color=(0, 255, 255), speed=2, size=5, life=1.0):
        for _ in range(count):
            angle = random.uniform(0, math.pi * 2)
            v_speed = random.uniform(speed * 0.5, speed * 1.5)
            vx = math.cos(angle) * v_speed
            vy = math.sin(angle) * v_speed
            
            p = Particle(x, y, (vx, vy), color, size, life, decay=random.uniform(0.5, 1.0))
            self.particles.append(p)

    def emit_trail(self, x, y, color=(0, 255, 255)):
        # Mouse trail effect - fewer particles, faster fade
        vx = random.uniform(-0.5, 0.5)
        vy = random.uniform(-0.5, 0.5)
        p = Particle(x, y, (vx, vy), color, size=random.uniform(2, 5), life=0.5, decay=1.5)
        self.particles.append(p)

    def update(self, dt):
        # Update details
        for p in self.particles:
            p.update(dt)
        # Remove dead particles
        self.particles = [p for p in self.particles if p.life > 0 and p.size > 0]

    def draw(self, surface):
        # To achieve "Neon Additive Blending", we draw particles to a black surface 
        # then BLIT it with BLEND_ADD to the main screen.
        
        # Create a temp surface matching screen size (or optimize to visible area)
        # For full screen glow, this is heavy. 
        # Optimization: Just draw normally for now, Pygame BLEND_ADD is per-blit.
        
        for p in self.particles:
            # We can't do true addictive easily without a separate buffer in Pygame 
            # if we warn performacne. 
            # Let's try direct draw with alpha.
            p.draw(surface)
            
        # Note: True "Glow" usually requires blitting a blurry texture with BLEND_ADD.
        # We simulate this by drawing soft circles in the Particle class.
