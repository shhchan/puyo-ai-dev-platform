"""PUYO-180 offline K-best oracle for build-then-fire capability evaluation.

The oracle may inspect the authoritative future queue for an evaluation seed,
but it can execute only a legal root action already present in the worker's
Proposal v2 K-best set.  The private future view is never added to runtime
observations, Candidate Ranker tensors, or proposal evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from agents.beam_search import (
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotentialBudget,
    clone_simulator,
)
from agents.compact_search import CompactSearchState
from agents.long_horizon_search import (
    FUTURE_SAMPLING_SCHEMA_VERSION,
    RUNTIME_PROFILE,
    long_horizon_profile,
)
from agents.worker_proposals import (
    WORKER_PROPOSAL_SCHEMA_VERSION,
    WorkerProposalBatch,
    WorkerProposalCandidate,
    build_worker_proposal_batch,
)
from eval.v1_7_benchmark import _observation, _runtime_info
from puyo_env.actions import (
    action_to_placement,
    legal_action_indices,
    legal_action_mask,
)
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor
from src.core.headless import HeadlessPuyoSimulator
from src.core.ojama import convert_score_to_ojama
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


ORACLE_CONFIG_SCHEMA_VERSION = "puyo.k_best_offline_oracle_config.v1"
ORACLE_FUTURE_SCHEMA_VERSION = "puyo.k_best_offline_oracle_future.v1"
ORACLE_CANDIDATE_SCHEMA_VERSION = "puyo.k_best_offline_oracle_candidate.v1"
ORACLE_DECISION_SCHEMA_VERSION = "puyo.k_best_offline_oracle_decision.v1"
ORACLE_TRAJECTORY_SCHEMA_VERSION = "puyo.k_best_offline_oracle_trajectory.v1"
ORACLE_SUITE_SCHEMA_VERSION = "puyo.k_best_offline_oracle_suite.v1"
ORACLE_DETERMINISM_SCHEMA_VERSION = "puyo.k_best_offline_oracle_determinism.v1"
ORACLE_MANIFEST_SCHEMA_VERSION = "puyo.k_best_offline_oracle_manifest.v1"

DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-k-best-oracle"
DEFAULT_BUILD_STEPS = 40
DEFAULT_FIRE_STEPS = 6
DEFAULT_TARGET_CHAIN = 10
DEFAULT_SELECTORS = (
    "offline_oracle",
    "compatibility_rank_0",
    "legacy_capability_selector",
)
PREMATURE_FIRE_CLASSES = (
    "unavoidable",
    "candidate_limited",
    "oracle_error",
    "none",
)
FAILURE_CLASSES = (
    "candidate_absence",
    "evaluator_overestimate",
    "premature_fire",
    "dead_end",
    "game_over",
    "fire_window_timeout",
)


def _canonical(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _canonical(item)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if hasattr(value, "tolist"):
        return _canonical(value.tolist())
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("oracle artifacts require finite numeric values")
        return float(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    return repr(value)


def _digest(value: Any, *, prefix: str) -> str:
    encoded = json.dumps(
        _canonical(value),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            _canonical(payload),
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _without_latency(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _without_latency(item)
            for key, item in value.items()
            if "latency" not in str(key)
        }
    if isinstance(value, list):
        return [_without_latency(item) for item in value]
    return value


def _nested(
    value: Mapping[str, Any] | None,
    path: str,
    default: Any = None,
) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return default
        current = current[part]
    return current


@dataclass(frozen=True)
class OracleSearchConfiguration:
    """Search and phase limits for one reproducible oracle suite."""

    profile: str
    depth: int
    width: int
    scenarios: int
    max_expanded_nodes: int
    candidate_limit: int = 8
    target_chain_count: int = DEFAULT_TARGET_CHAIN
    build_steps: int = DEFAULT_BUILD_STEPS
    fire_steps: int = DEFAULT_FIRE_STEPS
    preview_steps: int = DEFAULT_FIRE_STEPS
    build_potential_budget: BuildPotentialBudget = BuildPotentialBudget()
    config_id: str = ""

    def __post_init__(self) -> None:
        if min(
            self.depth,
            self.width,
            self.scenarios,
            self.max_expanded_nodes,
            self.candidate_limit,
            self.target_chain_count,
            self.build_steps,
            self.fire_steps,
            self.preview_steps,
        ) <= 0:
            raise ValueError("oracle search and phase limits must be positive")
        if self.candidate_limit != 8:
            raise ValueError("PUYO-180 oracle requires Proposal v2 K=8")
        if not self.config_id:
            object.__setattr__(
                self,
                "config_id",
                (
                    f"{self.profile}-d{self.depth}-w{self.width}-"
                    f"s{self.scenarios}-n{self.max_expanded_nodes}-k8"
                ),
            )

    @classmethod
    def for_profile(
        cls,
        profile: str = RUNTIME_PROFILE,
        **overrides: Any,
    ) -> "OracleSearchConfiguration":
        definition = long_horizon_profile(profile)
        values = {
            "profile": definition.name,
            "depth": definition.depth,
            "width": definition.width,
            "scenarios": definition.scenarios,
            "max_expanded_nodes": definition.max_expanded_nodes,
            "candidate_limit": definition.candidate_limit,
        }
        values.update(overrides)
        return cls(**values)

    def beam_config(self) -> BeamSearchConfig:
        return BeamSearchConfig.for_profile(
            self.profile,
            depth=self.depth,
            width=self.width,
            scenarios=self.scenarios,
            max_expanded_nodes=self.max_expanded_nodes,
            candidate_limit=self.candidate_limit,
            minimum_chain_count=self.target_chain_count,
            potential_probe_budget=self.candidate_limit,
            build_potential_budget=self.build_potential_budget,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORACLE_CONFIG_SCHEMA_VERSION,
            "config_id": self.config_id,
            "profile": self.profile,
            "search": {
                "depth": int(self.depth),
                "width": int(self.width),
                "scenarios": int(self.scenarios),
                "max_expanded_nodes": int(self.max_expanded_nodes),
                "candidate_limit": int(self.candidate_limit),
                "build_potential_budget": self.build_potential_budget.to_dict(),
            },
            "trajectory": {
                "target_chain_count": int(self.target_chain_count),
                "maximum_build_steps": int(self.build_steps),
                "maximum_fire_steps": int(self.fire_steps),
                "oracle_preview_steps": int(self.preview_steps),
            },
            "responsibility": (
                "offline candidate-set upper bound; not a runtime policy or "
                "learned-policy promotion result"
            ),
            "selection_constraint": (
                "executed actions must be legal roots in the current Proposal v2 K-best"
            ),
            "future_scope": (
                "authoritative seed future is visible only to the offline oracle evaluator"
            ),
        }


@dataclass(frozen=True)
class OracleFutureView:
    """Private authoritative queue view used only for counterfactual scoring."""

    pairs: tuple[tuple[str, str], ...]
    known_pair_count: int
    source_seed: str
    schema_version: str = ORACLE_FUTURE_SCHEMA_VERSION

    @property
    def digest(self) -> str:
        return _digest(
            {
                "pairs": self.pairs,
                "known_pair_count": self.known_pair_count,
                "source_seed": self.source_seed,
            },
            prefix="oracle-future",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": "offline_oracle_evaluator_only",
            "source": "authoritative_simulator_sequence_state",
            "source_seed": self.source_seed,
            "known_pair_count": int(self.known_pair_count),
            "pair_count": len(self.pairs),
            "pairs": [list(pair) for pair in self.pairs],
            "digest": self.digest,
        }


def authoritative_future_view(
    simulator: HeadlessPuyoSimulator,
    *,
    pair_count: int,
) -> OracleFutureView:
    """Snapshot current/NEXT and the exact hidden continuation without mutation."""

    if pair_count <= 0:
        raise ValueError("oracle future pair count must be positive")
    cloned = clone_simulator(simulator)
    game = cloned.game
    pairs = []
    if game.current_puyo_1 is not None and game.current_puyo_2 is not None:
        pairs.append(
            (
                game.current_puyo_1.color.name,
                game.current_puyo_2.color.name,
            )
        )
    pairs.extend(
        (pair[0].color.name, pair[1].color.name)
        for pair in tuple(game.next_puyo_queue)
    )
    while len(pairs) < pair_count:
        pair = game.puyo_sequence.next_pair()
        pairs.append((pair[0].color.name, pair[1].color.name))
    return OracleFutureView(
        pairs=tuple(pairs[:pair_count]),
        known_pair_count=min(3, len(pairs)),
        source_seed=repr(getattr(game.puyo_sequence, "seed", None)),
    )


def _board_metrics(simulator: HeadlessPuyoSimulator) -> dict[str, Any]:
    state = CompactSearchState.from_simulator(simulator)
    same_color_edges = 0
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            color = state.color_at(x, y)
            if color in {PuyoColor.EMPTY, PuyoColor.OJAMA, PuyoColor.WALL}:
                continue
            if state.color_at(x + 1, y) == color:
                same_color_edges += 1
            if state.color_at(x, y + 1) == color:
                same_color_edges += 1
    maximum_height = max(state.column_heights, default=0)
    danger = min(1.0, maximum_height / float(GRID_HEIGHT))
    roughness = sum(
        abs(left - right)
        for left, right in zip(state.column_heights, state.column_heights[1:])
    )
    value = (
        same_color_edges * 100.0
        + state.cell_count * 2.0
        - sum(height * height for height in state.column_heights)
        - roughness * 3.0
        - (1_000_000.0 if state.game_over else 0.0)
    )
    return {
        "board_digest": hashlib.sha256(state.to_bytes()).hexdigest(),
        "cell_count": int(state.cell_count),
        "column_heights": list(state.column_heights),
        "same_color_edges": int(same_color_edges),
        "roughness": int(roughness),
        "danger": float(danger),
        "value": float(value),
    }


def _structural_prediction(
    candidate: WorkerProposalCandidate,
) -> dict[str, Any]:
    structural = (
        candidate.evidence.structural_chain
        if candidate.evidence is not None
        else None
    )
    features = (
        structural.get("features")
        if isinstance(structural, Mapping)
        and isinstance(structural.get("features"), Mapping)
        else {}
    )
    trigger = (
        features.get("trigger")
        if isinstance(features.get("trigger"), Mapping)
        else {}
    )
    quiescence = (
        structural.get("quiescence")
        if isinstance(structural, Mapping)
        and isinstance(structural.get("quiescence"), Mapping)
        else {}
    )
    best = (
        quiescence.get("best")
        if isinstance(quiescence.get("best"), Mapping)
        else {}
    )
    trigger_payload = (
        best.get("trigger")
        if isinstance(best.get("trigger"), Mapping)
        else {}
    )
    placements = [
        [int(cell[0]), int(cell[1])]
        for cell in best.get("placements", ())
        if isinstance(cell, Sequence) and len(cell) == 2
    ]
    anchors = [
        [int(cell[0]), int(cell[1])]
        for cell in best.get("anchor_cells", ())
        if isinstance(cell, Sequence) and len(cell) == 2
    ]
    trigger_identity = None
    if best:
        trigger_identity = _digest(
            {
                "color": best.get("trigger_color"),
                "placements": placements,
                "anchor_cells": anchors,
            },
            prefix="trigger",
        )
    return {
        "evaluation_status": (
            structural.get("evaluation_status")
            if isinstance(structural, Mapping)
            else "not_evaluated"
        ),
        "score": (
            float(structural["score"])
            if isinstance(structural, Mapping)
            and structural.get("score") is not None
            else None
        ),
        "potential_chain_count": int(
            trigger.get(
                "potential_chain_count",
                candidate.predicted_chain_count,
            )
            or 0
        ),
        "trigger_reachable": bool(trigger.get("reachable", False)),
        "trigger_protection": float(trigger.get("protection", 0.0) or 0.0),
        "required_key_count": trigger.get("required_key_count"),
        "trigger_color": best.get("trigger_color"),
        "trigger_column": trigger_payload.get("column", trigger.get("column")),
        "trigger_height": trigger_payload.get("height", trigger.get("height")),
        "trigger_cells": placements,
        "anchor_cells": anchors,
        "trigger_id": trigger_identity,
        "structural_dead_end": bool(features.get("structural_dead_end", False)),
    }


def evaluate_candidate_with_authoritative_future(
    simulator: HeadlessPuyoSimulator,
    candidate: WorkerProposalCandidate,
    *,
    target_chain_count: int,
    preview_steps: int,
) -> dict[str, Any]:
    """Replay only the candidate's recorded plan against the real seed queue."""

    rollout = clone_simulator(simulator)
    steps = []
    maximum_chain = 0
    first_fire_depth = None
    target_fire_depth = None
    for depth, action in enumerate(candidate.action_sequence[:preview_steps], start=1):
        result = rollout.step(action_to_placement(int(action)))
        maximum_chain = max(maximum_chain, int(result.chain_count))
        if result.chain_count > 0 and first_fire_depth is None:
            first_fire_depth = depth
        if result.chain_count >= target_chain_count and target_fire_depth is None:
            target_fire_depth = depth
        steps.append(
            {
                "depth": int(depth),
                "action": int(action),
                "valid": bool(result.valid),
                "game_over": bool(result.game_over),
                "actual_chain_count": int(result.chain_count),
                "score_delta": int(result.score_delta),
            }
        )
        if not result.valid or result.game_over or result.chain_count > 0:
            break
    immediate = steps[0] if steps else {
        "valid": False,
        "game_over": True,
        "actual_chain_count": 0,
        "score_delta": 0,
    }
    structural = _structural_prediction(candidate)
    return {
        "schema_version": ORACLE_CANDIDATE_SCHEMA_VERSION,
        "candidate_id": candidate.candidate_id,
        "candidate_index": int(candidate.rank),
        "root_action": int(candidate.root_action),
        "action_sequence": [int(action) for action in candidate.action_sequence],
        "candidate_value": float(candidate.candidate_value),
        "search_predicted_chain_count": int(candidate.predicted_chain_count),
        "structural_prediction": structural,
        "immediate_outcome": dict(immediate),
        "authoritative_future_rollout": {
            "scope": "evaluation_only_candidate_plan_replay",
            "maximum_chain_count": int(maximum_chain),
            "first_fire_depth": first_fire_depth,
            "target_fire_depth": target_fire_depth,
            "steps": steps,
            "final_board": _board_metrics(rollout),
        },
        "danger": float(candidate.danger),
        "continuation_flexibility": float(candidate.continuation_flexibility),
        "trigger_recoverable": bool(
            candidate.trigger_recoverability.get("recoverable", False)
        ),
    }


