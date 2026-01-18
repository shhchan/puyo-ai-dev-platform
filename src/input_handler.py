import pygame
from .core.constants import Action

class InputHandler:
    def __init__(self):
        self.key_map = {
            pygame.K_LEFT: Action.LEFT,
            pygame.K_RIGHT: Action.RIGHT,
            pygame.K_DOWN: Action.DOWN,
            pygame.K_z: Action.ROTATE_LEFT,
            pygame.K_x: Action.ROTATE_RIGHT,
            pygame.K_UP: Action.ROTATE_RIGHT, # Alternative
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
