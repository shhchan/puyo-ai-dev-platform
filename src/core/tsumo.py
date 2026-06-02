import random

from .constants import NORMAL_PUYO_COLORS
from .puyo import Puyo


class PuyoSequence:
    def __init__(self, seed=None, colors=NORMAL_PUYO_COLORS):
        self.seed = seed
        self.colors = tuple(colors)
        if not self.colors:
            raise ValueError("PuyoSequence requires at least one color")
        self._rng = random.Random(seed)

    def next_pair(self):
        return (
            Puyo(self._rng.choice(self.colors)),
            Puyo(self._rng.choice(self.colors)),
        )

    def next_pairs(self, count):
        return [self.next_pair() for _ in range(count)]
