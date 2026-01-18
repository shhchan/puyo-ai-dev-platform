import pygame
import sys
import time
from src.core.game import GameState
from src.core.constants import GRID_WIDTH, GRID_HEIGHT, PUYO_SIZE, Action, VISIBLE_HEIGHT
from src.ui.renderer import Renderer
from src.input_handler import InputHandler

SCREEN_WIDTH = 400
SCREEN_HEIGHT = 600
FPS = 60

def main():
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Puyo Base")
    clock = pygame.time.Clock()

    game_state = GameState()
    renderer = Renderer(screen)
    input_handler = InputHandler()

    last_gravity_time = time.time()
    gravity_interval = 1.0 # seconds
    
    last_anim_time = time.time()
    anim_interval = 0.3 # seconds for drop/vanish step

    running = True
    while running:
        current_time = time.time()
        
        # Input
        actions = input_handler.process_input()
        if Action.QUIT in actions:
            running = False
        
        # Update
        if game_state.state == "control":
            game_state.update(actions)
            
            # Gravity (Auto Drop)
            if current_time - last_gravity_time > gravity_interval:
                game_state.step_gravity()
                last_gravity_time = current_time
            
            # Manual Drop (Down key) speeds up?
            # Implemented in GameState.update for DOWN as immediate move.
            # Usually holding down speeds up gravity. 
            # For now, simple DOWN move is enough.

        elif game_state.state == "animate":
            if current_time - last_anim_time > anim_interval:
                game_state.resolve_world()
                last_anim_time = current_time

        # Draw
        renderer.draw(game_state)
        
        clock.tick(FPS)

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
