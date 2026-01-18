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
    QUIT = auto()

GRID_WIDTH = 6
GRID_HEIGHT = 14  # visible 12 + hidden 1 + ghost 1
VISIBLE_HEIGHT = 12

PUYO_SIZE = 32  # pixel size for rendering assumption, can be changed in renderer