def _trigger_continuity(
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    *,
    target_fired: bool,
) -> dict[str, Any]:
    previous_id = None if previous is None else previous.get("trigger_id")
    current_id = current.get("trigger_id")
    exact_match = bool(previous_id and previous_id == current_id)
    previous_potential = (
        0 if previous is None else int(previous.get("potential_chain_count", 0))
    )
    current_potential = int(current.get("potential_chain_count", 0))
    maintained = bool(
        previous is None
        or target_fired
        or exact_match
        or (
            current.get("trigger_reachable")
            and current_potential >= previous_potential
        )
    )
    return {
        "previous_trigger_id": previous_id,
        "current_trigger_id": current_id,
        "exact_match": exact_match,
        "previous_potential_chain_count": previous_potential,
        "current_potential_chain_count": current_potential,
        "maintained": maintained,
        "lost": bool(previous is not None and not maintained),
    }


def _oracle_key(
    evaluation: Mapping[str, Any],
    *,
    phase: str,
    previous_trigger: Mapping[str, Any] | None,
    target_chain_count: int,
) -> tuple[Any, ...]:
    immediate = evaluation["immediate_outcome"]
    rollout = evaluation["authoritative_future_rollout"]
    structural = evaluation["structural_prediction"]
    valid = bool(immediate["valid"])
    surviving = valid and not bool(immediate["game_over"])
    immediate_chain = int(immediate["actual_chain_count"])
    maximum_chain = int(rollout["maximum_chain_count"])
    target = maximum_chain >= target_chain_count
    premature = 0 < immediate_chain < target_chain_count
    continuity = _trigger_continuity(
        previous_trigger,
        structural,
        target_fired=target,
    )
    structural_score = (
        -1.0e18
        if structural.get("score") is None
        else float(structural["score"])
    )
    common = (
        int(valid),
        int(surviving),
        int(target),
        int(maximum_chain),
        int(structural.get("potential_chain_count", 0)),
        int(bool(structural.get("trigger_reachable"))),
        int(continuity["maintained"]),
        structural_score,
        float(evaluation["continuation_flexibility"]),
        -float(rollout["final_board"]["danger"]),
        float(rollout["final_board"]["value"]),
        float(evaluation["candidate_value"]),
        -int(evaluation["candidate_index"]),
    )
    if phase == "fire":
        target_depth = rollout.get("target_fire_depth")
        return (
            int(target),
            int(surviving),
            int(maximum_chain),
            -int(target_depth or 10_000),
            *common[4:],
        )
    return (
        int(surviving),
        int(not premature),
        int(not (0 < maximum_chain < target_chain_count)),
        *common[2:],
    )


