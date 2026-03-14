import time
import pygame
from .core.constants import (
    Action,
    DAS_INITIAL_DELAY,
    DAS_REPEAT_INTERVAL,
    SOFT_DROP_REPEAT_INTERVAL,
)

class InputHandler:
    def __init__(self):
        self.rotation_key_map = {
            pygame.K_LEFT: Action.ROTATE_LEFT,
            pygame.K_RIGHT: Action.ROTATE_RIGHT,
        }
        self.quit_keys = {pygame.K_q, pygame.K_ESCAPE}
        self.hold_key_actions = {
            pygame.K_a: Action.LEFT,
            pygame.K_d: Action.RIGHT,
            pygame.K_s: Action.DOWN,
        }
        self.repeat_config = {
            pygame.K_a: (DAS_INITIAL_DELAY, DAS_REPEAT_INTERVAL),
            pygame.K_d: (DAS_INITIAL_DELAY, DAS_REPEAT_INTERVAL),
            pygame.K_s: (SOFT_DROP_REPEAT_INTERVAL, SOFT_DROP_REPEAT_INTERVAL),
        }
        self.held_keys = {key: False for key in self.hold_key_actions}
        self.next_repeat_at = {key: None for key in self.hold_key_actions}

    def _collect_hold_actions(self, now, pressed_state, just_pressed_keys):
        should_fire = {key: False for key in self.hold_key_actions}

        for key in self.hold_key_actions:
            pressed = bool(pressed_state[key]) or (key in just_pressed_keys)

            if not pressed:
                self.held_keys[key] = False
                self.next_repeat_at[key] = None
                continue

            if not self.held_keys[key]:
                initial_interval, _ = self.repeat_config[key]
                self.held_keys[key] = True
                self.next_repeat_at[key] = now + initial_interval
                should_fire[key] = True
                continue

            next_time = self.next_repeat_at[key]
            if next_time is not None and now >= next_time:
                _, repeat_interval = self.repeat_config[key]
                while self.next_repeat_at[key] <= now:
                    self.next_repeat_at[key] += repeat_interval
                should_fire[key] = True

        return should_fire

    def process_input(self):
        actions = []
        now = time.time()
        just_pressed_keys = set()

        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                actions.append(Action.QUIT)
            elif event.type == pygame.KEYDOWN:
                if event.key in self.quit_keys:
                    actions.append(Action.QUIT)
                elif event.key in self.rotation_key_map:
                    actions.append(self.rotation_key_map[event.key])
                elif event.key in self.hold_key_actions:
                    just_pressed_keys.add(event.key)
            elif event.type == pygame.KEYUP:
                if event.key in self.hold_key_actions:
                    self.held_keys[event.key] = False
                    self.next_repeat_at[event.key] = None

        pressed_state = pygame.key.get_pressed()
        hold_actions = self._collect_hold_actions(now, pressed_state, just_pressed_keys)

        left_held = self.held_keys[pygame.K_a]
        right_held = self.held_keys[pygame.K_d]

        # Left and right cancel each other while both are held.
        if not (left_held and right_held):
            if hold_actions[pygame.K_a]:
                actions.append(Action.LEFT)
            elif hold_actions[pygame.K_d]:
                actions.append(Action.RIGHT)

        if hold_actions[pygame.K_s]:
            actions.append(Action.DOWN)

        return actions
