import pygame
import sys
import time
from src.core.game import GameState
from src.core.constants import PUYO_SIZE, Action
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
    last_frame_time = time.time()

    running = True
    while running:
        current_time = time.time()
        delta_time = current_time - last_frame_time
        last_frame_time = current_time
        
        # Input
        actions = input_handler.process_input()
        if Action.QUIT in actions:
            running = False

        game_state.update(actions)

        # Update
        if game_state.state == "countdown":
            game_state.advance_countdown(delta_time)
        elif game_state.state == "control":
            # Gravity (Auto Drop)
            if current_time - last_gravity_time > gravity_interval:
                game_state.step_gravity()
                last_gravity_time = current_time
        elif game_state.state == "animate":
            if current_time - last_anim_time > anim_interval:
                game_state.resolve_world()
                last_anim_time = current_time

        if game_state.state != "control":
            last_gravity_time = current_time

        fall_offset_px = 0.0
        if (
            game_state.state == "control"
            and game_state.current_puyo_1 is not None
            and game_state.can_move(0, -1, game_state.puyo_rot)
        ):
            progress = min(1.0, max(0.0, (current_time - last_gravity_time) / gravity_interval))
            fall_offset_px = progress * PUYO_SIZE

        # Draw
        renderer.draw(game_state, fall_offset_px=fall_offset_px)
        
        clock.tick(FPS)

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    main()
