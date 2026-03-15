from .constants import PuyoColor

class Puyo:
    def __init__(self, color: PuyoColor):
        self.color = color

    def __repr__(self):
        return f"Puyo({self.color.name})"

    def is_empty(self):
        return self.color == PuyoColor.EMPTY

    def is_color_puyo(self):
        return self.color in [PuyoColor.RED, PuyoColor.BLUE, PuyoColor.GREEN, PuyoColor.YELLOW, PuyoColor.PURPLE]

    def is_ojama(self):
        return self.color == PuyoColor.OJAMA
