"""Versioned, deterministic board diagnostics for Adaptive Chain Manager."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping, Sequence

from agents.beam_search import clone_simulator, evaluate_board
from puyo_env.actions import action_to_placement, legal_action_indices
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor, VISIBLE_HEIGHT
from src.core.game import GameState
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo


ANALYZER_INPUT_SCHEMA_VERSION = "puyo.state_analyzer.input.v1"
ANALYZER_DIAGNOSTICS_SCHEMA_VERSION = "puyo.state_analyzer.diagnostics.v1"
_ALLOWED_COLORS = frozenset(
    color.name for color in PuyoColor if color not in {PuyoColor.WALL}
)
_PAIR_COLORS = frozenset(
    color.name
    for color in (
        PuyoColor.RED,
        PuyoColor.BLUE,
        PuyoColor.GREEN,
        PuyoColor.YELLOW,
        PuyoColor.PURPLE,
    )
)


@dataclass(frozen=True)
class AttackPacket:
    amount: int
    deadline: int

    def __post_init__(self) -> None:
        if self.amount < 0 or self.deadline < 0:
            raise ValueError("attack packet amount and deadline must be non-negative")


@dataclass(frozen=True)
class PlayerSnapshot:
    """Serializable state needed to analyze one player."""

    board: tuple[tuple[str, ...], ...]
    current_pair: tuple[str, str]
    next_pairs: tuple[tuple[str, str], ...]
    incoming: tuple[AttackPacket, ...] = ()
    score: int = 0
    sent_ojama_total: int = 0
    canceled_ojama_total: int = 0
    received_ojama_total: int = 0

    def __post_init__(self) -> None:
        if len(self.board) != GRID_HEIGHT or any(len(row) != GRID_WIDTH for row in self.board):
            raise ValueError(f"board must be {GRID_HEIGHT}x{GRID_WIDTH} in bottom-up row order")
        colors = [color for row in self.board for color in row]
        colors.extend(self.current_pair)
        colors.extend(color for pair in self.next_pairs for color in pair)
        unknown = sorted(set(colors).difference(_ALLOWED_COLORS))
        if unknown:
            raise ValueError(f"unknown puyo colors: {', '.join(unknown)}")
        if len(self.current_pair) != 2:
            raise ValueError("current_pair must contain two colors")
        if len(self.next_pairs) < 2 or any(len(pair) != 2 for pair in self.next_pairs):
            raise ValueError("next_pairs must contain at least two color pairs")
        pair_colors = set(self.current_pair)
        pair_colors.update(color for pair in self.next_pairs for color in pair)
        if not pair_colors.issubset(_PAIR_COLORS):
            raise ValueError("current_pair and next_pairs must contain only normal puyo colors")
        if min(self.score, self.sent_ojama_total, self.canceled_ojama_total, self.received_ojama_total) < 0:
            raise ValueError("score and ojama totals must be non-negative")


@dataclass(frozen=True)
class AnalyzerInput:
    own: PlayerSnapshot
    opponent: PlayerSnapshot
    turn: int = 0
    tick: int = 0
    policy_deadline: int = 0
    schema_version: str = ANALYZER_INPUT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != ANALYZER_INPUT_SCHEMA_VERSION:
            raise ValueError(f"unsupported analyzer input schema: {self.schema_version}")
        if min(self.turn, self.tick, self.policy_deadline) < 0:
            raise ValueError("turn, tick, and policy_deadline must be non-negative")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "AnalyzerInput":
        return cls(
            own=_player_snapshot_from_dict(value["own"]),
            opponent=_player_snapshot_from_dict(value["opponent"]),
            turn=int(value.get("turn", 0)),
            tick=int(value.get("tick", 0)),
            policy_deadline=int(value.get("policy_deadline", 0)),
            schema_version=str(value.get("schema_version", "")),
        )

    @classmethod
    def from_runtime_info(cls, info: Mapping[str, Any]) -> "AnalyzerInput":
        own_simulator = info.get("simulator")
        opponent_simulator = info.get("opponent_simulator")
        if own_simulator is None or opponent_simulator is None:
            raise ValueError("runtime info must include simulator and opponent_simulator")
        own_packets = tuple(
            AttackPacket(int(packet["amount"]), _packet_deadline(packet, info))
            for packet in info.get("incoming_attack_packets", ())
        )
        opponent_pending = max(0, int(info.get("opponent_pending_ojama", 0)))
        opponent_deadline = max(0, int(info.get("opponent_incoming_turns", 0)))
        return cls(
            own=_snapshot_from_simulator(
                own_simulator,
                incoming=own_packets,
                score=int(info.get("score", 0)),
                sent=int(info.get("sent_ojama_total", 0)),
                canceled=int(info.get("canceled_ojama_total", 0)),
                received=int(info.get("received_ojama_total", 0)),
            ),
            opponent=_snapshot_from_simulator(
                opponent_simulator,
                incoming=(AttackPacket(opponent_pending, opponent_deadline),) if opponent_pending else (),
                score=int(info.get("opponent_score", 0)),
                sent=int(info.get("opponent_sent_ojama_total", 0)),
                canceled=int(info.get("opponent_canceled_ojama_total", 0)),
                received=int(info.get("opponent_received_ojama_total", 0)),
            ),
            turn=max(0, int(info.get("step_count", 0))),
            tick=max(0, int(info.get("tick", 0))),
            policy_deadline=max(0, int(info.get("policy_deadline", 0))),
        )


@dataclass(frozen=True)
class AnalyzerConfig:
    max_depth: int = 3
    beam_width: int = 24
    max_attack_options: int = 8

    def __post_init__(self) -> None:
        if not 1 <= self.max_depth <= 3:
            raise ValueError("max_depth must be in [1, 3]")
        if self.beam_width <= 0 or self.max_attack_options <= 0:
            raise ValueError("beam_width and max_attack_options must be positive")


@dataclass(frozen=True)
class AttackOption:
    turns: int
    chain_count: int
    score: int
    attack: int
    attack_per_turn: float
    action_path: tuple[tuple[int, str], ...]
    trigger_cells: tuple[tuple[int, int], ...]
    chain_group_counts: tuple[int, ...]
    is_immediate: bool
    is_main_chain: bool = False
    is_sub_chain: bool = False
    is_all_clear: bool = False
    hard_to_answer: bool = False


@dataclass(frozen=True)
class AttackForecast:
    immediate_attack: int
    short_attack: int
    main_chain: AttackOption | None
    turns_to_best: int


@dataclass(frozen=True)
class PlayerAnalysis:
    danger: float
    vulnerability: float
    is_all_clear: bool
    forecast: AttackForecast
    attack_options: tuple[AttackOption, ...]


@dataclass(frozen=True)
class IncomingAnalysis:
    amount: int
    deadline: int
    max_return_by_deadline: int
    can_cancel: bool
    can_counter: bool
    counter_deficit: int


@dataclass(frozen=True)
class AnalyzerDiagnostics:
    own: PlayerAnalysis
    opponent: PlayerAnalysis
    incoming: IncomingAnalysis
    turn: int
    tick: int
    policy_deadline: int
    schema_version: str = ANALYZER_DIAGNOSTICS_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _SearchNode:
    simulator: HeadlessPuyoSimulator
    score: int
    max_chain: int
    path: tuple[tuple[int, str], ...]


class StateAnalyzer:
    """Summarize observations without selecting a tactic or placement."""

    def __init__(self, config: AnalyzerConfig | None = None):
        self.config = config or AnalyzerConfig()

    def analyze(self, analyzer_input: AnalyzerInput) -> AnalyzerDiagnostics:
        own = self._analyze_player(analyzer_input.own)
        opponent = self._analyze_player(analyzer_input.opponent)
        own = _with_options(own, _mark_sub_chains(own.attack_options))
        opponent = _with_options(
            opponent,
            _mark_hard_to_answer(opponent.attack_options, own.forecast),
        )
        incoming_amount = sum(packet.amount for packet in analyzer_input.own.incoming)
        deadline = min((packet.deadline for packet in analyzer_input.own.incoming), default=0)
        max_return = _return_by_deadline(own.forecast, deadline) if incoming_amount else 0
        return AnalyzerDiagnostics(
            own=own,
            opponent=opponent,
            incoming=IncomingAnalysis(
                amount=incoming_amount,
                deadline=deadline,
                max_return_by_deadline=max_return,
                can_cancel=incoming_amount > 0 and max_return >= incoming_amount,
                can_counter=incoming_amount > 0 and max_return > incoming_amount,
                counter_deficit=max(0, incoming_amount - max_return),
            ),
            turn=analyzer_input.turn,
            tick=analyzer_input.tick,
            policy_deadline=analyzer_input.policy_deadline,
        )

    def _analyze_player(self, snapshot: PlayerSnapshot) -> PlayerAnalysis:
        simulator = _simulator_from_snapshot(snapshot)
        options = self._search(simulator)
        main = max(options, key=lambda option: (option.chain_count, option.attack, -option.turns), default=None)
        if main is not None:
            options = tuple(replace(option, is_main_chain=option == main) for option in options)
            main = next(option for option in options if option.is_main_chain)
        immediate = max((option.attack for option in options if option.turns == 1), default=0)
        short = max((option.attack for option in options), default=0)
        danger = _board_danger(snapshot.board)
        vulnerability = min(
            1.0,
            danger * 0.7
            + (0.2 if immediate == 0 else 0.0)
            + (0.1 if not options else 0.0),
        )
        return PlayerAnalysis(
            danger=danger,
            vulnerability=vulnerability,
            is_all_clear=_is_all_clear(snapshot.board),
            forecast=AttackForecast(
                immediate_attack=immediate,
                short_attack=short,
                main_chain=main,
                turns_to_best=0 if main is None else main.turns,
            ),
            attack_options=options,
        )

    def _search(self, root: HeadlessPuyoSimulator) -> tuple[AttackOption, ...]:
        frontier = [_SearchNode(clone_simulator(root), 0, 0, ())]
        found: list[AttackOption] = []
        for depth in range(1, self.config.max_depth + 1):
            seen: dict[tuple[Any, ...], tuple[float, _SearchNode]] = {}
            for node in frontier:
                for action_index in legal_action_indices(node.simulator):
                    child = clone_simulator(node.simulator)
                    result = child.step(action_to_placement(action_index))
                    if not result.valid or result.game_over:
                        continue
                    path = node.path + ((result.action.axis_x, result.action.rotation.name),)
                    score = node.score + max(0, int(result.score_delta))
                    max_chain = max(node.max_chain, int(result.chain_count))
                    next_node = _SearchNode(child, score, max_chain, path)
                    rank = score * 1_000.0 + max_chain * 100_000.0 + evaluate_board(child.game)
                    key = _simulator_key(child)
                    previous = seen.get(key)
                    if previous is None or (rank, path) > (previous[0], previous[1].path):
                        seen[key] = (rank, next_node)
                    if result.score_delta > 0 and result.chain_count > 0:
                        trigger_cells: tuple[tuple[int, int], ...] = ()
                        if result.chains:
                            trigger_cells = tuple(sorted(result.chains[0].vanished))
                        option_score = max(0, int(result.score_delta))
                        found.append(
                            AttackOption(
                                turns=depth,
                                chain_count=int(result.chain_count),
                                score=option_score,
                                attack=option_score // 70,
                                attack_per_turn=(option_score // 70) / float(depth),
                                action_path=path,
                                trigger_cells=trigger_cells,
                                chain_group_counts=tuple(len(chain.groups) for chain in result.chains),
                                is_immediate=depth == 1,
                                is_all_clear=_game_is_all_clear(child.game),
                            )
                        )
            candidates = list(seen.values())
            candidates.sort(key=lambda item: (item[0], item[1].path), reverse=True)
            frontier = [item[1] for item in candidates[: self.config.beam_width]]
            if not frontier:
                break
        unique: dict[tuple[Any, ...], AttackOption] = {}
        for option in found:
            key = (option.turns, option.chain_count, option.attack, option.trigger_cells)
            previous = unique.get(key)
            if previous is None or option.action_path < previous.action_path:
                unique[key] = option
        ranked = sorted(
            unique.values(),
            key=lambda option: (
                option.attack_per_turn,
                option.attack,
                option.chain_count,
                -option.turns,
                tuple((-x, rotation) for x, rotation in option.action_path),
            ),
            reverse=True,
        )
        selected = ranked[: self.config.max_attack_options]
        main = max(
            unique.values(),
            key=lambda option: (option.chain_count, option.attack, -option.turns),
            default=None,
        )
        if main is not None and main not in selected:
            selected[-1] = main
        return tuple(selected)


def _player_snapshot_from_dict(value: Mapping[str, Any]) -> PlayerSnapshot:
    return PlayerSnapshot(
        board=tuple(tuple(str(color) for color in row) for row in value["board"]),
        current_pair=tuple(str(color) for color in value["current_pair"]),
        next_pairs=tuple(tuple(str(color) for color in pair) for pair in value["next_pairs"]),
        incoming=tuple(AttackPacket(int(packet["amount"]), int(packet["deadline"])) for packet in value.get("incoming", ())),
        score=int(value.get("score", 0)),
        sent_ojama_total=int(value.get("sent_ojama_total", 0)),
        canceled_ojama_total=int(value.get("canceled_ojama_total", 0)),
        received_ojama_total=int(value.get("received_ojama_total", 0)),
    )


def _packet_deadline(packet: Mapping[str, Any], info: Mapping[str, Any]) -> int:
    if "arrival_step" in packet:
        return max(0, int(packet["arrival_step"]) - int(info.get("step_count", 0)))
    if "turns_to_arrival" in packet:
        return max(0, int(packet["turns_to_arrival"]))
    return max(0, int(info.get("incoming_turns", 0)))


def _snapshot_from_simulator(
    simulator: HeadlessPuyoSimulator,
    *,
    incoming: tuple[AttackPacket, ...],
    score: int,
    sent: int,
    canceled: int,
    received: int,
) -> PlayerSnapshot:
    game = simulator.game
    current = (game.current_puyo_1, game.current_puyo_2)
    if any(puyo is None for puyo in current):
        raise ValueError("simulator must have an active current pair")
    next_pairs = tuple(tuple(puyo.color.name for puyo in pair) for pair in game.next_puyo_queue)
    if len(next_pairs) < 2:
        raise ValueError("simulator must expose at least two NEXT pairs")
    return PlayerSnapshot(
        board=tuple(tuple(puyo.color.name for puyo in row) for row in game.field.grid),
        current_pair=(current[0].color.name, current[1].color.name),
        next_pairs=next_pairs,
        incoming=incoming,
        score=max(0, score),
        sent_ojama_total=max(0, sent),
        canceled_ojama_total=max(0, canceled),
        received_ojama_total=max(0, received),
    )


def _simulator_from_snapshot(snapshot: PlayerSnapshot) -> HeadlessPuyoSimulator:
    game = GameState(seed=0)
    for y, row in enumerate(snapshot.board):
        for x, color in enumerate(row):
            game.field.grid[y][x] = Puyo(PuyoColor[color])
    game.current_puyo_1 = Puyo(PuyoColor[snapshot.current_pair[0]])
    game.current_puyo_2 = Puyo(PuyoColor[snapshot.current_pair[1]])
    game.next_puyo_queue = deque(
        (Puyo(PuyoColor[pair[0]]), Puyo(PuyoColor[pair[1]]))
        for pair in snapshot.next_pairs
    )
    game.state = "control"
    game.game_over = False
    return HeadlessPuyoSimulator(game_state=game)


def _simulator_key(simulator: HeadlessPuyoSimulator) -> tuple[Any, ...]:
    game = simulator.game
    board = tuple(puyo.color.name for row in game.field.grid for puyo in row)
    current = (game.current_puyo_1.color.name, game.current_puyo_2.color.name)
    next_pairs = tuple(tuple(puyo.color.name for puyo in pair) for pair in game.next_puyo_queue)
    return board, current, next_pairs


def _board_danger(board: Sequence[Sequence[str]]) -> float:
    heights = []
    ojama = 0
    for x in range(GRID_WIDTH):
        height = 0
        for y in range(GRID_HEIGHT - 1, -1, -1):
            color = board[y][x]
            if color == PuyoColor.OJAMA.name:
                ojama += 1
            if height == 0 and color != PuyoColor.EMPTY.name:
                height = y + 1
        heights.append(height)
    center = heights[2] / float(VISIBLE_HEIGHT)
    peak = max(heights) / float(VISIBLE_HEIGHT)
    nuisance = min(ojama / 30.0, 1.0)
    return min(1.0, center * 0.55 + peak * 0.35 + nuisance * 0.10)


def _is_all_clear(board: Sequence[Sequence[str]]) -> bool:
    return all(color == PuyoColor.EMPTY.name for row in board for color in row)


def _game_is_all_clear(game: GameState) -> bool:
    return all(puyo.is_empty() for row in game.field.grid for puyo in row)


def _return_by_deadline(forecast: AttackForecast, deadline: int) -> int:
    if deadline <= 0:
        return 0
    if deadline == 1:
        return forecast.immediate_attack
    return forecast.short_attack


def _mark_sub_chains(options: tuple[AttackOption, ...]) -> tuple[AttackOption, ...]:
    main = next((option for option in options if option.is_main_chain), None)
    if main is None:
        return options
    return tuple(
        replace(
            option,
            is_sub_chain=not option.is_main_chain and option.attack_per_turn >= main.attack_per_turn,
        )
        for option in options
    )


def _mark_hard_to_answer(
    options: tuple[AttackOption, ...],
    own_forecast: AttackForecast,
) -> tuple[AttackOption, ...]:
    return tuple(
        replace(option, hard_to_answer=_return_by_deadline(own_forecast, option.turns) < option.attack)
        for option in options
    )


def _with_options(player: PlayerAnalysis, options: tuple[AttackOption, ...]) -> PlayerAnalysis:
    main = next((option for option in options if option.is_main_chain), None)
    return replace(
        player,
        attack_options=options,
        forecast=replace(player.forecast, main_chain=main),
    )
