import pygame
from .core.constants import Action

class InputHandler:
    def __init__(self):
        self.key_map = {
            pygame.K_a: Action.LEFT,
            pygame.K_d: Action.RIGHT,
            pygame.K_s: Action.DOWN,
            pygame.K_LEFT: Action.ROTATE_LEFT,
            pygame.K_RIGHT: Action.ROTATE_RIGHT,
            pygame.K_q: Action.QUIT,
            pygame.K_ESCAPE: Action.QUIT
        }

    def process_input(self):
        actions = []
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                actions.append(Action.QUIT)
            elif event.type == pygame.KEYDOWN:
                if event.key in self.key_map:
                    actions.append(self.key_map[event.key])
        return actions
