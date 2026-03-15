import argparse
import pygame
import sys
import time
from src.core.game import GameState
from src.core.constants import (
    PUYO_SIZE,
    Action,
    SOFT_DROP_REPEAT_INTERVAL,
    GRAVITY_INTERVAL_SECONDS,
)
from src.ui.renderer import Renderer
from src.input_handler import InputHandler

SCREEN_WIDTH = 400
SCREEN_HEIGHT = 600
FPS = 60


def _quantize_half_cell(progress):
    clamped = min(1.0, max(0.0, progress))
    if clamped >= 1.0:
        return 1.0
    if clamped >= 0.5:
        return 0.5
    return 0.0


def parse_cli_args(argv=None):
    parser = argparse.ArgumentParser(description="Puyo Base")
    parser.add_argument(
        "-d",
        "--debug",
        action="store_true",
        help="Show debug HUD and offscreen rows (13-14).",
    )
    return parser.parse_args(argv)


def main(debug_mode=False):
    pygame.init()
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption("Puyo Base")
    clock = pygame.time.Clock()

    game_state = GameState()
    renderer = Renderer(screen, debug_mode=debug_mode)
    input_handler = InputHandler()

    last_gravity_time = time.time()
    gravity_interval = GRAVITY_INTERVAL_SECONDS
    
    last_frame_time = time.time()
    last_soft_drop_move_time = time.time()
    soft_drop_was_held = False

    running = True
    while running:
        current_time = time.time()
        delta_time = current_time - last_frame_time
        last_frame_time = current_time
        
        # Input
        actions = input_handler.process_input()
        if Action.QUIT in actions:
            running = False

        left_held = input_handler.is_action_held(Action.LEFT)
        right_held = input_handler.is_action_held(Action.RIGHT)
        down_held = input_handler.is_action_held(Action.DOWN)
        held_actions = {
            Action.LEFT: left_held,
            Action.RIGHT: right_held,
            Action.DOWN: down_held,
        }

        fall_offset_cells = 0.0
        if (
            game_state.state == "control"
            and game_state.current_puyo_1 is not None
            and game_state.can_move(0, -1, game_state.puyo_rot)
        ):
            auto_progress = min(1.0, max(0.0, (current_time - last_gravity_time) / gravity_interval))
            fall_offset_cells = _quantize_half_cell(auto_progress)

            if down_held:
                if not soft_drop_was_held:
                    last_soft_drop_move_time = current_time
                elapsed = current_time - last_soft_drop_move_time
                soft_drop_progress = 0.5 if elapsed >= (SOFT_DROP_REPEAT_INTERVAL * 0.5) else 0.0
                fall_offset_cells = max(fall_offset_cells, soft_drop_progress)
        else:
            last_soft_drop_move_time = current_time

        game_state.set_vertical_interpolation(fall_offset_cells)

        prev_state = game_state.state
        prev_y = game_state.puyo_y
        game_state.update(actions, held_actions=held_actions)

        # Update
        if game_state.state == "countdown":
            game_state.advance_countdown(delta_time)
        elif game_state.state == "control":
            # Gravity (Auto Drop)
            if current_time - last_gravity_time > gravity_interval:
                before_gravity_y = game_state.puyo_y
                game_state.step_gravity()
                if game_state.puyo_y != before_gravity_y:
                    last_gravity_time = current_time
        elif game_state.state == "animate":
            game_state.advance_animation(delta_time)

        if game_state.state != "control":
            last_gravity_time = current_time

        manual_soft_drop_moved = (
            prev_state == "control"
            and game_state.state == "control"
            and Action.DOWN in actions
            and game_state.puyo_y < prev_y
        )
        if manual_soft_drop_moved:
            last_soft_drop_move_time = current_time
            game_state.set_vertical_interpolation(0.0)

        soft_drop_was_held = down_held

        if game_state.state != "control":
            game_state.set_vertical_interpolation(0.0)

        fall_offset_px = game_state.vertical_interpolation_progress * PUYO_SIZE

        # Draw
        renderer.draw(game_state, fall_offset_px=fall_offset_px)
        
        clock.tick(FPS)

    pygame.quit()
    sys.exit()

if __name__ == "__main__":
    args = parse_cli_args()
    main(debug_mode=args.debug)