def _oracle_scalar(
    evaluation: Mapping[str, Any],
    *,
    phase: str,
    target_chain_count: int,
) -> float:
    immediate = evaluation["immediate_outcome"]
    rollout = evaluation["authoritative_future_rollout"]
    structural = evaluation["structural_prediction"]
    maximum_chain = int(rollout["maximum_chain_count"])
    target = maximum_chain >= target_chain_count
    valid_survival = bool(immediate["valid"]) and not bool(immediate["game_over"])
    immediate_chain = int(immediate["actual_chain_count"])
    premature = 0 < immediate_chain < target_chain_count
    structural_score = float(structural.get("score") or 0.0)
    value = (
        int(valid_survival) * 1.0e15
        + int(target) * 1.0e14
        + maximum_chain * 1.0e11
        + int(structural.get("potential_chain_count", 0)) * 1.0e9
        + int(bool(structural.get("trigger_reachable"))) * 1.0e8
        + structural_score
        + float(rollout["final_board"]["value"])
    )
    if phase == "build" and premature:
        value -= 5.0e14
    return float(value)


def select_offline_oracle_candidate(
    evaluations: Sequence[Mapping[str, Any]],
    *,
    phase: str,
    previous_trigger: Mapping[str, Any] | None,
    target_chain_count: int,
) -> int | None:
    """Return an index into the current K-best; no legal action is added."""

    if phase not in {"build", "fire"}:
        raise ValueError(f"unsupported oracle phase: {phase}")
    if not evaluations:
        return None
    return max(
        range(len(evaluations)),
        key=lambda index: _oracle_key(
            evaluations[index],
            phase=phase,
            previous_trigger=previous_trigger,
            target_chain_count=target_chain_count,
        ),
    )


def select_legacy_capability_candidate(
    batch: WorkerProposalBatch,
    root_outcomes: Mapping[int, Mapping[str, Any]],
    *,
    max_chain_so_far: int,
    target_chain_count: int,
) -> int | None:
    """Pinned copy of the pre-PUYO-180 handwritten capability selector."""

    indexed = [
        (index, candidate, root_outcomes.get(candidate.root_action, {}))
        for index, candidate in enumerate(batch.candidates)
        if candidate is not None
    ]
    surviving = [
        item
        for item in indexed
        if item[2].get("valid") and not item[2].get("game_over")
    ]
    pool = surviving or indexed
    if not pool:
        return None
    if max_chain_so_far < target_chain_count:
        target_fires = [
            item
            for item in pool
            if int(item[2].get("chain_count", 0)) >= target_chain_count
        ]
        if target_fires:
            pool = target_fires
        else:
            quiet = [
                item
                for item in pool
                if int(item[2].get("chain_count", 0)) == 0
            ]
            if quiet:
                pool = quiet

    def key(item: tuple[int, WorkerProposalCandidate, Mapping[str, Any]]):
        index, candidate, outcome = item
        potential = candidate.build_potential.get("predicted_chain_potential")
        return (
            int(outcome.get("chain_count", 0)),
            int(candidate.predicted_chain_count),
            -1.0 if potential is None else float(potential),
            bool(candidate.trigger_recoverability.get("recoverable")),
            float(candidate.continuation_flexibility),
            float(candidate.candidate_value),
            -int(index),
        )

    return int(max(pool, key=key)[0])


def classify_build_premature_fire(
    *,
    selected_chain_count: int,
    target_chain_count: int,
    candidate_root_outcomes: Sequence[Mapping[str, Any]],
    legal_root_outcomes: Sequence[Mapping[str, Any]],
) -> str:
    """Classify an observed 1..target-1 build-phase fire."""

    if not 0 < selected_chain_count < target_chain_count:
        return "none"

    def safe(outcome: Mapping[str, Any]) -> bool:
        chain = int(outcome.get("chain_count", 0))
        return bool(
            outcome.get("valid")
            and not outcome.get("game_over")
            and (chain == 0 or chain >= target_chain_count)
        )

    if any(safe(outcome) for outcome in candidate_root_outcomes):
        return "oracle_error"
    if any(safe(outcome) for outcome in legal_root_outcomes):
        return "candidate_limited"
    return "unavoidable"


def _root_outcomes(
    simulator: HeadlessPuyoSimulator,
) -> dict[int, dict[str, Any]]:
    outcomes = {}
    for action in legal_action_indices(simulator):
        child = clone_simulator(simulator)
        result = child.step(action_to_placement(int(action)))
        outcomes[int(action)] = {
            "action": int(action),
            "valid": bool(result.valid),
            "game_over": bool(result.game_over),
            "chain_count": int(result.chain_count),
            "score_delta": int(result.score_delta),
        }
    return outcomes


