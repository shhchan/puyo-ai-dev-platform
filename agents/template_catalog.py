"""Strict public-board template catalog and bounded known-piece fit witnesses.

Coordinates in a catalog are bottom-up. Boards supplied to the matcher are
public wire cells in top-down rows; no hidden piece or future draw is read.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import random
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml

from agents.nextgen_contracts import PUBLIC_CELL_TO_COLOR, PUBLIC_GARBAGE_ID
from puyo_env.actions import NUM_ACTIONS, PLACEMENT_ACTIONS
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, NORMAL_PUYO_COLORS
from src.core.game import GameState
from src.core.puyo import Puyo

SCHEMA_VERSION = "puyo.template_catalog.v1"
_SYMBOL = re.compile(r"[A-Z]")
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_VALIDATION_COLOR_TRIAL_CAP = 100_000
_MAX_MATCH_COLOR_TRIALS = 100_000
_MAX_MATCH_NODES = 100_000


@dataclass(frozen=True)
class _BindingSearch:
    bindings: tuple[tuple[tuple[str, int], ...], ...]
    trials: int
    cutoff: bool


def _enumerate_bindings(classes, different_classes, trial_limit, *, first_only=False):
    """Bound class-color checks, including checks rejected by constraints."""
    colors = range(1, len(NORMAL_PUYO_COLORS) + 1)
    neighbors = [set() for _ in classes]
    for a, b in different_classes:
        neighbors[a].add(b)
        neighbors[b].add(a)
    assigned = {}
    bindings = []
    trials = 0
    cutoff = False

    class _LimitReached(Exception):
        pass

    def domain(index):
        nonlocal trials
        used = {assigned[other] for other in neighbors[index] if other in assigned}
        available = []
        for color in colors:
            if trials >= trial_limit:
                raise _LimitReached
            trials += 1
            if color not in used:
                available.append(color)
        return available

    def visit():
        if len(assigned) == len(classes):
            bindings.append(
                tuple(
                    sorted(
                        (symbol, assigned[i])
                        for i, group in enumerate(classes)
                        for symbol in group
                    )
                )
            )
            return
        choices = [
            (domain(i), -len(neighbors[i]), i)
            for i in range(len(classes))
            if i not in assigned
        ]
        available, _, index = min(
            choices, key=lambda item: (len(item[0]), item[1], item[2])
        )
        for color in available:
            assigned[index] = color
            visit()
            del assigned[index]
            if first_only and bindings:
                return

    try:
        visit()
    except _LimitReached:
        cutoff = True
    return _BindingSearch(tuple(bindings), trials, cutoff)


def _keys(value, required, optional=(), path="value"):
    if (
        type(value) is not dict
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        raise ValueError(f"{path}: missing or unknown keys")


def _id(value, path):
    if type(value) is not str or not _ID.fullmatch(value):
        raise ValueError(f"{path}: invalid ID")
    return value


def _positive_int(value, path):
    if type(value) is not int or value <= 0:
        raise ValueError(f"{path}: expected positive integer")
    return value


def _coord(value, path):
    if (
        type(value) is not list
        or len(value) != 2
        or any(type(v) is not int for v in value)
    ):
        raise ValueError(f"{path}: expected [x, y_from_bottom]")
    x, y = value
    if not (0 <= x < GRID_WIDTH and 0 <= y < GRID_HEIGHT):
        raise ValueError(f"{path}: cell outside board")
    return x, y


def _relations(value, symbols, path):
    if type(value) is not list:
        raise ValueError(f"{path}: expected list")
    result = []
    for pair in value:
        if (
            type(pair) is not list
            or len(pair) != 2
            or any(type(s) is not str or s not in symbols for s in pair)
        ):
            raise ValueError(f"{path}: undefined symbol or invalid pair")
        result.append(tuple(pair))
    return tuple(result)


@dataclass(frozen=True)
class Variant:
    id: str
    origin: tuple[int, int]
    rows: tuple[str, ...]
    same: tuple[tuple[str, str], ...]
    different: tuple[tuple[str, str], ...]
    empty_cells: tuple[tuple[int, int], ...]
    occupied_cells: tuple[tuple[int, int], ...]
    weight: float
    transforms: tuple[str, ...]
    classes: tuple[tuple[str, ...], ...]
    different_classes: tuple[tuple[int, int], ...]
    seed_binding: tuple[tuple[str, int], ...]

    forbidden_cells: tuple[tuple[int, int, str], ...] = ()

    @property
    def width(self):
        return len(self.rows[0])

    @property
    def required_count(self):
        return (
            sum(ch not in ".*" for row in self.rows for ch in row)
            + len(self.empty_cells)
            + len(self.occupied_cells)
        )


@dataclass(frozen=True)
class Template:
    id: str
    version: str
    enabled: bool
    commit_turns: int
    variants: tuple[Variant, ...]


@dataclass(frozen=True)
class TemplateCatalog:
    enabled: bool
    mode: str
    temperature: float
    seed_stream: str
    default_commit_turns: int
    templates: tuple[Template, ...]
    semantic_digest: str

    @classmethod
    def from_dict(cls, data: Mapping) -> TemplateCatalog:
        _keys(
            data,
            {
                "schema_version",
                "enabled",
                "selection",
                "default_commit_turns",
                "templates",
            },
            path="catalog",
        )
        if (
            data["schema_version"] != SCHEMA_VERSION
            or type(data["enabled"]) is not bool
        ):
            raise ValueError("invalid catalog schema or enabled")
        selection = data["selection"]
        _keys(selection, {"mode", "temperature", "seed_stream"}, path="selection")
        if selection["mode"] not in ("argmax", "softmax"):
            raise ValueError("invalid selection mode")
        temp = selection["temperature"]
        if type(temp) not in (int, float) or not math.isfinite(temp) or temp <= 0:
            raise ValueError("temperature must be finite and positive")
        _id(selection["seed_stream"], "seed_stream")
        turns = _positive_int(data["default_commit_turns"], "default_commit_turns")
        if type(data["templates"]) is not list:
            raise ValueError("templates must be a list")
        templates = []
        seen = set()
        for item in data["templates"]:
            _keys(
                item,
                {"id", "version", "enabled", "commit_turns", "variants"},
                path="template",
            )
            tid = _id(item["id"], "template.id")
            if (
                tid in seen
                or type(item["version"]) is not str
                or not item["version"]
                or type(item["enabled"]) is not bool
            ):
                raise ValueError("duplicate template ID or invalid version/enabled")
            seen.add(tid)
            limit = (
                turns
                if item["commit_turns"] is None
                else _positive_int(item["commit_turns"], "commit_turns")
            )
            if type(item["variants"]) is not list or not item["variants"]:
                raise ValueError("template requires variants")
            variants = []
            variant_ids = set()
            for raw in item["variants"]:
                _keys(
                    raw,
                    {
                        "id",
                        "origin",
                        "pattern_rows_bottom_up",
                        "different",
                        "same",
                        "empty_cells",
                        "occupied_cells",
                        "weight",
                        "transforms",
                    },
                    optional={"forbidden_cells"},
                    path="variant",
                )
                vid = _id(raw["id"], "variant.id")
                if vid in variant_ids:
                    raise ValueError("duplicate variant ID")
                variant_ids.add(vid)
                _keys(raw["origin"], {"x", "y_from_bottom"}, path="origin")
                origin = _coord(
                    [raw["origin"]["x"], raw["origin"]["y_from_bottom"]], "origin"
                )
                rows = raw["pattern_rows_bottom_up"]
                if (
                    type(rows) is not list
                    or not rows
                    or any(type(row) is not str for row in rows)
                ):
                    raise ValueError("pattern requires rows")
                width = len(rows[0])
                if not width or any(
                    len(row) != width
                    or any(ch not in ".*" and not _SYMBOL.fullmatch(ch) for ch in row)
                    for row in rows
                ):
                    raise ValueError("invalid pattern width or character")
                if (
                    origin[0] + width > GRID_WIDTH
                    or origin[1] + len(rows) > GRID_HEIGHT
                ):
                    raise ValueError("pattern outside board")
                symbols = {ch for row in rows for ch in row if _SYMBOL.fullmatch(ch)}
                same = _relations(raw["same"], symbols, "same")
                different = _relations(raw["different"], symbols, "different")
                if (
                    type(raw["empty_cells"]) is not list
                    or type(raw["occupied_cells"]) is not list
                ):
                    raise ValueError("structural cells must be lists")
                empty = tuple(_coord(c, "empty_cells") for c in raw["empty_cells"])
                occupied = tuple(
                    _coord(c, "occupied_cells") for c in raw["occupied_cells"]
                )
                if any(
                    not origin[0] <= x < origin[0] + width for x, _ in empty + occupied
                ):
                    raise ValueError("structural cell outside pattern mirror width")
                if type(raw.get("forbidden_cells", [])) is not list:
                    raise ValueError("forbidden color cells must be a list")
                forbidden = []
                for cell in raw.get("forbidden_cells", []):
                    if type(cell) is not list or len(cell) != 3 or type(cell[2]) is not str or cell[2] not in symbols:
                        raise ValueError("invalid forbidden color cell")
                    x, y = _coord(cell[:2], "forbidden_cells")
                    if not origin[0] <= x < origin[0] + width:
                        raise ValueError("forbidden cell outside pattern mirror width")
                    forbidden.append((x, y, cell[2]))
                if len(set(forbidden)) != len(forbidden):
                    raise ValueError("duplicate forbidden color cell")
                symbol_cells = {
                    (origin[0] + x, origin[1] + y)
                    for y, row in enumerate(rows)
                    for x, ch in enumerate(row)
                    if ch not in ".*"
                }
                if len(set(empty) | set(occupied) | symbol_cells) != len(empty) + len(
                    occupied
                ) + len(symbol_cells):
                    raise ValueError("overlapping or duplicate cell conditions")
                if not symbol_cells and not empty and not occupied:
                    raise ValueError("variant has no required cells")
                if origin[1] > 0:
                    for x, y in symbol_cells:
                        if y == origin[1] and not all(
                            (x, support_y) in occupied for support_y in range(y)
                        ):
                            raise ValueError(
                                "raised pattern needs explicit occupied support"
                            )
                parent = {s: s for s in symbols}

                def root(s, parent=parent):
                    while parent[s] != s:
                        s = parent[s]
                    return s

                for a, b in same:
                    parent[root(b)] = root(a)
                classes = tuple(
                    sorted(
                        tuple(sorted(s for s in symbols if root(s) == representative))
                        for representative in {root(s) for s in symbols}
                    )
                )
                index = {
                    symbol: i for i, group in enumerate(classes) for symbol in group
                }
                diff_classes = set()
                for a, b in different:
                    pair = tuple(sorted((index[a], index[b])))
                    if pair[0] == pair[1]:
                        raise ValueError("contradictory same/different constraints")
                    diff_classes.add(pair)
                feasible = _enumerate_bindings(
                    classes,
                    tuple(sorted(diff_classes)),
                    _VALIDATION_COLOR_TRIAL_CAP,
                    first_only=True,
                )
                if not feasible.bindings:
                    if feasible.cutoff:
                        raise ValueError(
                            "color constraints exceed validation complexity cap"
                        )
                    raise ValueError("color constraints have no valid binding")
                weight = raw["weight"]
                if (
                    type(weight) not in (int, float)
                    or not math.isfinite(weight)
                    or weight <= 0
                ):
                    raise ValueError("variant weight must be finite and positive")
                transforms = raw["transforms"]
                if (
                    type(transforms) is not list
                    or not transforms
                    or any(type(t) is not str for t in transforms)
                    or len(set(transforms)) != len(transforms)
                    or any(t not in ("identity", "mirror_x") for t in transforms)
                ):
                    raise ValueError("invalid transforms")
                variants.append(
                    Variant(
                        vid,
                        origin,
                        tuple(rows),
                        same,
                        different,
                        empty,
                        occupied,
                        float(weight),
                        tuple(transforms),
                        classes,
                        tuple(sorted(diff_classes)),
                        feasible.bindings[0],
                        tuple(sorted(forbidden)),
                    )
                )
            templates.append(
                Template(tid, item["version"], item["enabled"], limit, tuple(variants))
            )
        if data["enabled"] and not any(t.enabled for t in templates):
            raise ValueError("enabled catalog has no enabled templates")
        canonical = {
            "schema_version": SCHEMA_VERSION,
            "enabled": data["enabled"],
            "selection": {
                "mode": selection["mode"],
                "temperature": float(temp),
                "seed_stream": selection["seed_stream"],
            },
            "default_commit_turns": turns,
            "templates": [],
        }
        for t in sorted(templates, key=lambda item: item.id):
            canonical["templates"].append(
                {
                    "id": t.id,
                    "version": t.version,
                    "enabled": t.enabled,
                    "commit_turns": t.commit_turns,
                    "variants": [
                        {
                            "id": v.id,
                            "origin": {"x": v.origin[0], "y_from_bottom": v.origin[1]},
                            "pattern_rows_bottom_up": list(v.rows),
                            "symbol_classes": [list(group) for group in v.classes],
                            "different_classes": [
                                list(pair) for pair in v.different_classes
                            ],
                            "empty_cells": [list(p) for p in sorted(v.empty_cells)],
                            "occupied_cells": [
                                list(p) for p in sorted(v.occupied_cells)
                            ],
                            **({"forbidden_cells": [list(p) for p in v.forbidden_cells]} if v.forbidden_cells else {}),
                            "weight": v.weight,
                            "transforms": sorted(v.transforms),
                        }
                        for v in sorted(t.variants, key=lambda item: item.id)
                    ],
                }
            )
        digest = hashlib.sha256(
            json.dumps(
                canonical,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode()
        ).hexdigest()
        return cls(
            data["enabled"],
            selection["mode"],
            float(temp),
            selection["seed_stream"],
            turns,
            tuple(templates),
            digest,
        )


def load_template_catalog(path: str | Path) -> TemplateCatalog:
    class UniqueLoader(yaml.SafeLoader):
        pass

    def construct_mapping(loader, node):
        result = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node)
            if key in result:
                raise ValueError(f"duplicate YAML key: {key}")
            result[key] = loader.construct_object(value_node)
        return result

    UniqueLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping
    )
    return TemplateCatalog.from_dict(
        yaml.load(Path(path).read_text(encoding="utf-8"), Loader=UniqueLoader)
    )


@dataclass(frozen=True)
class TemplateCandidate:
    template_id: str
    variant_id: str
    transform: str
    binding: tuple[tuple[str, int], ...]
    progress: float
    complete: bool
    score: float
    fit_status: str
    reason: str
    witness_candidate_id: str | None
    witness_actions: tuple[int, ...]
    known_prefix_length: int
    cutoff: bool
    score_source: str
    coverage_nodes: int
    compatible: bool = True
    continuation_kind: str = "none"
    continuation_score: float | None = None
    root_progress: tuple[tuple[int, int], ...] = ()

    @property
    def key(self):
        return (self.template_id, self.variant_id, self.transform, self.binding)


@dataclass(frozen=True)
class MatchResult:
    candidates: tuple[TemplateCandidate, ...]
    coverage_nodes: int
    cutoff: bool
    static_bindings: int
    static_cutoff: bool
    binding_trials: int = 0
    static_trials: int = 0
    feature_evaluations: int = 0


@dataclass(frozen=True)
class TemplateSelection:
    candidate: TemplateCandidate | None
    probability: float | None
    rng_position: int | None
    reason: str


class TemplateSelector:
    """Own RNG stream; callers do not share it with draws or scenario sampling."""

    def __init__(self, catalog: TemplateCatalog, seed: int):
        if type(seed) is not int:
            raise ValueError("seed must be integer")
        self.catalog = catalog
        self.rng = random.Random(seed)
        self.position = 0

    def select_initial(self, result: MatchResult) -> TemplateSelection:
        return self._select(result.candidates, fit_only=False)

    def reselect(self, result: MatchResult) -> TemplateSelection:
        return self._select(result.candidates, fit_only=True)

    def _select(self, candidates, *, fit_only):
        by_template = {}
        for candidate in candidates:
            if fit_only and candidate.fit_status != "fit":
                continue
            previous = by_template.get(candidate.template_id)
            if previous is None or (-candidate.score, candidate.key) < (
                -previous.score,
                previous.key,
            ):
                by_template[candidate.template_id] = candidate
        choices = sorted(by_template.values(), key=lambda c: (-c.score, c.key))
        if not choices:
            if fit_only:
                return TemplateSelection(None, None, None, "free_build_no_proven_fit")
            raise ValueError("initial selection requires enabled templates")
        if self.catalog.mode == "argmax":
            return TemplateSelection(choices[0], 1.0, None, "argmax")
        weights = [
            math.exp((c.score - choices[0].score) / self.catalog.temperature)
            for c in choices
        ]
        total = sum(weights)
        probabilities = [w / total for w in weights]
        draw = self.rng.random()
        self.position += 1
        cumulative = 0.0
        for candidate, probability in zip(choices, probabilities, strict=True):
            cumulative += probability
            if draw < cumulative:
                return TemplateSelection(
                    candidate, probability, self.position, "softmax"
                )
        return TemplateSelection(
            choices[-1], probabilities[-1], self.position, "softmax"
        )


def _board(board):
    if not board or any(len(row) != GRID_WIDTH for row in board):
        raise ValueError("public board must have six-column rows")
    if len(board) > GRID_HEIGHT or any(
        cell is not None
        and (type(cell) is not int or not 0 <= cell <= PUBLIC_GARBAGE_ID)
        for row in board
        for cell in row
    ):
        raise ValueError("invalid public board")
    rows = [tuple(row) for row in reversed(board)]
    return tuple(rows + [(None,) * GRID_WIDTH] * (GRID_HEIGHT - len(rows)))


def _conditions(variant, transform):
    def mirror(x):
        return variant.width - 1 - x if transform == "mirror_x" else x

    ox, oy = variant.origin
    symbols = tuple(
        (ox + mirror(x), oy + y, ch)
        for y, row in enumerate(variant.rows)
        for x, ch in enumerate(row)
        if ch not in ".*"
    )

    def structural(cells):
        result = []
        for x, y in cells:
            local_x = x - ox
            result.append(
                (ox + mirror(local_x) if 0 <= local_x < variant.width else x, y)
            )
        return tuple(result)

    forbidden = tuple((structural(((x, y),))[0] + (symbol,))
                      for x, y, symbol in variant.forbidden_cells)
    return symbols, structural(variant.empty_cells), structural(variant.occupied_cells), forbidden


def compile_selected_template(catalog, key):
    """Compile one committed catalog identity; dots impose no empty condition.

    Catalog labels may alias a color. Merge those labels for the backend's
    injective wire binding while retaining the original key in phase evidence.
    Generic occupied support cannot be expressed by the backend v1 contract;
    reject it explicitly instead of silently weakening that catalog condition.
    """
    from agents.selected_template import SelectedTemplate

    tid, vid, transform, binding_tuple = key
    template = next(t for t in catalog.templates if t.id == tid and t.enabled)
    variant = next(v for v in template.variants if v.id == vid)
    binding = dict(binding_tuple)
    if (transform not in variant.transforms or len(binding) != len(binding_tuple)
            or set(binding) != {s for group in variant.classes for s in group}
            or any(type(c) is not int or not 1 <= c <= 5 for c in binding.values())
            or any(binding[a] != binding[b] for a, b in variant.same)
            or any(binding[a] == binding[b] for a, b in variant.different)):
        raise ValueError("invalid committed template identity")
    symbols, empty, occupied, forbidden = _conditions(variant, transform)
    if occupied:
        raise ValueError("selected-template backend cannot encode arbitrary occupied support")
    aliases = tuple(("=".join(sorted(s for s, c in binding.items() if c == color)), color)
                    for color in sorted(set(binding.values())))
    return SelectedTemplate(
        tid, vid, transform, aliases,
        tuple((x, y, binding[s]) for x, y, s in symbols),
        tuple((x, y, 0) for x, y in empty)
        + tuple((x, y, binding[s]) for x, y, s in forbidden),
    )


def _evaluate(board, conditions, binding):
    symbols, empty, occupied, forbidden = conditions
    satisfied = set()
    conflicts = 0
    for x, y, symbol in symbols:
        cell = board[y][x]
        key = ("symbol", x, y)
        if cell == binding[symbol]:
            satisfied.add(key)
        elif cell is not None and cell != 0:
            conflicts += 1
    for name, cells in (("empty", empty), ("occupied", occupied)):
        for x, y in cells:
            cell = board[y][x]
            key = (name, x, y)
            if (name == "empty" and cell == 0) or (
                name == "occupied" and cell is not None and cell != 0
            ):
                satisfied.add(key)
            elif cell is not None:
                conflicts += 1
    conflicts += sum(board[y][x] == binding[symbol] for x, y, symbol in forbidden)
    return satisfied, conflicts


def _game(board, piece):
    game = GameState(seed=0)
    for y, row in enumerate(board):
        for x, cell in enumerate(row):
            if cell is not None:
                game.field.grid[y][x] = Puyo(PUBLIC_CELL_TO_COLOR[cell])
    game.current_puyo_1 = Puyo(PUBLIC_CELL_TO_COLOR[piece[0]])
    game.current_puyo_2 = Puyo(PUBLIC_CELL_TO_COLOR[piece[1]])
    game.state = "control"
    return game


def _wire(game):
    inverse = {color: index for index, color in enumerate(PUBLIC_CELL_TO_COLOR)}
    return tuple(tuple(inverse[p.color] for p in row) for row in game.field.grid)


def _tail_score(before, after, conditions):
    """Prefer connected, low tail cells outside the selected template footprint."""
    symbols, empty, occupied, forbidden = conditions
    reserved = {(x, y) for x, y, _ in symbols} | set(empty) | set(occupied)
    changed = [
        (x, y, after[y][x])
        for y in range(GRID_HEIGHT)
        for x in range(GRID_WIDTH)
        if before[y][x] == 0 and after[y][x] not in (0, None)
    ]
    if not changed or any((x, y) in reserved for x, y, _ in changed):
        return None
    core_columns = {x for x, _, _ in symbols}
    changed_positions = {(x, y) for x, y, _ in changed}
    connections = sum(
        after[ny][nx] == color
        for x, y, color in changed
        for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
        if 0 <= nx < GRID_WIDTH and 0 <= ny < GRID_HEIGHT
        and ((nx, ny) not in changed_positions or (x, y) < (nx, ny))
    )
    heights = [
        sum(after[y][x] not in (0, None) for y in range(GRID_HEIGHT))
        for x in range(GRID_WIDTH)
    ]
    return (
        4 * connections
        + 2 * sum(x not in core_columns for x, _, _ in changed)
        - max(heights) * 2
        - sum(height * height for height in heights) / 16
    )


def match_templates(
    catalog: TemplateCatalog,
    board: Sequence[Sequence[int | None]],
    known_pieces: Sequence[Sequence[int]],
    *,
    node_budget: int,
    binding_budget: int,
    reachable_mask: Sequence[bool] | None = None,
    static_binding_cap: int = 4096,
    preferred_key: tuple | None = None,
    prioritize_static_binding: bool = False,
) -> MatchResult:
    """Static score every enabled template, then spend bounded node/binding quota.

    A node is one legal engine placement and resolution. A fit witness contains
    its root action and full known prefix; a future sampled pair cannot prove fit.
    """
    if (
        type(node_budget) is not int
        or not 0 <= node_budget <= _MAX_MATCH_NODES
        or type(binding_budget) is not int
        or not 0 <= binding_budget <= _MAX_MATCH_COLOR_TRIALS
        or type(static_binding_cap) is not int
        or not 0 < static_binding_cap <= _MAX_MATCH_COLOR_TRIALS
    ):
        raise ValueError("budgets must be bounded non-negative integers")
    if not catalog.enabled:
        raise ValueError("catalog disabled")
    feature_evaluations = 0

    def evaluate(*args):
        nonlocal feature_evaluations
        feature_evaluations += 1
        return _evaluate(*args)

    b = _board(board)
    if len(known_pieces) > 3 or any(
        len(pair) != 2
        or any(
            type(c) is not int or not 1 <= c <= len(NORMAL_PUYO_COLORS) for c in pair
        )
        for pair in known_pieces
    ):
        raise ValueError("expected public current/NEXT/NEXT2 colors")
    if reachable_mask is not None and (
        len(reachable_mask) != NUM_ACTIONS
        or any(type(v) is not bool for v in reachable_mask)
    ):
        raise ValueError("invalid reachable mask")
    if preferred_key is not None and (
        type(preferred_key) is not tuple or len(preferred_key) != 4
    ):
        raise ValueError("invalid preferred template key")
    # Build at least one static candidate per variant/transform before spending quota.
    work = []
    static_bindings = 0
    static_trials = 0
    static_cutoff = False
    for template in catalog.templates:
        if not template.enabled:
            continue
        total_weight = sum(v.weight for v in template.variants)
        for variant in template.variants:
            transformed = set()
            for transform in variant.transforms:
                conditions = _conditions(variant, transform)
                signature = tuple(tuple(sorted(part)) for part in conditions)
                if signature in transformed:
                    continue
                transformed.add(signature)
                static_search = _enumerate_bindings(
                    variant.classes, variant.different_classes, static_binding_cap
                )
                static_trials += static_search.trials
                static_cutoff |= static_search.cutoff
                fallback = None
                for binding in static_search.bindings:
                    static_bindings += 1
                    satisfied, conflicts = evaluate(b, conditions, dict(binding))
                    static_score = (
                        (len(satisfied) - conflicts)
                        / variant.required_count
                        * variant.weight
                        / total_weight
                    )
                    if fallback is None or (-static_score, binding) < (
                        -fallback[1],
                        fallback[0],
                    ):
                        fallback = (binding, static_score)
                if fallback is None:
                    binding = variant.seed_binding
                    satisfied, conflicts = evaluate(b, conditions, dict(binding))
                    fallback = (
                        binding,
                        (len(satisfied) - conflicts)
                        / variant.required_count
                        * variant.weight
                        / total_weight,
                    )
                work.append(
                    (template, variant, transform, conditions, total_weight, *fallback)
                )
    candidates = []
    nodes = 0
    bindings_used = 0
    cutoff_any = False
    all_known = all(cell is not None for row in b for cell in row)
    visible_known = all(cell is not None for row in b[:12] for cell in row)
    visible_stable = (
        all(
            not any(
                b[y][x] == 0 and b[z][x] != 0
                for y in range(12)
                for z in range(y + 1, 12)
            )
            for x in range(GRID_WIDTH)
        )
        if visible_known
        else False
    )
    if preferred_key is not None:
        work.sort(
            key=lambda item: (item[0].id, item[1].id, item[2])
            != preferred_key[:3]
        )
    for (
        template,
        variant,
        transform,
        conditions,
        total_weight,
        fallback_binding,
        fallback_score,
    ) in work:
        if preferred_key is not None and (template.id, variant.id, transform) == preferred_key[:3]:
            values = dict(preferred_key[3])
            if (len(values) != len(preferred_key[3])
                    or set(values) != {s for group in variant.classes for s in group}
                    or any(type(c) is not int or not 1 <= c <= 5 for c in values.values())
                    or any(values[a] != values[b] for a, b in variant.same)
                    or any(values[a] == values[b] for a, b in variant.different)):
                raise ValueError("invalid preferred template binding")
            fallback_binding = tuple(sorted(values.items()))
            satisfied, conflicts = evaluate(b, conditions, dict(fallback_binding))
            fallback_score = (len(satisfied) - conflicts) / variant.required_count * variant.weight / total_weight
        required = variant.required_count
        variant_candidates = []
        evaluated = 0
        preferred_binding = (
            preferred_key[3]
            if preferred_key is not None
            and (template.id, variant.id, transform) == preferred_key[:3]
            and bindings_used < binding_budget
            else None
        )
        if prioritize_static_binding and preferred_key is None and bindings_used < binding_budget:
            # Initial selection has no committed key yet. Its best static
            # binding must receive witness coverage before unrelated color
            # assignments consume the node quota. Like an active preferred
            # binding, this already validated assignment costs one binding unit.
            # Template/variant order and the node quota remain unchanged.
            preferred_binding = fallback_binding
        if preferred_binding is not None:
            bindings_used += 1
        binding_search = _enumerate_bindings(
            variant.classes,
            variant.different_classes,
            binding_budget - bindings_used,
        )
        bindings_used += binding_search.trials
        cutoff_any |= binding_search.cutoff
        search_bindings = list(binding_search.bindings)
        if preferred_binding is not None:
            search_bindings = [preferred_binding] + [
                binding for binding in search_bindings if binding != preferred_binding
            ]
        for binding_tuple in search_bindings:
            evaluated += 1
            binding = dict(binding_tuple)
            before, conflicts = evaluate(b, conditions, binding)
            score = (len(before) - conflicts) / required * variant.weight / total_weight
            static_score = score
            nodes_before = nodes
            status, reason, witness, witness_actions, prefix = (
                "unknown",
                "no_known_legal_witness",
                None,
                (),
                0,
            )
            fixed_conflict = conflicts and preferred_key == (template.id, variant.id, transform, binding_tuple)
            if fixed_conflict:
                status, reason = "no_fit", "fixed_binding_conflict"
            guards_known = all(b[y][x] is not None for x, y, _ in conditions[3])
            complete = len(before) == required and guards_known
            neutral = None
            root_progress = []
            exhaustive = all_known and bool(known_pieces)
            conservative_visible = (
                not all_known
                and visible_known
                and visible_stable
                and bool(known_pieces)
                and reachable_mask is not None
            )
            if (exhaustive or conservative_visible) and conflicts == 0:
                frontier = [(_game(b, known_pieces[0]), (), b)]
                found = False
                witness_rank = None
                search_interrupted = False
                for depth in range(len(known_pieces) if exhaustive else 1):
                    next_frontier = []
                    for game, actions, previous in frontier:
                        for action_id, action in enumerate(PLACEMENT_ACTIONS):
                            if (
                                depth == 0
                                and reachable_mask is not None
                                and not reachable_mask[action_id]
                            ):
                                continue
                            if nodes >= node_budget:
                                exhaustive = False
                                search_interrupted = True
                                break
                            if (
                                game.find_landing_y(action.axis_x, action.rotation)
                                is None
                            ):
                                continue
                            nodes += 1
                            branch = copy.deepcopy(game)
                            step = branch.place_current_pair_and_resolve(
                                action.axis_x, action.rotation, spawn_next=False
                            )
                            if step is None or step["game_over"]:
                                continue
                            after_board = _wire(branch)
                            after, after_conflicts = evaluate(
                                after_board, conditions, binding
                            )
                            if conservative_visible and (
                                step["chain_count"]
                                or any(
                                    after_board[y][x] != b[y][x]
                                    for y in range(12, GRID_HEIGHT)
                                    for x in range(GRID_WIDTH)
                                    if b[y][x] is not None
                                )
                                or any(
                                    after_board[y][x]
                                    for y in range(12, GRID_HEIGHT)
                                    for x in range(GRID_WIDTH)
                                )
                            ):
                                continue
                            previous_satisfied, _ = evaluate(
                                previous, conditions, binding
                            )
                            if after_conflicts or not previous_satisfied <= after:
                                continue
                            if depth == 0:
                                root_progress.append((action_id, len(after) - len(before)))
                            next_actions = actions + (action_id,)
                            new_score = (
                                (len(after) - after_conflicts)
                                / required
                                * variant.weight
                                / total_weight
                            )
                            score = max(score, new_score)
                            if (
                                depth == 0
                                and after_conflicts == 0
                                and conflicts == 0
                                and not step["chain_count"]
                            ):
                                tail_score = _tail_score(b, after_board, conditions)
                                if tail_score is not None and (
                                    neutral is None
                                    or (tail_score, -action_id) > neutral[:2]
                                ):
                                    neutral = (tail_score, -action_id, action_id)
                            before_symbols = sum(key[0] == "symbol" for key in before)
                            after_symbols = sum(key[0] == "symbol" for key in after)
                            if after_conflicts == 0 and (
                                after_symbols > before_symbols
                                or (complete and len(after) == required)
                            ):
                                if found and (new_score, tuple(-a for a in next_actions)) <= witness_rank:
                                    continue
                                witness_rank = (new_score, tuple(-a for a in next_actions))
                                status, reason, prefix = (
                                    "fit",
                                    "known_prefix_witness",
                                    depth + 1,
                                )
                                witness = hashlib.sha256(
                                    json.dumps(
                                        {
                                            "template": template.id,
                                            "variant": variant.id,
                                            "transform": transform,
                                            "binding": binding_tuple,
                                            "actions": next_actions,
                                        },
                                        sort_keys=True,
                                    ).encode()
                                ).hexdigest()
                                witness_actions = next_actions
                                found = True
                                continue
                            if depth + 1 < len(known_pieces):
                                next_pair = known_pieces[depth + 1]
                                branch.current_puyo_1 = Puyo(
                                    PUBLIC_CELL_TO_COLOR[next_pair[0]]
                                )
                                branch.current_puyo_2 = Puyo(
                                    PUBLIC_CELL_TO_COLOR[next_pair[1]]
                                )
                                branch.state = "control"
                                next_frontier.append(
                                    (branch, next_actions, after_board)
                                )
                        if search_interrupted:
                            break
                    if found or search_interrupted:
                        break
                    frontier = next_frontier
                if not found:
                    if neutral is not None:
                        status, reason = "unknown", "compatible_tail_witness"
                        witness_actions = (neutral[2],)
                        prefix = 1
                        witness = hashlib.sha256(
                            json.dumps({
                                "template": template.id,
                                "variant": variant.id,
                                "transform": transform,
                                "binding": binding_tuple,
                                "actions": witness_actions,
                            }, sort_keys=True).encode()
                        ).hexdigest()
                    else:
                        status, reason = (
                            ("no_fit", "exhaustive_known_prefix")
                            if exhaustive
                            else (
                                "unknown",
                                "hidden_cells_or_budget_unobserved"
                                if conservative_visible else "node_budget_exhausted",
                            )
                        )
            if binding_search.cutoff and status != "fit" and not fixed_conflict:
                status, reason = "unknown", "binding_budget_exhausted"
            source = (
                "searched"
                if exhaustive or status == "fit"
                else ("searched_partial" if score > static_score else "static_fallback")
            )
            candidate = TemplateCandidate(
                template.id,
                variant.id,
                transform,
                binding_tuple,
                len(before) / required,
                complete and conflicts == 0,
                score,
                status,
                reason,
                witness,
                witness_actions,
                prefix,
                binding_search.cutoff
                or (not exhaustive and status != "fit" and nodes >= node_budget),
                source,
                nodes - nodes_before,
                conflicts == 0,
                "progress" if status == "fit" else
                "tail" if neutral is not None and witness_actions else "none",
                neutral[0] if status != "fit" and neutral is not None else None,
                tuple(root_progress),
            )
            variant_candidates.append(candidate)
        if not variant_candidates or all(
            c.binding != fallback_binding for c in variant_candidates
        ):
            satisfied, conflicts = evaluate(b, conditions, dict(fallback_binding))
            variant_candidates.append(
                TemplateCandidate(
                    template.id,
                    variant.id,
                    transform,
                    fallback_binding,
                    len(satisfied) / required,
                    len(satisfied) == required and conflicts == 0
                    and all(b[y][x] is not None for x, y, _ in conditions[3]),
                    fallback_score,
                    "no_fit" if conflicts and preferred_key == (template.id, variant.id, transform, fallback_binding) else "unknown",
                    "fixed_binding_conflict" if conflicts and preferred_key == (template.id, variant.id, transform, fallback_binding) else "static_fallback",
                    None,
                    (),
                    0,
                    evaluated == 0 or static_cutoff,
                    "static_fallback",
                    0,
                    conflicts == 0,
                )
            )
        if evaluated == 0 or any(candidate.cutoff for candidate in variant_candidates):
            cutoff_any = True
        candidates.extend(variant_candidates)
    if not candidates:
        raise ValueError("catalog has no enabled candidates")
    return MatchResult(
        tuple(candidates),
        nodes,
        cutoff_any or static_cutoff,
        static_bindings,
        static_cutoff,
        bindings_used,
        static_trials,
        feature_evaluations,
    )
