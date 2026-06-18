"""Deterministic tactical scenarios and counterfactual teacher data."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agents.strategy_workers import (
    AttackForecast,
    SearchControl,
    SearchProposal,
    StrategyOrchestrator,
    TacticalContext,
    WorkerProfile,
    baseline_search_controls,
    smoke_worker_profiles,
)
from puyo_env.manager_env import ManagerState, build_manager_observation
from puyo_env.versus_env import ScheduledAttack, VersusPuyoEnv
from src.core.constants import PuyoColor
from src.core.puyo import Puyo


@dataclass(frozen=True)
class TacticalScenario:
    name: str
    category: str
    seed: int
    expected_strategy: str
    context: TacticalContext
    own_heights: tuple[int, ...] = (0, 0, 0, 0, 0, 0)
    opponent_heights: tuple[int, ...] = (0, 0, 0, 0, 0, 0)


@dataclass(frozen=True)
class TeacherExample:
    scenario: str
    category: str
    seed: int
    expected_strategy: str
    selected_profile_id: int
    selected_profile_name: str
    reason: str
    board: list[Any]
    next_pairs: list[Any]
    manager_features: list[float]
    counterfactuals: list[dict[str, Any]]
    selected_search_control_id: int = 0
    selected_search_control_name: str = "baseline"
    selected_action_id: int = 0


def _context(
    *,
    own: AttackForecast | None = None,
    opponent: AttackForecast | None = None,
    own_danger: float = 0.2,
    opponent_danger: float = 0.2,
    capacity: int = 24,
    lethal_target: int = 12,
    incoming: int = 0,
    deadline: int = 0,
    counter_target: int = 0,
    max_return: int = 0,
    recommended: str,
    reason: str,
) -> TacticalContext:
    own_forecast = own or AttackForecast()
    opponent_forecast = opponent or AttackForecast()
    return TacticalContext(
        own_forecast=own_forecast,
        opponent_forecast=opponent_forecast,
        own_danger=own_danger,
        opponent_danger=opponent_danger,
        opponent_capacity=capacity,
        lethal_target=lethal_target,
        lethal_margin=own_forecast.immediate_attack - lethal_target,
        incoming_attack=incoming,
        incoming_deadline=deadline,
        counter_target=counter_target,
        max_return_by_deadline=max_return,
        counter_deficit=counter_target - max_return,
        build_potential=own_forecast.medium_attack,
        build_safety=max(0.0, 1.0 - own_danger),
        recommended_strategy=recommended,
        switch_reason=reason,
    )


def default_tactical_scenarios(seed: int = 100) -> tuple[TacticalScenario, ...]:
    """Return the fixed scenario categories required by PUYO-45."""

    return (
        TacticalScenario(
            "safe_build",
            "safe_build",
            seed,
            "build_large",
            _context(
                own=AttackForecast(1, 0, 2, 6, 3),
                recommended="build_large",
                reason="safe board with no urgent attack",
            ),
            (2, 3, 2, 3, 2, 3),
            (2, 2, 3, 2, 3, 2),
        ),
        TacticalScenario(
            "lethal_punish",
            "punish",
            seed + 1,
            "punish",
            _context(
                own=AttackForecast(3, 14, 14, 14, 1),
                opponent_danger=0.78,
                capacity=10,
                lethal_target=10,
                recommended="punish",
                reason="immediate attack clears the lethal target",
            ),
            (4, 5, 4, 5, 4, 5),
            (9, 9, 10, 9, 10, 9),
        ),
        TacticalScenario(
            "counter_available",
            "counter",
            seed + 2,
            "counter",
            _context(
                own=AttackForecast(2, 4, 12, 16, 2),
                own_danger=0.68,
                incoming=10,
                deadline=2,
                counter_target=12,
                max_return=12,
                recommended="counter",
                reason="incoming attack can be canceled before arrival",
            ),
            (7, 8, 7, 8, 7, 8),
            (5, 5, 6, 5, 6, 5),
        ),
        TacticalScenario(
            "counter_impossible",
            "counter_impossible",
            seed + 3,
            "survival",
            _context(
                own=AttackForecast(1, 0, 2, 4, 3),
                own_danger=0.76,
                incoming=18,
                deadline=1,
                counter_target=20,
                max_return=0,
                recommended="survival",
                reason="deadline attack exceeds estimated return",
            ),
            (9, 10, 9, 10, 9, 10),
            (5, 5, 5, 5, 5, 5),
        ),
        TacticalScenario(
            "high_danger_survival",
            "survival",
            seed + 4,
            "survival",
            _context(
                own=AttackForecast(1, 0, 0, 1, 3),
                own_danger=0.9,
                recommended="survival",
                reason="choke danger requires immediate survival",
            ),
            (10, 11, 10, 11, 10, 11),
            (4, 4, 4, 4, 4, 4),
        ),
        TacticalScenario(
            "recovered_build",
            "recovery",
            seed + 5,
            "build_large",
            _context(
                own=AttackForecast(1, 0, 3, 8, 3),
                own_danger=0.35,
                recommended="build_large",
                reason="danger has cleared and construction can resume",
            ),
            (3, 4, 3, 4, 3, 4),
            (4, 4, 5, 4, 5, 4),
        ),
    )


def apply_tactical_scenario(env: VersusPuyoEnv, scenario: TacticalScenario):
    colors = (PuyoColor.RED, PuyoColor.BLUE, PuyoColor.GREEN, PuyoColor.YELLOW)
    for agent, heights in (
        ("player_0", scenario.own_heights),
        ("player_1", scenario.opponent_heights),
    ):
        field = env.player_states[agent].simulator.game.field
        for x, height in enumerate(heights):
            for y in range(height):
                field.place_puyo(x, y, Puyo(colors[(x + y * 2) % len(colors)]))
    if scenario.context.incoming_attack > 0:
        env.player_states["player_0"].incoming_attacks = [
            ScheduledAttack(
                amount=scenario.context.incoming_attack,
                arrival_step=max(1, scenario.context.incoming_deadline),
                source_agent="player_1",
                created_step=0,
            )
        ]
    observations, infos = env._observations_and_infos()
    info = dict(infos["player_0"])
    info["tactical_context"] = scenario.context
    return observations, info


def _canonical(strategy: str) -> str:
    return {
        "large_chain": "build_large",
        "quick_attack": "build_budget",
        "fire": "fire_max",
    }.get(strategy, strategy)


def _teacher_value(
    scenario: TacticalScenario,
    profile: WorkerProfile,
    proposal: SearchProposal,
) -> float:
    strategy = _canonical(profile.strategy)
    alignment = 1_000_000.0 if strategy == scenario.expected_strategy else 0.0
    target_bonus = 0.0
    if scenario.expected_strategy in {"punish", "counter"}:
        target_bonus = 250_000.0 if proposal.predicted_attack >= proposal.target_attack else 0.0
    if scenario.expected_strategy == "survival":
        target_bonus = (1.0 - proposal.danger) * 100_000.0
    if scenario.expected_strategy == "build_large":
        target_bonus = proposal.candidate_value * 0.01
    return (
        alignment
        + target_bonus
        + proposal.predicted_attack * 1_000.0
        - proposal.danger * 10_000.0
        - proposal.elapsed_seconds * 100.0
    )


def generate_teacher_examples(
    *,
    scenarios: tuple[TacticalScenario, ...] | None = None,
    profiles: tuple[WorkerProfile, ...] | None = None,
    search_controls: tuple[SearchControl, ...] | None = None,
) -> list[TeacherExample]:
    selected_scenarios = scenarios or default_tactical_scenarios()
    selected_profiles = profiles or smoke_worker_profiles()
    selected_controls = search_controls or baseline_search_controls()
    examples: list[TeacherExample] = []
    for scenario in selected_scenarios:
        env = VersusPuyoEnv(seed=scenario.seed, max_steps=40)
        env.reset(seed=scenario.seed)
        observations, info = apply_tactical_scenario(env, scenario)
        manager_observation = build_manager_observation(
            observations["player_0"],
            info,
            ManagerState(
                profile_counts=[0] * len(selected_profiles),
                search_control_counts=[0] * len(selected_controls),
            ),
            len(selected_profiles),
            len(selected_controls),
        )
        orchestrator = StrategyOrchestrator(selected_profiles)
        results = []
        for profile in selected_profiles:
            for control in selected_controls:
                proposal = orchestrator.propose(
                    profile.profile_id,
                    observations["player_0"],
                    info,
                    control,
                )
                value = _teacher_value(scenario, profile, proposal) - control.cost_penalty * 10_000.0
                results.append((value, profile, control, proposal))
        _, best_profile, best_control, _ = max(results, key=lambda item: item[0])
        selected_action_id = best_profile.profile_id * len(selected_controls) + best_control.control_id
        examples.append(
            TeacherExample(
                scenario=scenario.name,
                category=scenario.category,
                seed=scenario.seed,
                expected_strategy=scenario.expected_strategy,
                selected_profile_id=best_profile.profile_id,
                selected_profile_name=best_profile.name,
                selected_search_control_id=best_control.control_id,
                selected_search_control_name=best_control.name,
                selected_action_id=selected_action_id,
                reason=scenario.context.switch_reason,
                board=manager_observation["board"].tolist(),
                next_pairs=manager_observation["next_pairs"].tolist(),
                manager_features=manager_observation["manager_features"].tolist(),
                counterfactuals=[
                    {
                        "profile_id": profile.profile_id,
                        "profile_name": profile.name,
                        "search_control_id": control.control_id,
                        "search_control_name": control.name,
                        "action_id": profile.profile_id * len(selected_controls) + control.control_id,
                        "teacher_value": value,
                        "predicted_attack": proposal.predicted_attack,
                        "target_attack": proposal.target_attack,
                        "danger": proposal.danger,
                        "elapsed_seconds": proposal.elapsed_seconds,
                        "expanded_nodes": proposal.expanded_nodes,
                    }
                    for value, profile, control, proposal in results
                ],
            )
        )
        env.close()
    return examples


def write_teacher_dataset(path: str | Path, examples: list[TeacherExample]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([asdict(example) for example in examples], indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_teacher_dataset(path: str | Path) -> list[TeacherExample]:
    values = json.loads(Path(path).read_text(encoding="utf-8"))
    examples = []
    for value in values:
        if "selected_action_id" not in value:
            value["selected_action_id"] = int(value["selected_profile_id"])
        examples.append(TeacherExample(**value))
    return examples


def scenario_selection_accuracy(policy, scenarios: tuple[TacticalScenario, ...] | None = None) -> float:
    selected = scenarios or default_tactical_scenarios()
    correct = 0
    for scenario in selected:
        env = VersusPuyoEnv(seed=scenario.seed, max_steps=2)
        env.reset(seed=scenario.seed)
        observations, info = apply_tactical_scenario(env, scenario)
        policy.select_action(observations["player_0"], info)
        current = getattr(policy, "current_profile_name", None)
        if current == scenario.expected_strategy:
            correct += 1
        env.close()
    return correct / max(1, len(selected))
