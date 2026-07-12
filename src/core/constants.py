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

NORMAL_PUYO_COLORS = (
    PuyoColor.RED,
    PuyoColor.BLUE,
    PuyoColor.GREEN,
    PuyoColor.YELLOW,
)

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
GRAVITY_INTERVAL_SECONDS = 5

# Input repeat tuning
DAS_INITIAL_DELAY = 0.15
DAS_REPEAT_INTERVAL = 0.05
SOFT_DROP_REPEAT_INTERVAL = 0.08

# Ground lock tuning
LOCK_CONTACT_LIMIT = 8
LOCK_FRAME_LIMIT = 32

COUNTDOWN_SECONDS = 3.0

# Animation tuning
VANISH_FLASH_SECONDS = 0.5
CHAIN_DROP_TWEEN_SECONDS = 0.24
VANISH_BLINK_INTERVAL_SECONDS = 0.08

# Puyo Puyo Tsu all-clear bonus. The bonus is awarded once on the next
# successful chain after a chain resolution empties the field.
ALL_CLEAR_BONUS_SCORE = 2100

# Deterministic realtime headless timing.
#
# Existing UI-facing values above remain seconds. Realtime headless code uses
# these derived tick constants as the single source of truth for fixed-step
# simulation and replay fixtures.
REALTIME_TICK_RATE = 60
REALTIME_TICK_SECONDS = 1.0 / REALTIME_TICK_RATE
REALTIME_GRAVITY_INTERVAL_TICKS = max(1, round(GRAVITY_INTERVAL_SECONDS * REALTIME_TICK_RATE))
REALTIME_DAS_INITIAL_DELAY_TICKS = max(1, round(DAS_INITIAL_DELAY * REALTIME_TICK_RATE))
REALTIME_DAS_REPEAT_INTERVAL_TICKS = max(1, round(DAS_REPEAT_INTERVAL * REALTIME_TICK_RATE))
REALTIME_SOFT_DROP_REPEAT_INTERVAL_TICKS = max(1, round(SOFT_DROP_REPEAT_INTERVAL * REALTIME_TICK_RATE))
REALTIME_VANISH_FLASH_TICKS = max(1, round(VANISH_FLASH_SECONDS * REALTIME_TICK_RATE))
REALTIME_CHAIN_DROP_TWEEN_TICKS = max(1, round(CHAIN_DROP_TWEEN_SECONDS * REALTIME_TICK_RATE))
REALTIME_ATTACK_DELAY_TICKS = REALTIME_TICK_RATE

# Scoring bonus tables (Puyo Puyo Tsu rules)
# Index is chain count. 19+ chains use index 19.
CHAIN_BONUS_TABLE = (
    0,    # 0 (unused)
    0,    # 1 chain
    8,    # 2 chain
    16,   # 3 chain
    32,   # 4 chain
    64,   # 5 chain
    96,   # 6 chain
    128,  # 7 chain
    160,  # 8 chain
    192,  # 9 chain
    224,  # 10 chain
    256,  # 11 chain
    288,  # 12 chain
    320,  # 13 chain
    352,  # 14 chain
    384,  # 15 chain
    416,  # 16 chain
    448,  # 17 chain
    480,  # 18 chain
    512,  # 19+ chain
)

COLOR_BONUS_TABLE = {
    1: 0,
    2: 3,
    3: 6,
    4: 12,
    5: 24,
}


def get_connection_bonus(group_size):
    if group_size <= 4:
        return 0
    if group_size == 5:
        return 2
    if group_size == 6:
        return 3
    if group_size == 7:
        return 4
    if group_size == 8:
        return 5
    if group_size == 9:
        return 6
    if group_size == 10:
        return 7
    return 10
