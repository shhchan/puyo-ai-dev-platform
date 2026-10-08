"""Deduce settled own occupancy from a continuous public lifecycle only.

No match, simulator, private field, lock height, future queue or RNG is accepted.
The scratch Field below is constructed exclusively from previous deductions and
visible cells. Clear/garbage inconsistencies, gaps and ambiguous locks fail closed.
"""
from __future__ import annotations

from agents.nextgen_contracts import (
    PUBLIC_CELL_TO_COLOR, PUBLIC_GARBAGE_ID, PublicBoardInference, semantic_digest,
)
from puyo_env.actions import PLACEMENT_ACTIONS
from src.core.constants import Direction
from src.core.field import Field
from src.core.puyo import Puyo

_CELL = {color: i for i, color in enumerate(PUBLIC_CELL_TO_COLOR)}
_OFFSETS = {Direction.UP: (0, 1), Direction.RIGHT: (1, 0),
            Direction.DOWN: (0, -1), Direction.LEFT: (-1, 0)}


def _visible(public):
    return tuple(reversed(public.visible_board))[:12]


def _visible_complete(public):
    return all(v is not None for row in _visible(public) for v in row)


def _resolve(board):
    field = Field()
    field.grid = [[Puyo(PUBLIC_CELL_TO_COLOR[v]) for v in row] for row in board]
    chains = 0
    while True:
        field.drop_puyo()
        vanished = field.check_vanish()
        if not vanished:
            break
        field.remove_puyos(vanished)
        chains += 1
    return tuple(tuple(_CELL[v.color] for v in row) for row in field.grid), chains


class PublicInferenceTracker:
    def __init__(self, public, *, episode_id, player_id, started_tick, empty_reset=False):
        self.episode_id, self.player_id = episode_id, player_id
        self.last_tick = started_tick - 1
        self.last_lock_id = None
        self.seen_locks = set()
        self.public = public
        self.pending = None
        self.dropped = False
        # Empty visible rows or tick zero alone are not an empty-field proof.
        known = empty_reset and _visible_complete(public) and not any(
            v for row in _visible(public) for v in row)
        self.board = (*_visible(public), (0,) * 6, (0,) * 6) if known else None
        self.reason = "empty_reset" if known else "origin_unknown"

    def invalidate(self, reason):
        self.board, self.pending = None, None
        self.reason = reason

    def observe(self, public, *, tick, episode_id, locks=(), resolutions=(), events=()):
        before, self.public = self.public, public
        expected_tick, self.last_tick = self.last_tick + 1, tick
        if episode_id != self.episode_id or tick != expected_tick:
            self.invalidate("lifecycle_gap")
            return
        own_events = tuple(e for e in events if e.player_id == self.player_id)
        placements = tuple(e for e in own_events if e.kind == "placement")
        own_resolutions = tuple(r for r in resolutions if r.player_id == self.player_id)
        if sum(r.chain_count > 0 for r in own_resolutions) != sum(e.kind == "clear" for e in own_events):
            self.invalidate("clear_history_gap")
            return
        if len(locks) != len(placements) or len(locks) > 1:
            self.invalidate("lock_history_gap")
            return
        if any(r.player_id != self.player_id or r.tick != tick or
               r.event_id in self.seen_locks for r in locks):
            self.invalidate("lock_identity_mismatch")
            return
        if any(not before.known_pieces or r.pair != before.known_pieces[0] for r in locks):
            self.invalidate("public_pair_mismatch")
            return
        self.seen_locks.update(r.event_id for r in locks)
        if locks:
            self.last_lock_id = locks[0].event_id
        if own_resolutions and self.pending is None and not locks and self.board is not None:
            self.invalidate("resolution_without_lock")
            return
        self.dropped |= any(e.kind == "drop" for e in own_events)
        if locks and self.board is not None:
            if self.pending is not None or before.phase != "control" or not _visible_complete(before):
                self.invalidate("unsettled_lock")
                return
            if self.board[:12] != _visible(before):
                self.invalidate("prelock_visible_mismatch")
                return
            record = locks[0]
            pose = PLACEMENT_ACTIONS[record.action]
            dx, dy = _OFFSETS[pose.rotation]
            possible = []
            for height in range(14):
                cells = ((pose.axis_x, height), (pose.axis_x + dx, height + dy))
                # Include the possible interpolated top-overflow lock. Only
                # public visibility and groundedness may eliminate its height.
                if any(not 0 <= x < 6 or not 0 <= y <= 14 for x, y in cells):
                    continue
                if any(y < 14 and self.board[y][x] for x, y in cells):
                    continue
                below = tuple((x, y - 1) for x, y in cells)
                if all(0 <= y < 14 and not self.board[y][x] for x, y in below):
                    continue
                placed = [list(row) for row in self.board]
                for (x, y), color in zip(cells, record.pair):
                    if y < 14:
                        placed[y][x] = color
                if _visible_complete(public) and tuple(map(tuple, placed[:12])) != _visible(public):
                    continue
                value = _resolve(placed)
                if value not in possible:
                    possible.append(value)
                if len(possible) > 1:
                    self.invalidate("ambiguous_lock_height")
                    return
            if not possible:
                self.invalidate("inconsistent_lock")
                return
            self.pending = possible[0]
        if self.pending is not None:
            board, chain = self.pending
            if any(r.player_id == self.player_id and r.chain_count != chain for r in resolutions):
                self.invalidate("clear_mismatch")
                return
            if public.phase == "control" and _visible_complete(public):
                if not self._matches(board[:12], _visible(public)):
                    self.invalidate("settled_visible_mismatch")
                    return
                self.board = (*_visible(public), *board[12:])
                self.pending, self.dropped, self.reason = None, False, "public_actual_lock"
        elif self.board is not None and public.phase == "control" and _visible_complete(public):
            if not self._matches(self.board[:12], _visible(public)):
                self.invalidate("visible_mismatch")
                return
            self.board = (*_visible(public), *self.board[12:])
            self.dropped = False

    def _matches(self, expected, actual):
        # The public game rule places garbage only into visible empty cells.
        # We observe the displayed result instead of reconstructing RNG columns.
        return all(a == b or (self.dropped and a == 0 and b == PUBLIC_GARBAGE_ID)
                   for row_a, row_b in zip(expected, actual) for a, b in zip(row_a, row_b))

    def snapshot(self):
        known = (self.board is not None and self.pending is None and
                 self.public.phase == "control" and _visible_complete(self.public))
        return PublicBoardInference(
            self.episode_id, self.player_id, self.last_tick + 1,
            semantic_digest(self.public.visible_board), self.last_lock_id,
            "known" if known else "unknown",
            self.board[12:] if known else ((None,) * 6,) * 2,
            self.reason if self.pending is None else "unsettled",
            origin_episode_id=self.episode_id,
        )