def _rank_zero_index(batch: WorkerProposalBatch) -> int | None:
    if batch.selected_index is not None and batch.candidate_mask[batch.selected_index]:
        return int(batch.selected_index)
    return next(
        (index for index, present in enumerate(batch.candidate_mask) if present),
        None,
    )


def _selector_comparison(
    batch: WorkerProposalBatch,
    evaluations: Sequence[dict[str, Any]],
    root_outcomes: Mapping[int, Mapping[str, Any]],
    *,
    phase: str,
    previous_trigger: Mapping[str, Any] | None,
    max_chain_so_far: int,
    target_chain_count: int,
) -> tuple[dict[str, Any], dict[str, int | None]]:
    oracle_index = select_offline_oracle_candidate(
        evaluations,
        phase=phase,
        previous_trigger=previous_trigger,
        target_chain_count=target_chain_count,
    )
    indices = {
        "offline_oracle": oracle_index,
        "compatibility_rank_0": _rank_zero_index(batch),
        "legacy_capability_selector": select_legacy_capability_candidate(
            batch,
            root_outcomes,
            max_chain_so_far=max_chain_so_far,
            target_chain_count=target_chain_count,
        ),
    }
    for evaluation in evaluations:
        evaluation["oracle_lexicographic_key"] = list(
            _oracle_key(
                evaluation,
                phase=phase,
                previous_trigger=previous_trigger,
                target_chain_count=target_chain_count,
            )
        )
        evaluation["oracle_value"] = _oracle_scalar(
            evaluation,
            phase=phase,
            target_chain_count=target_chain_count,
        )
    oracle_value = (
        None
        if oracle_index is None
        else float(evaluations[oracle_index]["oracle_value"])
    )
    comparison = {}
    for name, index in indices.items():
        selected = None if index is None else evaluations[index]
        selected_value = (
            None if selected is None else float(selected["oracle_value"])
        )
        comparison[name] = {
            "status": "evaluated",
            "selected_index": index,
            "selected_candidate_id": (
                None if selected is None else selected["candidate_id"]
            ),
            "selected_action": None if selected is None else selected["root_action"],
            "oracle_value": selected_value,
            "selection_regret": (
                None
                if oracle_value is None or selected_value is None
                else float(oracle_value - selected_value)
            ),
            "actual_future_max_chain": (
                None
                if selected is None
                else int(
                    selected["authoritative_future_rollout"][
                        "maximum_chain_count"
                    ]
                )
            ),
        }
    comparison["learned_selector"] = {
        "status": "not_evaluated",
        "reason": "Candidate Ranker/PPO training is out of scope for PUYO-180",
        "input_contract": {
            "proposal_id": batch.proposal_id,
            "ranker_input_digest": batch.ranker_input.deterministic_digest,
            "oracle_future_fields_present": False,
        },
    }
    return comparison, indices


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).lower() in {
                "oracle_future",
                "authoritative_future",
                "actual_future",
            }:
                return True
            if _contains_forbidden_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_key(item) for item in value)
    return False


