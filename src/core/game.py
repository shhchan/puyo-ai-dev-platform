import random
from collections import deque
from .constants import (
    GRID_WIDTH,
    GRID_HEIGHT,
    PuyoColor,
    Action,
    Direction,
    LOCK_CONTACT_LIMIT,
    LOCK_FRAME_LIMIT,
)
from .puyo import Puyo
from .field import Field

class GameState:
    def __init__(self):
        self.field = Field()
        self.score = 0
        self.chain_count = 0
        self.game_over = False
        
        self.current_puyo_1 = None 
        self.current_puyo_2 = None 
        self.puyo_x = 2
        self.puyo_y = 12 # Top (Index 12)
        self.puyo_rot = Direction.UP 
        
        self.next_puyo_queue = deque()
        self._fill_next_queue()
        self.spawn_puyo()
        
        self.state = "control" 
        self.animation_state = None 
        self.drop_timer = 0
        self.ground_contact_count = 0
        self.ground_frame_count = 0
        self.was_grounded_prev_frame = False
        
    def _fill_next_queue(self):
        while len(self.next_puyo_queue) < 2:
            colors = [PuyoColor.RED, PuyoColor.BLUE, PuyoColor.GREEN, PuyoColor.YELLOW, PuyoColor.PURPLE]
            p1 = Puyo(random.choice(colors))
            p2 = Puyo(random.choice(colors))
            self.next_puyo_queue.append((p1, p2))

    def spawn_puyo(self):
        # Check game over condition
        # If the spawn point or critical point is blocked.
        # Usually checking (2, 11) i.e. 12th row.
        # If (2, 11) is occupied, we die? 
        # Or if we can't spawn at (2, 12)?
        # Let's check (2, 11) (Top Visible). If it's blocked, it usually means game over soon.
        # Strict rule: If X marker is blocked. X marker is usually at (2, 12).
        # But (2, 12) is Hidden.
        
        # Let's rely on: If we can't place spawned puyo, game over.
        
        spawn_y = 12
        if not self.field.get_puyo(2, spawn_y).is_empty():
             self.game_over = True
             self.state = "gameover"
             return

        next_pair = self.next_puyo_queue.popleft()
        self._fill_next_queue()
        
        self.current_puyo_1 = next_pair[0] 
        self.current_puyo_2 = next_pair[1] 
        
        self.puyo_x = 2
        self.puyo_y = spawn_y 
        self.puyo_rot = Direction.UP 
        self.state = "control"
        self.ground_contact_count = 0
        self.ground_frame_count = 0
        self.was_grounded_prev_frame = False

    def get_sub_puyo_offset(self, rotation):
        # UP = +Y (Screen UP) -> In Y=0 Bottom, UP is +1
        if rotation == Direction.UP:
            return (0, 1) 
        elif rotation == Direction.RIGHT:
            return (1, 0)
        elif rotation == Direction.DOWN:
            return (0, -1)
        elif rotation == Direction.LEFT:
            return (-1, 0)
        return (0, 1)

    def can_move(self, dx, dy, rot):
        # dy = -1 for gravity (Down)
        tx1, ty1 = self.puyo_x + dx, self.puyo_y + dy
        if not (0 <= tx1 < GRID_WIDTH and 0 <= ty1 < GRID_HEIGHT):
            return False
        if not self.field.get_puyo(tx1, ty1).is_empty():
            return False
            
        ox, oy = self.get_sub_puyo_offset(rot)
        tx2, ty2 = tx1 + ox, ty1 + oy
        # Sub puyo can be at GRID_HEIGHT (14th row, Ind 13) momentarily during rotation?
        # Yes, Ghost row exists.
        if not (0 <= tx2 < GRID_WIDTH and 0 <= ty2 < GRID_HEIGHT):
            return False
        if not self.field.get_puyo(tx2, ty2).is_empty():
            return False
            
        return True

    def rotate(self, clockwise):
        dirs = [Direction.UP, Direction.RIGHT, Direction.DOWN, Direction.LEFT]
        idx = dirs.index(self.puyo_rot)
        new_idx = (idx + 1) % 4 if clockwise else (idx - 1) % 4
        new_rot = dirs[new_idx]
        
        if self.can_move(0, 0, new_rot):
            self.puyo_rot = new_rot
        else:
            axis_below_blocked = self.puyo_y == 0 or (not self.field.get_puyo(self.puyo_x, self.puyo_y - 1).is_empty())
            if axis_below_blocked and self.can_move(0, 1, new_rot):
                self.puyo_y += 1
                self.puyo_rot = new_rot
            elif self.can_move(1, 0, new_rot):
                self.puyo_x += 1
                self.puyo_rot = new_rot
            elif self.can_move(-1, 0, new_rot):
                self.puyo_x -= 1
                self.puyo_rot = new_rot

    def _update_ground_lock(self):
        if self.state != "control" or self.current_puyo_1 is None:
            return

        grounded = not self.can_move(0, -1, self.puyo_rot)
        if grounded:
            self.ground_frame_count += 1
            if not self.was_grounded_prev_frame:
                self.ground_contact_count += 1
            self.was_grounded_prev_frame = True

            if self.ground_contact_count >= LOCK_CONTACT_LIMIT or self.ground_frame_count >= LOCK_FRAME_LIMIT:
                self.lock_puyo()
        else:
            self.was_grounded_prev_frame = False

    def update(self, actions):
        if self.state == "gameover":
            return

        if self.state == "control":
            left_pressed = Action.LEFT in actions
            right_pressed = Action.RIGHT in actions
            down_pressed = Action.DOWN in actions

            horizontal_requested = False
            horizontal_moved = False

            if left_pressed and not right_pressed:
                horizontal_requested = True
                if self.can_move(-1, 0, self.puyo_rot):
                    self.puyo_x -= 1
                    horizontal_moved = True
            elif right_pressed and not left_pressed:
                horizontal_requested = True
                if self.can_move(1, 0, self.puyo_rot):
                    self.puyo_x += 1
                    horizontal_moved = True

            if down_pressed:
                # Horizontal input has priority only when horizontal movement succeeds.
                can_apply_down = (not horizontal_requested) or (not horizontal_moved)
                if can_apply_down and self.can_move(0, -1, self.puyo_rot):
                    self.puyo_y -= 1

            for action in actions:
                if action == Action.ROTATE_RIGHT:
                    self.rotate(True)
                elif action == Action.ROTATE_LEFT:
                    self.rotate(False)

            self._update_ground_lock()

    def step_gravity(self):
        if self.state == "control":
            if self.can_move(0, -1, self.puyo_rot): # Down is -Y
                self.puyo_y -= 1

    def lock_puyo(self):
        self.field.place_puyo(self.puyo_x, self.puyo_y, self.current_puyo_1)
        ox, oy = self.get_sub_puyo_offset(self.puyo_rot)
        self.field.place_puyo(self.puyo_x + ox, self.puyo_y + oy, self.current_puyo_2)
        
        self.current_puyo_1 = None
        self.current_puyo_2 = None
        self.state = "animate"
        self.animation_state = "drop"
        self.drop_timer = 0
        self.chain_count = 0

    def resolve_world(self):
        dropped = self.field.drop_puyo()
        if dropped:
            return 
            
        vanish = self.field.check_vanish()
        if vanish:
            self.field.remove_puyos(vanish)
            self.score += len(vanish) * 10 * (self.chain_count + 1)
            self.chain_count += 1
            return 
            
        # Specific Defeat Check: (2,11)?
        # If we just locked, and now nothing happened.
        # Check if spawn point is blocked is done at spawn_puyo.
        
        self.spawn_puyo()
