from enum import Enum, auto

class PuyoColor(Enum):
    RED = auto()
    BLUE = auto()
    GREEN = auto()
    YELLOW = auto()
    PURPLE = auto()
    OJAMA = auto()
    WALL = auto()
    EMPTY = auto()

class Direction(Enum):
    UP = auto()
    RIGHT = auto()
    DOWN = auto()
    LEFT = auto()

class Action(Enum):
    LEFT = auto()
    RIGHT = auto()
    DOWN = auto()
    ROTATE_LEFT = auto()
    ROTATE_RIGHT = auto()
    START = auto()
    QUIT = auto()

GRID_WIDTH = 6
GRID_HEIGHT = 14  # visible 12 + hidden 1 + ghost 1
VISIBLE_HEIGHT = 12

PUYO_SIZE = 32  # pixel size for rendering assumption, can be changed in renderer

# Gravity tuning
GRAVITY_INTERVAL_SECONDS = 0.50

# Input repeat tuning
DAS_INITIAL_DELAY = 0.15
DAS_REPEAT_INTERVAL = 0.05
SOFT_DROP_REPEAT_INTERVAL = 0.08

# Ground lock tuning
LOCK_CONTACT_LIMIT = 8
LOCK_FRAME_LIMIT = 32

COUNTDOWN_SECONDS = 3.0

# Animation tuning
VANISH_FLASH_SECONDS = 0.24
CHAIN_DROP_TWEEN_SECONDS = 0.24
VANISH_BLINK_INTERVAL_SECONDS = 0.08