def verify_oracle_input_isolation(
    observation: Mapping[str, Any],
    batch: WorkerProposalBatch,
    future_view: OracleFutureView,
) -> dict[str, Any]:
    """Prove the private future payload is absent from learned/runtime inputs."""

    ranker = batch.ranker_input.to_dict()
    observation_payload = _canonical(observation)
    ranker_text = json.dumps(ranker, sort_keys=True, separators=(",", ":"))
    observation_text = json.dumps(
        observation_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest_absent = (
        future_view.digest not in ranker_text
        and future_view.digest not in observation_text
    )
    forbidden_absent = not _contains_forbidden_key(
        {"ranker_input": ranker, "ppo_observation": observation_payload}
    )
    return {
        "passed": bool(digest_absent and forbidden_absent),
        "scope": ["Candidate Ranker input", "PPO/runtime observation"],
        "oracle_future_digest_absent": digest_absent,
        "oracle_future_keys_absent": forbidden_absent,
        "ranker_input_digest": batch.ranker_input.deterministic_digest,
        "ppo_observation_digest": _digest(
            observation_payload,
            prefix="ppo-observation",
        ),
    }


def _compact_proposal(batch: WorkerProposalBatch) -> dict[str, Any]:
    shared = batch.shared_context
    return {
        "schema_version": batch.schema_version,
        "proposal_id": batch.proposal_id,
        "decision_id": batch.decision_id,
        "candidate_limit": int(batch.candidate_limit),
        "candidate_count": int(batch.candidate_count),
        "candidate_mask": list(batch.candidate_mask),
        "legal_action_mask": list(batch.legal_action_mask),
        "compatibility_selected_index": batch.selected_index,
        "candidate_ids": [
            None if candidate is None else candidate.candidate_id
            for candidate in batch.candidates
        ],
        "root_actions": [
            None if candidate is None else int(candidate.root_action)
            for candidate in batch.candidates
        ],
        "ranker_input": batch.ranker_input.to_dict(),
        "deterministic_digest": batch.deterministic_digest,
        "sampled_future": (
            None
            if shared is None
            else {
                "schema_version": FUTURE_SAMPLING_SCHEMA_VERSION,
                "scenario_digest": shared.scenario_digest,
                "scenario_mask": list(shared.scenario_mask),
                "future_sampling": dict(
                    shared.search_config.get("future_sampling", {})
                ),
            }
        ),
    }


def _proposal_batch(
    policy: BeamSearchPolicy,
    simulator: HeadlessPuyoSimulator,
    opponent: HeadlessPuyoSimulator,
    *,
    step_count: int,
    configuration: OracleSearchConfiguration,
    score_carry: int,
    sent_ojama: int,
) -> tuple[Mapping[str, Any], WorkerProposalBatch]:
    maximum_steps = configuration.build_steps + configuration.fire_steps
    info = _runtime_info(
        simulator,
        opponent,
        step_count=step_count,
        max_steps=maximum_steps,
        score_carry=score_carry,
        sent_ojama=sent_ojama,
    )
    observation = _observation(
        simulator,
        opponent,
        step_count=step_count,
        max_steps=maximum_steps,
        sent_ojama=sent_ojama,
    )
    candidates = policy.generate_candidates(dict(observation), info)
    diagnostics = policy.last_diagnostics
    if diagnostics is None:
        raise RuntimeError("offline oracle requires proposal diagnostics")
    legal = legal_action_mask(simulator)
    selected_action = (
        int(candidates[0].action)
        if candidates
        else next((index for index, allowed in enumerate(legal) if allowed), 0)
    )
    batch = build_worker_proposal_batch(
        candidates,
        selected_action=selected_action,
        candidate_limit=configuration.candidate_limit,
        legal_action_mask=legal,
        profile_id=0,
        profile_name=f"offline-oracle-{configuration.profile}",
        strategy="build_large",
        simulator=simulator,
        score_carry=score_carry,
        incoming_attack=0,
        search_latency_ms=diagnostics.elapsed_seconds * 1_000.0,
        expanded_nodes=diagnostics.expanded_nodes,
        scenario_budget=diagnostics.scenario_budget,
        worker_deadline_status={
            "status": "offline_oracle_observational",
            "budget_ms": None,
            "overrun": False,
            "source": "PUYO-180",
        },
        schema_version=WORKER_PROPOSAL_SCHEMA_VERSION,
    )
    return observation, batch


def _trajectory_summary(
    *,
    seed: int,
    selector: str,
    configuration: OracleSearchConfiguration,
    decisions: Sequence[Mapping[str, Any]],
    termination_reason: str,
    phase_boundary: Mapping[str, Any] | None,
) -> dict[str, Any]:
    maximum_actual_chain = max(
        (
            int(decision["outcome"]["actual_chain_count"])
            for decision in decisions
        ),
        default=0,
    )
    maximum_fire_chain = max(
        (
            int(decision["outcome"]["actual_chain_count"])
            for decision in decisions
            if decision["phase"] == "fire"
        ),
        default=0,
    )
    maximum_search_chain = max(
        (
            int(decision["candidate_set_metrics"]["maximum_search_chain"])
            for decision in decisions
        ),
        default=0,
    )
    maximum_structural = max(
        (
            int(decision["candidate_set_metrics"]["maximum_structural_potential"])
            for decision in decisions
        ),
        default=0,
    )
    premature = Counter(
        str(decision["premature_fire"]["classification"])
        for decision in decisions
    )
    target_achieved = maximum_actual_chain >= configuration.target_chain_count
    candidate_absence = not any(
        bool(decision["candidate_set_metrics"]["target_path_available"])
        for decision in decisions
    )
    calibration_failure = bool(
        not target_achieved
        and max(maximum_search_chain, maximum_structural)
        >= configuration.target_chain_count
    )
    dead_end = any(
        bool(decision["candidate_set_metrics"]["dead_end"])
        for decision in decisions
    )
    observed_failures = {
        "candidate_absence": candidate_absence,
        "evaluator_overestimate": calibration_failure,
        "premature_fire": any(
            decision["premature_fire"]["classification"] != "none"
            for decision in decisions
        ),
        "dead_end": dead_end,
        "game_over": termination_reason == "game_over",
        "fire_window_timeout": termination_reason == "fire_window_timeout",
    }
    if target_achieved:
        primary_failure = None
    else:
        priority = (
            "game_over",
            "premature_fire",
            "fire_window_timeout",
            "dead_end",
            "evaluator_overestimate",
            "candidate_absence",
        )
        primary_failure = next(
            (name for name in priority if observed_failures[name]),
            "candidate_absence",
        )
    build_decisions = sum(
        decision["phase"] == "build" for decision in decisions
    )
    fire_decisions = sum(
        decision["phase"] == "fire" for decision in decisions
    )
    fire_entered = phase_boundary is not None
    build_end_reason = (
        termination_reason
        if phase_boundary is None
        else str(phase_boundary["reason"])
    )
    phase_lifecycle = {
        "build": {
            "entered": True,
            "start_turn": 0,
            "end_turn_exclusive": int(build_decisions),
            "decision_count": int(build_decisions),
            "end_reason": build_end_reason,
        },
        "fire": {
            "entered": fire_entered,
            "start_turn": (
                None
                if phase_boundary is None
                else int(phase_boundary["turn"])
            ),
            "end_turn_exclusive": (
                None if not fire_entered else len(decisions)
            ),
            "decision_count": int(fire_decisions),
            "end_reason": (
                termination_reason
                if fire_entered
                else "not_entered_due_to_build_termination"
            ),
            "blocked_by": None if fire_entered else build_end_reason,
        },
    }
    return {
        "seed": int(seed),
        "selector": selector,
        "target_achieved": target_achieved,
        "termination_reason": termination_reason,
        "primary_failure_class": primary_failure,
        "failure_classes": [
            name for name in FAILURE_CLASSES if observed_failures[name]
        ],
        "phase_boundary": (
            None if phase_boundary is None else dict(phase_boundary)
        ),
        "phase_lifecycle": phase_lifecycle,
        "build_decisions": int(build_decisions),
        "fire_decisions": int(fire_decisions),
        "maximum_build_steps": int(configuration.build_steps),
        "maximum_fire_steps": int(configuration.fire_steps),
        "actual_max_chain_count": int(maximum_actual_chain),
        "actual_fire_chain_count": int(maximum_fire_chain),
        "maximum_k_best_search_chain": int(maximum_search_chain),
        "maximum_structural_potential": int(maximum_structural),
        "evaluator_calibration_failure": calibration_failure,
        "game_over_turn": next(
            (
                int(decision["turn"])
                for decision in decisions
                if decision["outcome"]["game_over"]
            ),
            None,
        ),
        "maximum_danger": max(
            (
                float(decision["selected_candidate"]["danger"])
                for decision in decisions
            ),
            default=0.0,
        ),
        "trigger_loss_count": sum(
            bool(decision["trigger_continuity"]["lost"])
            for decision in decisions
        ),
        "k_best_candidate_gap_count": sum(
            bool(decision["candidate_set_metrics"]["k_best_candidate_gap"])
            for decision in decisions
        ),
        "premature_fire_classification_counts": {
            name: int(premature.get(name, 0))
            for name in PREMATURE_FIRE_CLASSES
        },
        "future_isolation_passed": all(
            bool(decision["future_isolation"]["passed"])
            for decision in decisions
        ),
    }


def evaluate_oracle_trajectory(
    seed: int,
    *,
    configuration: OracleSearchConfiguration,
    selector: str = "offline_oracle",
) -> dict[str, Any]:
    """Evaluate one selector on a continuous authoritative build/fire trajectory."""

    if selector not in DEFAULT_SELECTORS:
        raise ValueError(f"unsupported selector: {selector}")
    simulator = HeadlessPuyoSimulator(seed=int(seed))
    opponent = HeadlessPuyoSimulator(seed=int(seed) + 1_000_003)
    policy = BeamSearchPolicy(configuration.beam_config())
    trajectory_id = _digest(
        {
            "seed": int(seed),
            "config": configuration.to_dict(),
            "selector": selector,
        },
        prefix="oracle-trajectory",
    )
    comparison_group_id = _digest(
        {"seed": int(seed), "config": configuration.to_dict()},
        prefix="oracle-comparison",
    )
    phase = "build"
    build_steps = 0
    fire_steps = 0
    score_carry = 0
    sent_ojama = 0
    maximum_chain = 0
    previous_trigger = None
    decisions = []
    phase_boundary = None
    termination_reason = "fire_window_timeout"

    while build_steps < configuration.build_steps or fire_steps < configuration.fire_steps:
        if phase == "build" and build_steps >= configuration.build_steps:
            phase = "fire"
            phase_boundary = {
                "turn": len(decisions),
                "build_steps": int(build_steps),
                "reason": "maximum_build_steps_reached",
            }
        remaining_fire = max(1, configuration.fire_steps - fire_steps)
        preview_steps = min(configuration.preview_steps, remaining_fire)
        observation, batch = _proposal_batch(
            policy,
            simulator,
            opponent,
            step_count=len(decisions),
            configuration=configuration,
            score_carry=score_carry,
            sent_ojama=sent_ojama,
        )
        future = authoritative_future_view(
            simulator,
            pair_count=max(3, preview_steps),
        )
        actual_candidates = [
            candidate for candidate in batch.candidates if candidate is not None
        ]
        evaluations = [
            evaluate_candidate_with_authoritative_future(
                simulator,
                candidate,
                target_chain_count=configuration.target_chain_count,
                preview_steps=preview_steps,
            )
            for candidate in actual_candidates
        ]
        target_path_available = any(
            evaluation["authoritative_future_rollout"]["target_fire_depth"]
            is not None
            for evaluation in evaluations
        )
        phase_transition = None
        if (
            selector == "offline_oracle"
            and phase == "build"
            and target_path_available
        ):
            phase = "fire"
            phase_boundary = {
                "turn": len(decisions),
                "build_steps": int(build_steps),
                "reason": "authoritative_target_path_available",
            }
            phase_transition = dict(phase_boundary)

        roots = _root_outcomes(simulator)
        comparison, indices = _selector_comparison(
            batch,
            evaluations,
            roots,
            phase=phase,
            previous_trigger=previous_trigger,
            max_chain_so_far=maximum_chain,
            target_chain_count=configuration.target_chain_count,
        )
        selected_index = indices[selector]
        if selected_index is None or selected_index >= len(evaluations):
            termination_reason = "candidate_absence"
            break
        selected = evaluations[selected_index]
        selected_candidate = actual_candidates[selected_index]
        if int(selected["root_action"]) != int(selected_candidate.root_action):
            raise RuntimeError("oracle candidate evaluation/root mismatch")
        if not batch.legal_action_mask[int(selected["root_action"])]:
            raise RuntimeError("oracle selected a root outside the legal K-best mask")

        candidate_root_outcomes = [
            {
                "action": int(evaluation["root_action"]),
                "valid": bool(evaluation["immediate_outcome"]["valid"]),
                "game_over": bool(evaluation["immediate_outcome"]["game_over"]),
                "chain_count": int(
                    evaluation["immediate_outcome"]["actual_chain_count"]
                ),
            }
            for evaluation in evaluations
        ]
        legal_safe = [
            outcome
            for outcome in roots.values()
            if outcome["valid"]
            and not outcome["game_over"]
            and (
                outcome["chain_count"] == 0
                or outcome["chain_count"] >= configuration.target_chain_count
            )
        ]
        candidate_safe = [
            outcome
            for outcome in candidate_root_outcomes
            if outcome["valid"]
            and not outcome["game_over"]
            and (
                outcome["chain_count"] == 0
                or outcome["chain_count"] >= configuration.target_chain_count
            )
        ]
        result = simulator.step(action_to_placement(int(selected["root_action"])))
        maximum_chain = max(maximum_chain, int(result.chain_count))
        conversion = convert_score_to_ojama(result.attack_score_delta, score_carry)
        score_carry = conversion.carry
        sent_ojama += conversion.units
        premature_classification = (
            classify_build_premature_fire(
                selected_chain_count=int(result.chain_count),
                target_chain_count=configuration.target_chain_count,
                candidate_root_outcomes=candidate_root_outcomes,
                legal_root_outcomes=list(roots.values()),
            )
            if phase == "build"
            else "none"
        )
        structural = selected["structural_prediction"]
        continuity = _trigger_continuity(
            previous_trigger,
            structural,
            target_fired=result.chain_count >= configuration.target_chain_count,
        )
        previous_trigger = structural
        isolation = verify_oracle_input_isolation(observation, batch, future)
        decision = {
            "schema_version": ORACLE_DECISION_SCHEMA_VERSION,
            "trajectory_id": trajectory_id,
            "comparison_group_id": comparison_group_id,
            "turn": len(decisions),
            "phase": phase,
            "phase_transition": phase_transition,
            "proposal": _compact_proposal(batch),
            "oracle_private_future": future.to_dict(),
            "future_isolation": isolation,
            "candidates": evaluations,
            "selector_comparison": comparison,
            "executed_selector": selector,
            "selected_candidate": {
                "candidate_id": selected["candidate_id"],
                "candidate_index": int(selected_index),
                "root_action": int(selected["root_action"]),
                "oracle_value": float(selected["oracle_value"]),
                "search_predicted_chain_count": int(
                    selected["search_predicted_chain_count"]
                ),
                "structural_potential": int(
                    structural["potential_chain_count"]
                ),
                "danger": float(selected["danger"]),
            },
            "candidate_set_metrics": {
                "maximum_search_chain": max(
                    (
                        int(item["search_predicted_chain_count"])
                        for item in evaluations
                    ),
                    default=0,
                ),
                "maximum_structural_potential": max(
                    (
                        int(
                            item["structural_prediction"][
                                "potential_chain_count"
                            ]
                        )
                        for item in evaluations
                    ),
                    default=0,
                ),
                "target_path_available": target_path_available,
                "k_best_candidate_gap": bool(legal_safe and not candidate_safe),
                "dead_end": bool(
                    not candidate_safe
                    or structural.get("structural_dead_end", False)
                ),
            },
            "trigger_continuity": continuity,
            "premature_fire": {
                "classification": premature_classification,
                "selected_chain_count": int(result.chain_count),
            },
            "outcome": {
                "valid": bool(result.valid),
                "game_over": bool(result.game_over),
                "actual_chain_count": int(result.chain_count),
                "score_delta": int(result.score_delta),
                "maximum_chain_so_far": int(maximum_chain),
            },
        }
        decisions.append(decision)
        if phase == "build":
            build_steps += 1
        else:
            fire_steps += 1

        if result.chain_count >= configuration.target_chain_count:
            termination_reason = "target_fire"
            break
        if result.game_over:
            termination_reason = "game_over"
            break
        if premature_classification != "none":
            termination_reason = "premature_fire"
            break
        if phase == "fire" and fire_steps >= configuration.fire_steps:
            termination_reason = "fire_window_timeout"
            break

    summary = _trajectory_summary(
        seed=seed,
        selector=selector,
        configuration=configuration,
        decisions=decisions,
        termination_reason=termination_reason,
        phase_boundary=phase_boundary,
    )
    payload = {
        "schema_version": ORACLE_TRAJECTORY_SCHEMA_VERSION,
        "trajectory_id": trajectory_id,
        "comparison_group_id": comparison_group_id,
        "config": configuration.to_dict(),
        "summary": summary,
        "decisions": decisions,
    }
    payload["latency_free_digest"] = _digest(
        _without_latency(payload),
        prefix="oracle-trajectory",
    )
    return payload


def _comparison_summary(
    trajectories: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_selector = {
        str(trajectory["summary"]["selector"]): trajectory["summary"]
        for trajectory in trajectories
    }
    oracle = by_selector["offline_oracle"]
    generator_success = bool(oracle["target_achieved"])
    baseline_failures = [
        selector
        for selector in (
            "compatibility_rank_0",
            "legacy_capability_selector",
        )
        if selector in by_selector
        and not by_selector[selector]["target_achieved"]
    ]
    if not generator_success:
        classification = "candidate_generator_failure"
    elif baseline_failures:
        classification = "candidate_selection_failure"
    else:
        classification = "candidate_and_selection_success"
    return {
        "seed": int(oracle["seed"]),
        "classification": classification,
        "generator_capability": {
            "oracle_target_achieved": generator_success,
            "oracle_failure_class": oracle["primary_failure_class"],
        },
        "selection_capability": {
            "failed_selectors": baseline_failures,
            "selector_results": {
                selector: {
                    "target_achieved": bool(summary["target_achieved"]),
                    "actual_max_chain_count": int(
                        summary["actual_max_chain_count"]
                    ),
                    "primary_failure_class": summary["primary_failure_class"],
                }
                for selector, summary in sorted(by_selector.items())
            },
        },
    }


def evaluate_oracle_seed(
    seed: int,
    *,
    configuration: OracleSearchConfiguration,
    selectors: Sequence[str] = DEFAULT_SELECTORS,
) -> dict[str, Any]:
    trajectories = [
        evaluate_oracle_trajectory(
            int(seed),
            configuration=configuration,
            selector=str(selector),
        )
        for selector in selectors
    ]
    if "offline_oracle" not in selectors:
        raise ValueError("oracle suite must include offline_oracle")
    return {
        "seed": int(seed),
        "trajectories": trajectories,
        "comparison": _comparison_summary(trajectories),
    }


def evaluate_oracle_suite(
    seeds: Sequence[int],
    *,
    configuration: OracleSearchConfiguration,
    selectors: Sequence[str] = DEFAULT_SELECTORS,
) -> dict[str, Any]:
    normalized = tuple(sorted({int(seed) for seed in seeds}))
    if not normalized:
        raise ValueError("oracle suite requires at least one seed")
    seed_results = [
        evaluate_oracle_seed(
            seed,
            configuration=configuration,
            selectors=selectors,
        )
        for seed in normalized
    ]
    comparisons = [result["comparison"] for result in seed_results]
    trajectories = [
        trajectory
        for result in seed_results
        for trajectory in result["trajectories"]
    ]
    summary = {
        "schema_version": ORACLE_SUITE_SCHEMA_VERSION,
        "config": configuration.to_dict(),
        "seeds": list(normalized),
        "selectors": list(selectors),
        "counts": dict(
            Counter(
                str(comparison["classification"])
                for comparison in comparisons
            )
        ),
        "oracle_successes": sum(
            bool(comparison["generator_capability"]["oracle_target_achieved"])
            for comparison in comparisons
        ),
        "future_isolation_passed": all(
            bool(trajectory["summary"]["future_isolation_passed"])
            for trajectory in trajectories
        ),
        "phase_limits_respected": all(
            int(trajectory["summary"]["build_decisions"])
            <= configuration.build_steps
            and int(trajectory["summary"]["fire_decisions"])
            <= configuration.fire_steps
            for trajectory in trajectories
        ),
        "executed_roots_in_k_best": all(
            decision["selected_candidate"]["candidate_id"]
            in decision["proposal"]["candidate_ids"]
            and decision["selected_candidate"]["root_action"]
            in decision["proposal"]["root_actions"]
            for trajectory in trajectories
            for decision in trajectory["decisions"]
        ),
        "comparisons": comparisons,
        "seed_results": seed_results,
    }
    summary["latency_free_digest"] = _digest(
        _without_latency(summary),
        prefix="oracle-suite",
    )
    return summary


def _report(summary: Mapping[str, Any], determinism: Mapping[str, Any]) -> str:
    counts = summary["counts"]
    config = summary["config"]
    search = config["search"]
    trajectory = config["trajectory"]
    potential = search["build_potential_budget"]
    return "\n".join(
        [
            "# PUYO-180 K-best offline oracle",
            "",
            f"- config: `{summary['config']['config_id']}`",
            f"- seeds: {len(summary['seeds'])}",
            f"- oracle target fires: {summary['oracle_successes']}/{len(summary['seeds'])}",
            f"- generator/selection classifications: `{json.dumps(counts, sort_keys=True)}`",
            f"- K-best root constraint: {'PASS' if summary['executed_roots_in_k_best'] else 'FAIL'}",
            f"- future-input isolation: {'PASS' if summary['future_isolation_passed'] else 'FAIL'}",
            f"- build/fire phase limits: {'PASS' if summary['phase_limits_respected'] else 'FAIL'}",
            f"- latency-free repeat determinism: {'PASS' if determinism['passed'] else 'FAIL'}",
            "",
            "The oracle is an offline candidate-set upper bound. Its future queue,",
            "values, and selections are not runtime or learned-policy features.",
            "",
            "Reproduce with:",
            "",
            "```bash",
            "python -m eval.v1_7_k_best_oracle run \\",
            f"  --seeds {','.join(str(seed) for seed in summary['seeds'])} \\",
            f"  --profile {config['profile']} \\",
            f"  --config-id {config['config_id']} \\",
            (
                f"  --depth {search['depth']} --width {search['width']} "
                f"--scenarios {search['scenarios']} \\"
            ),
            f"  --max-expanded-nodes {search['max_expanded_nodes']} \\",
            (
                f"  --build-steps {trajectory['maximum_build_steps']} "
                f"--fire-steps {trajectory['maximum_fire_steps']} \\"
            ),
            (
                f"  --preview-steps {trajectory['oracle_preview_steps']} "
                f"--target-chain {trajectory['target_chain_count']} \\"
            ),
            (
                "  --potential-max-added-puyos "
                f"{potential['max_added_puyos']} "
                "--potential-max-pattern-nodes "
                f"{potential['max_pattern_nodes']} \\"
            ),
            (
                "  --potential-max-resolution-nodes "
                f"{potential['max_resolution_nodes']} "
                "--potential-max-alternatives "
                f"{potential['max_alternatives']} \\"
            ),
            (
                "  --potential-max-continuation-actions "
                f"{potential['max_continuation_actions']} "
                "--potential-max-recovery-puyos "
                f"{potential['max_recovery_puyos']} \\"
            ),
            f"  --repetitions {determinism['repetitions']}",
            "python -m eval.v1_7_k_best_oracle verify",
            "```",
            "",
        ]
    )


def run_oracle_artifacts(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
    *,
    seeds: Sequence[int] = (123,),
    configuration: OracleSearchConfiguration | None = None,
    selectors: Sequence[str] = DEFAULT_SELECTORS,
    repetitions: int = 2,
) -> dict[str, Any]:
    if repetitions < 2:
        raise ValueError("oracle artifact requires at least two repetitions")
    config = configuration or OracleSearchConfiguration.for_profile()
    repetitions_payload = [
        evaluate_oracle_suite(
            seeds,
            configuration=config,
            selectors=selectors,
        )
        for _ in range(repetitions)
    ]
    digests = [payload["latency_free_digest"] for payload in repetitions_payload]
    determinism = {
        "schema_version": ORACLE_DETERMINISM_SCHEMA_VERSION,
        "passed": len(set(digests)) == 1,
        "repetitions": int(repetitions),
        "digests": digests,
        "excluded_fields": ["*latency*"],
    }
    first = repetitions_payload[0]
    first["determinism"] = determinism
    output = Path(output_dir)
    config_path = output / "oracle_config.json"
    summary_path = output / "oracle_summary.json"
    trajectory_path = output / "trajectory_records.json"
    determinism_path = output / "determinism.json"
    report_path = output / "benchmark_report.md"
    manifest_path = output / "benchmark_manifest.json"
    summary_payload = {
        key: value
        for key, value in first.items()
        if key != "seed_results"
    }
    _write_json(config_path, config.to_dict())
    _write_json(summary_path, summary_payload)
    _write_json(
        trajectory_path,
        {
            "schema_version": ORACLE_SUITE_SCHEMA_VERSION,
            "seed_results": first["seed_results"],
        },
    )
    _write_json(determinism_path, determinism)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_report(first, determinism), encoding="utf-8")
    artifacts = (
        (config_path, "config"),
        (summary_path, "summary"),
        (trajectory_path, "trajectories"),
        (determinism_path, "determinism"),
        (report_path, "report"),
    )
    manifest = {
        "schema_version": ORACLE_MANIFEST_SCHEMA_VERSION,
        "generated_at": utc_timestamp(),
        "git_commit": git_commit(),
        "artifacts": [
            describe_artifact(path, run_dir=output, role=role)
            for path, role in artifacts
        ],
    }
    _write_json(manifest_path, manifest)
    return summary_payload


def verify_oracle_artifacts(
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> dict[str, Any]:
    output = Path(output_dir)
    config = _read_json(output / "oracle_config.json")
    summary = _read_json(output / "oracle_summary.json")
    records = _read_json(output / "trajectory_records.json")
    determinism = _read_json(output / "determinism.json")
    manifest = _read_json(output / "benchmark_manifest.json")
    issues = []
    if config.get("schema_version") != ORACLE_CONFIG_SCHEMA_VERSION:
        issues.append("oracle config schema mismatch")
    if summary.get("schema_version") != ORACLE_SUITE_SCHEMA_VERSION:
        issues.append("oracle summary schema mismatch")
    if records.get("schema_version") != ORACLE_SUITE_SCHEMA_VERSION:
        issues.append("oracle trajectory record schema mismatch")
    if determinism.get("schema_version") != ORACLE_DETERMINISM_SCHEMA_VERSION:
        issues.append("oracle determinism schema mismatch")
    if manifest.get("schema_version") != ORACLE_MANIFEST_SCHEMA_VERSION:
        issues.append("oracle manifest schema mismatch")
    if not summary.get("future_isolation_passed"):
        issues.append("oracle future leaked into a runtime/learned input")
    if not summary.get("phase_limits_respected"):
        issues.append("oracle trajectory exceeded a phase limit")
    if not summary.get("executed_roots_in_k_best"):
        issues.append("oracle executed an action outside K-best")
    if not determinism.get("passed"):
        issues.append("oracle latency-free repetitions diverged")
    for seed_result in records.get("seed_results", ()):
        for trajectory in seed_result.get("trajectories", ()):
            if (
                trajectory.get("schema_version")
                != ORACLE_TRAJECTORY_SCHEMA_VERSION
            ):
                issues.append("oracle trajectory schema mismatch")
            trajectory_summary = trajectory.get("summary", {})
            lifecycle = trajectory_summary.get("phase_lifecycle", {})
            build_lifecycle = lifecycle.get("build", {})
            fire_lifecycle = lifecycle.get("fire", {})
            if (
                not build_lifecycle.get("entered")
                or not build_lifecycle.get("end_reason")
                or int(build_lifecycle.get("decision_count", -1))
                != int(trajectory_summary.get("build_decisions", -2))
            ):
                issues.append("oracle build-phase lifecycle mismatch")
            if (
                not fire_lifecycle.get("end_reason")
                or int(fire_lifecycle.get("decision_count", -1))
                != int(trajectory_summary.get("fire_decisions", -2))
                or bool(fire_lifecycle.get("entered"))
                != bool(trajectory_summary.get("phase_boundary"))
            ):
                issues.append("oracle fire-phase lifecycle mismatch")
            for decision in trajectory.get("decisions", ()):
                if decision.get("schema_version") != ORACLE_DECISION_SCHEMA_VERSION:
                    issues.append("oracle decision schema mismatch")
                if (
                    decision.get("oracle_private_future", {}).get("scope")
                    != "offline_oracle_evaluator_only"
                ):
                    issues.append("oracle future scope mismatch")
    for artifact in manifest.get("artifacts", ()):
        target = output / str(artifact.get("path", ""))
        if not target.is_file():
            issues.append(f"missing oracle artifact: {target}")
        elif artifact.get("sha256") != file_sha256(target):
            issues.append(f"oracle artifact checksum mismatch: {target}")
    if issues:
        raise ValueError("; ".join(issues))
    return {
        "schema_version": ORACLE_SUITE_SCHEMA_VERSION,
        "status": "passed",
        "latency_free_digest": summary["latency_free_digest"],
        "counts": dict(summary.get("counts", {})),
    }


def _parse_ints(value: str) -> tuple[int, ...]:
    result = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    if not result:
        raise argparse.ArgumentTypeError("at least one integer is required")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run")
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--seeds", type=_parse_ints, default=(123,))
    run.add_argument("--profile", default=RUNTIME_PROFILE)
    run.add_argument("--config-id", default="")
    run.add_argument("--depth", type=int)
    run.add_argument("--width", type=int)
    run.add_argument("--scenarios", type=int)
    run.add_argument("--max-expanded-nodes", type=int)
    run.add_argument("--build-steps", type=int, default=DEFAULT_BUILD_STEPS)
    run.add_argument("--fire-steps", type=int, default=DEFAULT_FIRE_STEPS)
    run.add_argument("--preview-steps", type=int, default=DEFAULT_FIRE_STEPS)
    run.add_argument("--target-chain", type=int, default=DEFAULT_TARGET_CHAIN)
    run.add_argument("--repetitions", type=int, default=2)
    defaults = BuildPotentialBudget()
    run.add_argument(
        "--potential-max-added-puyos",
        type=int,
        default=defaults.max_added_puyos,
    )
    run.add_argument(
        "--potential-max-pattern-nodes",
        type=int,
        default=defaults.max_pattern_nodes,
    )
    run.add_argument(
        "--potential-max-resolution-nodes",
        type=int,
        default=defaults.max_resolution_nodes,
    )
    run.add_argument(
        "--potential-max-alternatives",
        type=int,
        default=defaults.max_alternatives,
    )
    run.add_argument(
        "--potential-max-continuation-actions",
        type=int,
        default=defaults.max_continuation_actions,
    )
    run.add_argument(
        "--potential-max-recovery-puyos",
        type=int,
        default=defaults.max_recovery_puyos,
    )
    verify = subparsers.add_parser("verify")
    verify.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "verify":
        result = verify_oracle_artifacts(args.output_dir)
    else:
        overrides = {
            key: value
            for key, value in {
                "depth": args.depth,
                "width": args.width,
                "scenarios": args.scenarios,
                "max_expanded_nodes": args.max_expanded_nodes,
            }.items()
            if value is not None
        }
        configuration = OracleSearchConfiguration.for_profile(
            args.profile,
            config_id=args.config_id,
            target_chain_count=args.target_chain,
            build_steps=args.build_steps,
            fire_steps=args.fire_steps,
            preview_steps=args.preview_steps,
            build_potential_budget=BuildPotentialBudget(
                max_added_puyos=args.potential_max_added_puyos,
                max_pattern_nodes=args.potential_max_pattern_nodes,
                max_resolution_nodes=args.potential_max_resolution_nodes,
                max_alternatives=args.potential_max_alternatives,
                max_continuation_actions=(
                    args.potential_max_continuation_actions
                ),
                max_recovery_puyos=args.potential_max_recovery_puyos,
            ),
            **overrides,
        )
        result = run_oracle_artifacts(
            args.output_dir,
            seeds=args.seeds,
            configuration=configuration,
            repetitions=args.repetitions,
        )
    print(json.dumps(_canonical(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
