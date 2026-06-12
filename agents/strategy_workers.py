"""Fixed search workers used by the strategy manager."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

from agents.beam_search import BeamSearchConfig, BeamSearchPolicy, clone_simulator, evaluate_board
from puyo_env.actions import action_to_placement, legal_action_indices
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor


STRATEGY_NAMES = ("large_chain", "quick_attack", "fire", "survival")


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

    def __post_init__(self) -> None:
        if self.strategy not in STRATEGY_NAMES:
            raise ValueError(f"unknown strategy: {self.strategy}")
        if self.profile_id < 0:
            raise ValueError("profile_id must be non-negative")


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


@dataclass(frozen=True)
class SearchContext:
    observation: dict[str, Any]
    info: dict[str, Any]

    @property
    def simulator(self):
        return self.info.get("simulator")


class SearchWorker(Protocol):
    def propose(self, context: SearchContext, profile: WorkerProfile) -> SearchProposal:
        """Return one legal placement and its diagnostics."""


def default_worker_profiles() -> tuple[WorkerProfile, ...]:
    """Budgets suitable for repeated versus decisions in Python."""

    return (
        WorkerProfile(
            profile_id=0,
            name="large_chain",
            strategy="large_chain",
            depth=6,
            width=32,
            minimum_chain_count=6,
        ),
        WorkerProfile(
            profile_id=1,
            name="quick_attack",
            strategy="quick_attack",
            depth=3,
            width=16,
            minimum_chain_count=2,
            chain_weight=35_000.0,
            score_weight=4.0,
            premature_chain_penalty=25.0,
        ),
        WorkerProfile(profile_id=2, name="fire", strategy="fire"),
        WorkerProfile(profile_id=3, name="survival", strategy="survival"),
    )


def smoke_worker_profiles() -> tuple[WorkerProfile, ...]:
    """Small deterministic budgets for tests and pipeline smoke runs."""

    return (
        WorkerProfile(0, "large_chain", "large_chain", depth=2, width=8, minimum_chain_count=3),
        WorkerProfile(1, "quick_attack", "quick_attack", depth=2, width=8, minimum_chain_count=2),
        WorkerProfile(2, "fire", "fire"),
        WorkerProfile(3, "survival", "survival"),
    )


class BeamStrategyWorker:
    """Adapter that applies a profile to the shared beam search engine."""

    def propose(self, context: SearchContext, profile: WorkerProfile) -> SearchProposal:
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
        return SearchProposal(
            action=action,
            profile_id=profile.profile_id,
            profile_name=profile.name,
            strategy=profile.strategy,
            predicted_chain_count=result.chain_count if result is not None else 0,
            predicted_score=result.score_delta if result is not None else 0,
            predicted_attack=(result.score_delta // 70) if result is not None else 0,
            danger=danger,
            elapsed_seconds=(diagnostics.elapsed_seconds if diagnostics is not None else time.perf_counter() - started),
            expanded_nodes=diagnostics.expanded_nodes if diagnostics is not None else 0,
            candidate_value=float(values.get(action, 0.0)),
        )


class ImmediateStrategyWorker:
    """One-ply worker for firing and survival decisions."""

    def propose(self, context: SearchContext, profile: WorkerProfile) -> SearchProposal:
        simulator = context.simulator
        legal = legal_action_indices(simulator) if simulator is not None else _legal_from_info(context.info)
        if not legal:
            return SearchProposal(0, profile.profile_id, profile.name, profile.strategy, 0, 0, 0, 1.0, 0.0, 0, 0.0)

        started = time.perf_counter()
        best_action = legal[0]
        best_result = None
        best_danger = 1.0
        best_value = float("-inf")
        expanded = 0
        for action in legal:
            result, danger = _preview_action(simulator, action)
            expanded += 1
            if result is None:
                continue
            if profile.strategy == "fire":
                value = result.score_delta * 10.0 + result.chain_count * 1_000.0 - danger * 250.0
            else:
                board_value = evaluate_board(_resulting_game(simulator, action))
                value = board_value - danger * 20_000.0 + result.score_delta * 0.25
            if result.game_over:
                value -= 1_000_000.0
            if value > best_value:
                best_action = action
                best_result = result
                best_danger = danger
                best_value = value

        return SearchProposal(
            action=best_action,
            profile_id=profile.profile_id,
            profile_name=profile.name,
            strategy=profile.strategy,
            predicted_chain_count=best_result.chain_count if best_result is not None else 0,
            predicted_score=best_result.score_delta if best_result is not None else 0,
            predicted_attack=(best_result.score_delta // 70) if best_result is not None else 0,
            danger=best_danger,
            elapsed_seconds=time.perf_counter() - started,
            expanded_nodes=expanded,
            candidate_value=best_value,
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
        self._immediate_worker = ImmediateStrategyWorker()
        self.last_proposal: SearchProposal | None = None

    def propose(self, profile_id: int, observation: dict[str, Any], info: dict[str, Any]) -> SearchProposal:
        profile = self.profiles[int(profile_id)]
        context = SearchContext(observation=observation, info=info)
        worker = self._beam_worker if profile.strategy in {"large_chain", "quick_attack"} else self._immediate_worker
        self.last_proposal = worker.propose(context, profile)
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


def estimate_immediate_threat(simulator) -> tuple[int, int]:
    """Return the best one-ply chain and attack without mutating the state."""

    if simulator is None:
        return 0, 0
    best_chain = 0
    best_attack = 0
    for action in legal_action_indices(simulator):
        child = clone_simulator(simulator)
        result = child.step(action_to_placement(action))
        if not result.valid:
            continue
        best_chain = max(best_chain, int(result.chain_count))
        best_attack = max(best_attack, int(result.score_delta // 70))
    return best_chain, best_attack


def _preview_action(simulator, action: int):
    if simulator is None:
        return None, 1.0
    child = clone_simulator(simulator)
    result = child.step(action_to_placement(action))
    if not result.valid:
        return None, 1.0
    return result, board_danger(child.game)


def _resulting_game(simulator, action: int):
    child = clone_simulator(simulator)
    child.step(action_to_placement(action))
    return child.game


def _legal_from_info(info: dict[str, Any]) -> list[int]:
    mask = info.get("action_mask")
    if mask is None:
        return []
    return [index for index, allowed in enumerate(mask) if bool(allowed)]
