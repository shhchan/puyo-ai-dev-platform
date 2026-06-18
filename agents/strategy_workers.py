"""Fixed search workers and tactical forecasts used by the strategy manager."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from agents.beam_search import BeamSearchConfig, BeamSearchPolicy, clone_simulator, evaluate_board
from puyo_env.actions import action_to_placement, legal_action_indices
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor, VISIBLE_HEIGHT


STRATEGY_NAMES = (
    "build_large",
    "build_budget",
    "punish",
    "counter",
    "fire_max",
    "survival",
    # PUYO-28 checkpoint compatibility.
    "large_chain",
    "quick_attack",
    "fire",
)
BUILD_STRATEGIES = {"build_large", "build_budget", "large_chain", "quick_attack"}


@dataclass(frozen=True)
class WorkerProfile:
    """One discrete manager action and its search budget."""

    profile_id: int
    name: str
    strategy: str
    depth: int = 1
    width: int = 22
    scenarios: int = 1
    minimum_chain_count: int = 1
    chain_weight: float = 100_000.0
    score_weight: float = 1.0
    premature_chain_penalty: float = 350.0
    safety_margin: int = 2
    danger_tolerance: float = 0.75

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGY_NAMES:
            raise ValueError(f"unknown strategy: {self.strategy}")
        if self.profile_id < 0:
            raise ValueError("profile_id must be non-negative")


@dataclass(frozen=True)
class AttackForecast:
    immediate_chain: int = 0
    immediate_attack: int = 0
    short_attack: int = 0
    medium_attack: int = 0
    turns_to_best: int = 0


@dataclass(frozen=True)
class TacticalObjective:
    """Serializable contract that tells a worker what outcome to search for."""

    kind: str
    target_attack: int = 0
    target_score: int = 0
    target_chain: int = 0
    deadline: int = 0
    deadline_ticks: int = 0
    safety_margin: int = 0
    max_danger: float = 1.0
    fallback_strategy: str = "survival"
    source_profile_id: int = -1
    source_profile_name: str = ""
    reason: str = ""

    @property
    def allowed_danger(self) -> float:
        return self.max_danger

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "search-objective-v1",
            "kind": self.kind,
            "target_attack": int(self.target_attack),
            "target_score": int(self.target_score),
            "target_chain": int(self.target_chain),
            "deadline": int(self.deadline),
            "deadline_ticks": int(self.deadline_ticks),
            "safety_margin": int(self.safety_margin),
            "allowed_danger": float(self.max_danger),
            "fallback_strategy": self.fallback_strategy,
            "source_profile_id": int(self.source_profile_id),
            "source_profile_name": self.source_profile_name,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class ObjectiveResult:
    """Outcome diagnostics for one objective-conditioned proposal."""

    achieved: bool
    possible_by_deadline: bool
    miss_reasons: tuple[str, ...] = ()
    surplus_attack: int = 0
    score_delta: int = 0
    chain_delta: int = 0
    deadline_missed: bool = False
    danger_excess: float = 0.0
    time_overrun_ticks: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "achieved": bool(self.achieved),
            "possible_by_deadline": bool(self.possible_by_deadline),
            "miss_reasons": list(self.miss_reasons),
            "surplus_attack": int(self.surplus_attack),
            "score_delta": int(self.score_delta),
            "chain_delta": int(self.chain_delta),
            "deadline_missed": bool(self.deadline_missed),
            "danger_excess": float(self.danger_excess),
            "time_overrun_ticks": int(self.time_overrun_ticks),
        }


@dataclass(frozen=True)
class TacticalContext:
    own_forecast: AttackForecast
    opponent_forecast: AttackForecast
    own_danger: float
    opponent_danger: float
    opponent_capacity: int
    lethal_target: int
    lethal_margin: int
    incoming_attack: int
    incoming_deadline: int
    counter_target: int
    max_return_by_deadline: int
    counter_deficit: int
    build_potential: int
    build_safety: float
    recommended_strategy: str
    switch_reason: str
    incoming_deadline_ticks: int = 0


@dataclass(frozen=True)
class SearchProposal:
    """Worker result consumed by policies, training, and diagnostics."""

    action: int
    profile_id: int
    profile_name: str
    strategy: str
    predicted_chain_count: int
    predicted_score: int
    predicted_attack: int
    danger: float
    elapsed_seconds: float
    expanded_nodes: int
    candidate_value: float
    target_attack: int = 0
    incoming_attack: int = 0
    deadline: int = 0
    max_return_attack: int = 0
    reason: str = ""
    objective: TacticalObjective | None = None
    objective_result: ObjectiveResult | None = None

    @property
    def objective_dict(self) -> dict[str, Any]:
        return {} if self.objective is None else self.objective.to_dict()

    @property
    def objective_result_dict(self) -> dict[str, Any]:
        return {} if self.objective_result is None else self.objective_result.to_dict()


@dataclass(frozen=True)
class SearchContext:
    observation: dict[str, Any]
    info: dict[str, Any]
    tactical: TacticalContext

    @property
    def simulator(self):
        return self.info.get("simulator")


class SearchWorker(Protocol):
    def propose(
        self,
        context: SearchContext,
        profile: WorkerProfile,
        objective: TacticalObjective,
    ) -> SearchProposal:
        """Return one legal placement and its diagnostics."""


def default_worker_profiles() -> tuple[WorkerProfile, ...]:
    """Budgets suitable for repeated versus decisions in Python."""

    return (
        WorkerProfile(0, "build_large", "build_large", depth=6, width=32, minimum_chain_count=6),
        WorkerProfile(
            1,
            "build_budget",
            "build_budget",
            depth=3,
            width=16,
            minimum_chain_count=4,
            chain_weight=65_000.0,
        ),
        WorkerProfile(2, "punish", "punish", depth=3, width=18, safety_margin=0),
        WorkerProfile(3, "counter", "counter", depth=3, width=18, safety_margin=2),
        WorkerProfile(4, "fire_max", "fire_max"),
        WorkerProfile(5, "survival", "survival", danger_tolerance=0.55),
    )


def smoke_worker_profiles() -> tuple[WorkerProfile, ...]:
    """Small deterministic budgets for tests and pipeline smoke runs."""

    return (
        WorkerProfile(0, "build_large", "build_large", depth=2, width=8, minimum_chain_count=3),
        WorkerProfile(1, "build_budget", "build_budget", depth=2, width=8, minimum_chain_count=2),
        WorkerProfile(2, "punish", "punish", depth=2, width=8, safety_margin=0),
        WorkerProfile(3, "counter", "counter", depth=2, width=8, safety_margin=1),
        WorkerProfile(4, "fire_max", "fire_max"),
        WorkerProfile(5, "survival", "survival"),
    )


def scaled_worker_profiles(
    profiles: tuple[WorkerProfile, ...],
    *,
    depth_scale: float = 1.0,
    width_scale: float = 1.0,
) -> tuple[WorkerProfile, ...]:
    """Return execution-only budgets while preserving profile ids and semantics."""

    return tuple(
        WorkerProfile(
            **{
                **profile.__dict__,
                "depth": max(1, int(round(profile.depth * depth_scale))),
                "width": max(4, int(round(profile.width * width_scale))),
            }
        )
        for profile in profiles
    )


def profile_id_by_name(profiles: tuple[WorkerProfile, ...], *names: str) -> int:
    for profile in profiles:
        if profile.name in names or profile.strategy in names:
            return profile.profile_id
    raise KeyError(f"worker profile not found: {names}")


def board_danger(game) -> float:
    """Return a bounded height/ojama risk estimate."""

    heights = []
    ojama = 0
    for x in range(GRID_WIDTH):
        height = 0
        for y in range(GRID_HEIGHT - 1, -1, -1):
            puyo = game.field.grid[y][x]
            if puyo.color == PuyoColor.OJAMA:
                ojama += 1
            if height == 0 and not puyo.is_empty():
                height = y + 1
        heights.append(height)
    center = heights[2] / float(GRID_HEIGHT)
    peak = max(heights) / float(GRID_HEIGHT)
    nuisance = min(ojama / 30.0, 1.0)
    return min(1.0, center * 0.55 + peak * 0.35 + nuisance * 0.10)


def estimate_attack_forecast(
    simulator,
    *,
    max_depth: int = 2,
    width: int = 3,
) -> AttackForecast:
    """Bounded cloned rollout used for manager features, not full worker search."""

    if simulator is None:
        return AttackForecast()
    frontier = [(clone_simulator(simulator), 0, 0)]
    best_chain = 0
    best_by_depth = {1: 0, 2: 0, 3: 0}
    best_attack = 0
    best_turn = 0
    for depth in range(1, max(1, min(int(max_depth), 3)) + 1):
        candidates = []
        for parent, cumulative_attack, cumulative_chain in frontier:
            for action in legal_action_indices(parent):
                child = clone_simulator(parent)
                result = child.step(action_to_placement(action))
                if not result.valid or result.game_over:
                    continue
                attack = cumulative_attack + max(0, int(result.score_delta // 70))
                chain = max(cumulative_chain, int(result.chain_count))
                best_chain = max(best_chain, chain)
                best_by_depth[depth] = max(best_by_depth[depth], attack)
                if attack > best_attack:
                    best_attack = attack
                    best_turn = depth
                heuristic = attack * 100_000.0 + chain * 10_000.0 + evaluate_board(child.game)
                candidates.append((heuristic, child, attack, chain))
        candidates.sort(key=lambda item: item[0], reverse=True)
        frontier = [(item[1], item[2], item[3]) for item in candidates[: max(1, width)]]
        if not frontier:
            break
    return AttackForecast(
        immediate_chain=best_chain if max_depth == 1 else _estimate_immediate_chain(simulator),
        immediate_attack=best_by_depth[1],
        short_attack=max(best_by_depth[1], best_by_depth[2]),
        medium_attack=max(best_by_depth.values()),
        turns_to_best=best_turn,
    )


def estimate_immediate_threat(simulator) -> tuple[int, int]:
    forecast = estimate_attack_forecast(simulator, max_depth=1, width=22)
    return forecast.immediate_chain, forecast.immediate_attack


def _estimate_immediate_chain(simulator) -> int:
    best_chain = 0
    for action in legal_action_indices(simulator):
        child = clone_simulator(simulator)
        result = child.step(action_to_placement(action))
        if result.valid:
            best_chain = max(best_chain, int(result.chain_count))
    return best_chain


def _opponent_capacity(simulator, pending: int) -> int:
    if simulator is None:
        return 0
    center_height = 0
    for y in range(VISIBLE_HEIGHT - 1, -1, -1):
        if not simulator.game.field.get_puyo(2, y).is_empty():
            center_height = y + 1
            break
    rows_to_choke = max(0, VISIBLE_HEIGHT - center_height)
    return max(0, rows_to_choke * GRID_WIDTH - max(0, int(pending)))


def build_tactical_context(info: dict[str, Any]) -> TacticalContext:
    cached = info.get("tactical_context")
    if isinstance(cached, TacticalContext):
        return cached
    own_simulator = info.get("simulator")
    opponent_simulator = info.get("opponent_simulator")
    own_forecast = estimate_attack_forecast(own_simulator)
    opponent_forecast = estimate_attack_forecast(opponent_simulator)
    own_danger = board_danger(own_simulator.game) if own_simulator is not None else 1.0
    opponent_danger = board_danger(opponent_simulator.game) if opponent_simulator is not None else 1.0
    incoming = max(0, int(info.get("incoming_ojama", info.get("pending_ojama", 0))))
    deadline = max(0, int(info.get("incoming_turns", 0)))
    deadline_ticks = max(0, int(info.get("incoming_ticks", info.get("incoming_arrival_tick", 0)) or 0))
    opponent_pending = max(0, int(info.get("opponent_pending_ojama", 0)))
    capacity = _opponent_capacity(opponent_simulator, opponent_pending)
    lethal_target = max(1, min(30, capacity + 1))
    lethal_margin = own_forecast.immediate_attack - lethal_target
    counter_target = incoming + (2 if incoming > 0 else 0)
    if deadline <= 1:
        max_return = own_forecast.immediate_attack
    elif deadline == 2:
        max_return = own_forecast.short_attack
    else:
        max_return = own_forecast.medium_attack
    counter_deficit = counter_target - max_return
    build_potential = own_forecast.medium_attack
    build_safety = max(0.0, 1.0 - own_danger)

    incoming_dangerous = incoming > 0 and (incoming >= max(6, capacity // 2) or own_danger >= 0.6)
    if incoming_dangerous and counter_deficit <= 0:
        recommended = "counter"
        reason = "incoming attack is dangerous and can be canceled before arrival"
    elif incoming_dangerous and counter_deficit > 0:
        recommended = "survival"
        reason = "incoming attack exceeds the estimated return before deadline"
    elif lethal_margin >= 0:
        recommended = "punish"
        reason = "an immediate attack reaches the estimated lethal target"
    elif own_danger >= 0.82:
        recommended = "survival"
        reason = "board danger is above the survival threshold"
    elif own_forecast.immediate_attack >= 12 and build_safety < 0.35:
        recommended = "fire_max"
        reason = "banked immediate attack should be fired before board safety collapses"
    else:
        recommended = "build_large"
        reason = "no urgent lethal, counter, or survival condition is active"
    return TacticalContext(
        own_forecast=own_forecast,
        opponent_forecast=opponent_forecast,
        own_danger=own_danger,
        opponent_danger=opponent_danger,
        opponent_capacity=capacity,
        lethal_target=lethal_target,
        lethal_margin=lethal_margin,
        incoming_attack=incoming,
        incoming_deadline=deadline,
        counter_target=counter_target,
        max_return_by_deadline=max_return,
        counter_deficit=counter_deficit,
        build_potential=build_potential,
        build_safety=build_safety,
        recommended_strategy=recommended,
        switch_reason=reason,
        incoming_deadline_ticks=deadline_ticks,
    )


def objective_for_profile(tactical: TacticalContext, profile: WorkerProfile) -> TacticalObjective:
    strategy = profile.strategy
    if strategy == "punish":
        return TacticalObjective(
            kind="punish",
            target_attack=tactical.lethal_target,
            target_score=tactical.lethal_target * 70,
            target_chain=1,
            deadline=max(1, min(profile.depth, 3)),
            deadline_ticks=tactical.incoming_deadline_ticks,
            max_danger=profile.danger_tolerance,
            fallback_strategy="fire_max",
            source_profile_id=profile.profile_id,
            source_profile_name=profile.name,
            reason=tactical.switch_reason,
        )
    if strategy == "counter":
        deadline = max(1, min(profile.depth, tactical.incoming_deadline or 1))
        return TacticalObjective(
            kind="counter",
            target_attack=tactical.counter_target,
            target_score=tactical.counter_target * 70,
            target_chain=1,
            deadline=deadline,
            deadline_ticks=tactical.incoming_deadline_ticks,
            safety_margin=profile.safety_margin,
            max_danger=profile.danger_tolerance,
            fallback_strategy="survival",
            source_profile_id=profile.profile_id,
            source_profile_name=profile.name,
            reason=tactical.switch_reason,
        )
    if strategy in {"fire", "fire_max"}:
        return TacticalObjective(
            kind="fire_max",
            target_attack=max(1, tactical.own_forecast.immediate_attack),
            target_score=max(70, tactical.own_forecast.immediate_attack * 70),
            target_chain=max(1, tactical.own_forecast.immediate_chain),
            deadline=1,
            deadline_ticks=tactical.incoming_deadline_ticks,
            fallback_strategy="survival",
            source_profile_id=profile.profile_id,
            source_profile_name=profile.name,
            reason=tactical.switch_reason,
        )
    if strategy == "survival":
        return TacticalObjective(
            kind="survival",
            deadline=1,
            deadline_ticks=tactical.incoming_deadline_ticks,
            max_danger=profile.danger_tolerance,
            fallback_strategy="survival",
            source_profile_id=profile.profile_id,
            source_profile_name=profile.name,
            reason=tactical.switch_reason,
        )
    return TacticalObjective(
        kind="build",
        target_attack=0,
        target_chain=profile.minimum_chain_count,
        deadline=max(1, profile.depth),
        max_danger=profile.danger_tolerance,
        fallback_strategy="survival",
        source_profile_id=profile.profile_id,
        source_profile_name=profile.name,
        reason=tactical.switch_reason,
    )


def objective_from_v1_profile(profile: WorkerProfile, tactical: TacticalContext) -> TacticalObjective:
    """Compatibility shim for the v1.0 fixed-profile manager contract."""

    return objective_for_profile(tactical, profile)


class BeamStrategyWorker:
    """Adapter that applies a build profile to the shared beam search engine."""

    def propose(
        self,
        context: SearchContext,
        profile: WorkerProfile,
        objective: TacticalObjective,
    ) -> SearchProposal:
        started = time.perf_counter()
        policy = BeamSearchPolicy(
            BeamSearchConfig(
                depth=profile.depth,
                width=profile.width,
                scenarios=profile.scenarios,
                minimum_chain_count=profile.minimum_chain_count,
                chain_weight=profile.chain_weight,
                score_weight=profile.score_weight,
                premature_chain_penalty=profile.premature_chain_penalty,
            )
        )
        action = policy.select_action(context.observation, context.info)
        diagnostics = policy.last_diagnostics
        result, danger = _preview_action(context.simulator, action)
        values = dict(diagnostics.candidate_values) if diagnostics is not None else {}
        return _proposal(
            profile,
            objective,
            context.tactical,
            action=action,
            chain=result.chain_count if result is not None else 0,
            score=result.score_delta if result is not None else 0,
            attack=(result.score_delta // 70) if result is not None else 0,
            danger=danger,
            elapsed=(diagnostics.elapsed_seconds if diagnostics is not None else time.perf_counter() - started),
            expanded=diagnostics.expanded_nodes if diagnostics is not None else 0,
            value=float(values.get(action, 0.0)),
        )


@dataclass
class _TacticalCandidate:
    simulator: Any
    first_action: int
    attack: int
    score: int
    chain: int
    danger: float
    depth: int
    value: float


class TacticalStrategyWorker:
    """Bounded objective search for punish, counter, fire, and survival."""

    def propose(
        self,
        context: SearchContext,
        profile: WorkerProfile,
        objective: TacticalObjective,
    ) -> SearchProposal:
        simulator = context.simulator
        legal = legal_action_indices(simulator) if simulator is not None else _legal_from_info(context.info)
        if not legal:
            return _proposal(profile, objective, context.tactical, action=0, danger=1.0)

        started = time.perf_counter()
        max_depth = 1 if objective.kind in {"fire_max", "survival"} else max(1, objective.deadline)
        frontier: list[_TacticalCandidate] = []
        all_candidates: list[_TacticalCandidate] = []
        expanded = 0
        for depth in range(1, max_depth + 1):
            parents = frontier if depth > 1 else [None]
            next_frontier: list[_TacticalCandidate] = []
            for parent in parents:
                parent_simulator = simulator if parent is None else parent.simulator
                for action in legal_action_indices(parent_simulator):
                    child = clone_simulator(parent_simulator)
                    result = child.step(action_to_placement(action))
                    expanded += 1
                    if not result.valid:
                        continue
                    first_action = action if parent is None else parent.first_action
                    attack = (0 if parent is None else parent.attack) + max(0, int(result.score_delta // 70))
                    score = (0 if parent is None else parent.score) + max(0, int(result.score_delta))
                    chain = max(0 if parent is None else parent.chain, int(result.chain_count))
                    danger = board_danger(child.game)
                    value = _tactical_value(objective, attack, score, chain, danger, depth, child.game)
                    if result.game_over:
                        value -= 1_000_000.0
                    candidate = _TacticalCandidate(
                        child,
                        first_action,
                        attack,
                        score,
                        chain,
                        danger,
                        depth,
                        value,
                    )
                    next_frontier.append(candidate)
                    all_candidates.append(candidate)
            next_frontier.sort(key=lambda item: item.value, reverse=True)
            frontier = next_frontier[: max(1, profile.width)]
            if not frontier:
                break
        if not all_candidates:
            return _proposal(profile, objective, context.tactical, action=legal[0], danger=1.0)
        best = max(all_candidates, key=lambda item: item.value)
        return _proposal(
            profile,
            objective,
            context.tactical,
            action=best.first_action,
            chain=best.chain,
            score=best.score,
            attack=best.attack,
            danger=best.danger,
            elapsed=time.perf_counter() - started,
            expanded=expanded,
            value=best.value,
            depth=best.depth,
        )


def _tactical_value(
    objective: TacticalObjective,
    attack: int,
    score: int,
    chain: int,
    danger: float,
    depth: int,
    game,
) -> float:
    if objective.kind in {"punish", "counter"}:
        deficit = max(0, objective.target_attack - attack)
        excess = max(0, attack - objective.target_attack)
        reached = 1.0 if deficit == 0 else 0.0
        return (
            reached * 1_000_000.0
            + attack * 30_000.0
            - deficit * 50_000.0
            - excess * 1_500.0
            - depth * 8_000.0
            - danger * 25_000.0
        )
    if objective.kind == "fire_max":
        return attack * 100_000.0 + chain * 10_000.0 + score - danger * 20_000.0
    if objective.kind == "survival":
        return evaluate_board(game) - danger * 100_000.0 + attack * 500.0
    return evaluate_board(game) + chain * 10_000.0 - danger * 20_000.0


def _proposal(
    profile: WorkerProfile,
    objective: TacticalObjective,
    tactical: TacticalContext,
    *,
    action: int,
    chain: int = 0,
    score: int = 0,
    attack: int = 0,
    danger: float = 1.0,
    elapsed: float = 0.0,
    expanded: int = 0,
    value: float = 0.0,
    depth: int = 1,
) -> SearchProposal:
    result = _evaluate_objective(
        objective,
        tactical,
        attack=int(attack),
        score=int(score),
        chain=int(chain),
        danger=float(danger),
        depth=int(depth),
    )
    return SearchProposal(
        action=action,
        profile_id=profile.profile_id,
        profile_name=profile.name,
        strategy=profile.strategy,
        predicted_chain_count=int(chain),
        predicted_score=int(score),
        predicted_attack=int(attack),
        danger=float(danger),
        elapsed_seconds=float(elapsed),
        expanded_nodes=int(expanded),
        candidate_value=float(value),
        target_attack=objective.target_attack,
        incoming_attack=tactical.incoming_attack,
        deadline=objective.deadline,
        max_return_attack=tactical.max_return_by_deadline,
        reason=objective.reason,
        objective=objective,
        objective_result=result,
    )


def _evaluate_objective(
    objective: TacticalObjective,
    tactical: TacticalContext,
    *,
    attack: int,
    score: int,
    chain: int,
    danger: float,
    depth: int,
) -> ObjectiveResult:
    miss_reasons: list[str] = []
    deadline_missed = objective.deadline > 0 and depth > objective.deadline
    if objective.target_attack > 0 and attack < objective.target_attack:
        miss_reasons.append("target_attack")
    if objective.target_score > 0 and score < objective.target_score:
        miss_reasons.append("target_score")
    if objective.target_chain > 0 and chain < objective.target_chain:
        miss_reasons.append("target_chain")
    danger_excess = max(0.0, float(danger) - float(objective.max_danger))
    if danger_excess > 0.0:
        miss_reasons.append("allowed_danger")
    if deadline_missed:
        miss_reasons.append("deadline")

    possible_by_deadline = True
    if objective.deadline > 0 and objective.target_attack > 0:
        if objective.deadline <= max(1, tactical.incoming_deadline or objective.deadline):
            possible_by_deadline = objective.target_attack <= tactical.max_return_by_deadline
        else:
            possible_by_deadline = objective.target_attack <= tactical.build_potential
        if not possible_by_deadline:
            miss_reasons.append("impossible_by_deadline")

    return ObjectiveResult(
        achieved=not miss_reasons,
        possible_by_deadline=possible_by_deadline,
        miss_reasons=tuple(dict.fromkeys(miss_reasons)),
        surplus_attack=max(0, int(attack) - int(objective.target_attack)),
        score_delta=int(score) - int(objective.target_score),
        chain_delta=int(chain) - int(objective.target_chain),
        deadline_missed=deadline_missed,
        danger_excess=danger_excess,
    )


class StrategyOrchestrator:
    """Execute exactly one worker selected by a manager action."""

    def __init__(self, profiles: tuple[WorkerProfile, ...] | None = None):
        self.profiles = profiles or default_worker_profiles()
        expected = tuple(range(len(self.profiles)))
        actual = tuple(profile.profile_id for profile in self.profiles)
        if actual != expected:
            raise ValueError(f"profile ids must be contiguous from zero: {actual}")
        self._beam_worker = BeamStrategyWorker()
        self._tactical_worker = TacticalStrategyWorker()
        self.last_proposal: SearchProposal | None = None
        self.last_tactical_context: TacticalContext | None = None

    def propose(self, profile_id: int, observation: dict[str, Any], info: dict[str, Any]) -> SearchProposal:
        profile = self.profiles[int(profile_id)]
        tactical = build_tactical_context(info)
        self.last_tactical_context = tactical
        context = SearchContext(observation=observation, info=info, tactical=tactical)
        objective = objective_for_profile(tactical, profile)
        worker = self._beam_worker if profile.strategy in BUILD_STRATEGIES else self._tactical_worker
        self.last_proposal = worker.propose(context, profile, objective)
        return self.last_proposal

    def select_action(self, profile_id: int, observation: dict[str, Any], info: dict[str, Any]) -> int:
        return self.propose(profile_id, observation, info).action


class FixedProfilePolicy:
    """Policy adapter used for worker baselines and smoke evaluation."""

    def __init__(self, profile_id: int, profiles: tuple[WorkerProfile, ...] | None = None):
        self.profile_id = int(profile_id)
        self.orchestrator = StrategyOrchestrator(profiles)
        self.last_proposal: SearchProposal | None = None

    def select_action(self, observation: dict[str, Any], info: dict[str, Any]) -> int:
        self.last_proposal = self.orchestrator.propose(self.profile_id, observation, info)
        return self.last_proposal.action


def _preview_action(simulator, action: int):
    if simulator is None:
        return None, 1.0
    child = clone_simulator(simulator)
    result = child.step(action_to_placement(action))
    if not result.valid:
        return None, 1.0
    return result, board_danger(child.game)


def _legal_from_info(info: dict[str, Any]) -> list[int]:
    mask = info.get("action_mask")
    if mask is None:
        return []
    return [index for index, allowed in enumerate(mask) if bool(allowed)]
