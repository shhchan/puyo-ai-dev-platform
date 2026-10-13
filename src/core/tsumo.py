import copy
import hashlib
import random
from pathlib import Path

from .constants import NORMAL_PUYO_COLORS, PuyoColor
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


ESPORTS_SOURCE_SHA256 = "568a066c7f50dc3ca9e3aa6bdcc284df5e20f3f39ef689a398c61641c34b52eb"
ESPORTS_SOURCE_VERSION = "haipuyo-2019-05-19-sha256-568a066c"
ESPORTS_PATTERN_COUNT = 65536
ESPORTS_PAIR_COUNT = 128
_SOURCE_COLORS = {
    "r": PuyoColor.RED, "g": PuyoColor.GREEN, "b": PuyoColor.BLUE,
    "y": PuyoColor.YELLOW, "p": PuyoColor.PURPLE,
}
_CANONICAL_SOURCE_COLORS = "rgby"


class EsportsTsuSource:
    """Validate the author's LF text source before any pattern is exposed."""

    def __init__(self, path: str | Path):
        raw = Path(path).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if digest != ESPORTS_SOURCE_SHA256:
            raise ValueError(f"unsupported esports tsu source SHA-256: {digest}")
        rows = raw.splitlines()
        if len(rows) != ESPORTS_PATTERN_COUNT:
            raise ValueError("esports tsu source must have 65536 patterns")
        for index, row in enumerate(rows):
            if len(row) != ESPORTS_PAIR_COUNT * 2 or len(set(row)) != 4 or not set(row) <= set(b"rgbyp"):
                raise ValueError(f"invalid esports tsu pattern {index}")
        self.rows = tuple(rows)
        self.checksum = digest
        self.version = ESPORTS_SOURCE_VERSION

    def sequence(self, pattern_id: int):
        if isinstance(pattern_id, bool) or not isinstance(pattern_id, int) or not 0 <= pattern_id < ESPORTS_PATTERN_COUNT:
            raise ValueError("pattern_id must be an integer in [0, 65535]")
        return EsportsTsuSequence(self, pattern_id)

    def color_mapping(self, pattern_id: int) -> dict[str, PuyoColor]:
        """Map any purple to the one absent canonical color for this row."""
        row = self.rows[pattern_id]
        mapping = {key: value for key, value in _SOURCE_COLORS.items() if key != "p" and ord(key) in row}
        if ord("p") in row:
            missing = next(key for key in _CANONICAL_SOURCE_COLORS if ord(key) not in row)
            mapping["p"] = _SOURCE_COLORS[missing]
        return mapping

    def __deepcopy__(self, memo):
        # The validated corpus is immutable; cloning a planner state copies only
        # its sequence cursor, never the 16 MiB corpus.
        memo[id(self)] = self
        return self


class EsportsTsuSequence:
    def __init__(self, source: EsportsTsuSource, pattern_id: int):
        self.source = source
        self.pattern_id = pattern_id
        self.cursor = 0
        self.color_mapping = source.color_mapping(pattern_id)

    def next_pair(self):
        row = self.source.rows[self.pattern_id]
        offset = (self.cursor % ESPORTS_PAIR_COUNT) * 2
        self.cursor += 1
        return tuple(Puyo(self.color_mapping[chr(value)]) for value in row[offset:offset + 2])

    def next_pairs(self, count):
        return [self.next_pair() for _ in range(count)]


def make_tsumo_sequence(*, seed=None, mode="random", source=None, pattern_id=None):
    """Construct a provider without changing the legacy seed/RNG contract."""
    if mode == "random":
        if source is not None or pattern_id is not None:
            raise ValueError("random mode cannot take a source or pattern_id")
        return PuyoSequence(seed=seed)
    if mode == "esports_tsu":
        if source is None or pattern_id is None:
            raise ValueError("esports_tsu requires source and pattern_id")
        return source.sequence(pattern_id)
    raise ValueError(f"unknown tsumo mode: {mode}")


def public_tsumo_game_copy(game):
    """Copy a game for policy input, replacing private corpus state.

    The current pair and two visible NEXT pairs already reside on the game.
    A deterministic random provider supplies only synthetic unknown future
    after those pairs are consumed by planning.
    """
    visible = copy.deepcopy(game)
    if isinstance(visible.puyo_sequence, EsportsTsuSequence):
        visible.puyo_sequence = PuyoSequence(seed=0)
    return visible
