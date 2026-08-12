"""PUYO-132 reproducible v1.7.2 pre-training benchmark and scenario QA."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import json
import multiprocessing as mp
import os
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from agents.state_analyzer import (
    AnalyzerConfig,
    AnalyzerInput,
    StateAnalyzer,
    simulator_from_snapshot,
)
from agents.v1_7_strategy_manager import POLICY_TYPE
from eval.analyzer_scenarios import (
    build_report as build_analyzer_report,
    evaluate_scenarios as evaluate_analyzer_scenarios,
    load_scenarios as load_analyzer_scenarios,
    scenario_input,
    write_report as write_analyzer_report,
)
from eval.arena import (
    ArenaResult,
    MatchResult,
    run_parallel_paired_series,
    summarize_result,
    write_matches_csv,
)
from eval.lifecycle_audit import audit_realtime_lifecycle
from eval.realtime_arena import replay_realtime_match
from eval.v1_7_bootstrap_benchmark import load_checkpoint_evidence
from puyo_env.actions import action_to_placement, legal_action_mask
from puyo_env.obs import encode_board, encode_next_pairs, encode_scalars
from selfplay.policies import Policy, make_policy
from src.core.constants import VISIBLE_HEIGHT
from src.core.diagnostics import build_all_clear_runtime_info
from src.core.headless import HeadlessPuyoSimulator
from src.core.ojama import convert_score_to_ojama
from train.artifacts import describe_artifact, file_sha256, git_commit, utc_timestamp


BENCHMARK_SCHEMA_VERSION = "puyo.v1_7_benchmark.v1"
OUTCOME_SCHEMA_VERSION = "puyo.v1_7_response_scenarios.v1"
DEFAULT_OUTPUT_DIR = "docs/benchmarks/puyo-v1-7-2-baseline"
DEFAULT_OUTCOME_DATASET = Path(__file__).with_name("scenarios") / "v1_7_response.json"
DEFAULT_MODEL_REGISTRY = "runs/model_registry.json"
SAFE_POLICIES = (
    "v1_7_1",
    "forced_build_main",
    "worker_large",
    "standard_beam",
)
ARENA_BASELINES = (
    ("manager_rule", "manager_rule"),
    ("standard_beam", "beam"),
    ("worker_large", "worker_large"),
    ("existing_checkpoint", "checkpoint"),
)
REQUIRED_OUTCOMES = (
    "mild_threat",
    "short_burst",
    "full_cancel",
    "surplus_counter",
    "survival",
    "resume_build",
)

_SAFE_POLICY: Policy | None = None
_SAFE_MAX_STEPS = 40


def _write_json(path: str | Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def percentile(values: Sequence[float | int], quantile: float) -> float:
    """Return a deterministic linearly interpolated percentile."""

    if not values:
        return 0.0
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be in [0, 1]")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def _policy_spec(
    policy_type: str,
    *,
    seed: int,
    checkpoint_path: str | None = None,
    beam_depth: int = 10,
    beam_width: int = 48,
    forced_tactic_id: str | None = None,
) -> dict[str, Any]:
    return {
        "policy_type": policy_type,
        "seed": seed,
        "checkpoint_path": checkpoint_path,
        "device": "cpu",
        "deterministic": True,
        "beam_depth": beam_depth,
        "beam_width": beam_width,
        "beam_scenarios": 1,
        "beam_minimum_chain": 6,
        "forced_tactic_id": forced_tactic_id,
    }


def load_champion_evidence(registry_path: str | Path) -> dict[str, Any]:
    registry = _read_json(registry_path)
    champion = registry.get("roles", {}).get("champion")
    if not isinstance(champion, Mapping):
        raise ValueError(f"model registry has no champion: {registry_path}")
    path = Path(str(champion.get("path", "")))
    if not path.is_file():
        raise FileNotFoundError(f"champion checkpoint not found: {path}")
    actual_hash = file_sha256(path)
    declared_hash = str(champion.get("sha256", ""))
    return {
        "schema_version": "puyo.v1_7_existing_checkpoint_evidence.v1",
        "registry_path": str(registry_path),
        "registry_schema_version": registry.get("schema_version"),
        "path": str(path),
        "sha256": actual_hash,
        "declared_sha256": declared_hash,
        "hash_matches_registry": actual_hash == declared_hash,
        "size_bytes": path.stat().st_size,
    }


def _observation(
    simulator: HeadlessPuyoSimulator,
    opponent: HeadlessPuyoSimulator,
    *,
    step_count: int,
    max_steps: int,
    sent_ojama: int = 0,
) -> dict[str, Any]:
    own_board = encode_board(simulator.game)
    opponent_board = encode_board(opponent.game)
    return {
        "board": np.concatenate([own_board, opponent_board], axis=0).astype(
            np.float32, copy=False
        ),
        "own_board": own_board,
        "opponent_board": opponent_board,
        "next_pairs": encode_next_pairs(simulator.game),
        "scalars": encode_scalars(
            simulator.game,
            step_count=step_count,
            max_steps=max_steps,
            sent_ojama=sent_ojama,
        ),
    }


def _runtime_info(
    simulator: HeadlessPuyoSimulator,
    opponent: HeadlessPuyoSimulator,
    *,
    step_count: int,
    max_steps: int,
    score_carry: int = 0,
    sent_ojama: int = 0,
    canceled_ojama: int = 0,
    received_ojama: int = 0,
    incoming: Sequence[Mapping[str, int]] = (),
    opponent_score_carry: int = 0,
) -> dict[str, Any]:
    packets = tuple(
        {
            "amount": int(packet["amount"]),
            "arrival_step": step_count + int(packet.get("deadline", 0)),
            "created_step": step_count,
            "source_agent": "scenario",
        }
        for packet in incoming
    )
    return {
        "action_mask": np.asarray(legal_action_mask(simulator), dtype=np.bool_),
        "score": int(simulator.game.score),
        "opponent_score": int(opponent.game.score),
        "pending_ojama": sum(int(packet["amount"]) for packet in incoming),
        "incoming_ojama": sum(int(packet["amount"]) for packet in incoming),
        "incoming_attack_packets": packets,
        "sent_ojama_total": sent_ojama,
        "generated_ojama_total": sent_ojama + canceled_ojama,
        "canceled_ojama_total": canceled_ojama,
        "received_ojama_total": received_ojama,
        "score_carry": score_carry,
        "last_chain_end_score": int(simulator.game.last_chain_end_score),
        "opponent_pending_ojama": 0,
        "opponent_incoming_turns": 0,
        "opponent_sent_ojama_total": 0,
        "opponent_canceled_ojama_total": 0,
        "opponent_received_ojama_total": 0,
        "opponent_score_carry": opponent_score_carry,
        "opponent_simulator": opponent,
        "simulator": simulator,
        "step_count": step_count,
        "tick": step_count,
        "policy_deadline": step_count + 1,
        "max_steps": max_steps,
        **build_all_clear_runtime_info(simulator.game, opponent.game),
    }


def _main_chain_length(diagnostics: Any) -> int:
    main_chain = diagnostics.own.forecast.main_chain
    return 0 if main_chain is None else int(main_chain.chain_count)


def _run_safe_game(policy: Policy, seed: int, max_steps: int) -> dict[str, Any]:
    simulator = HeadlessPuyoSimulator(seed=seed)
    opponent = HeadlessPuyoSimulator(seed=seed + 1_000_003)
    analyzer = StateAnalyzer(
        AnalyzerConfig(max_depth=1, beam_width=12, max_attack_options=6)
    )
    carry = 0
    sent = 0
    max_chain = 0
    premature_fires = 0
    trigger_opportunities = 0
    trigger_losses = 0
    latencies: list[float] = []
    steps = 0
    reset = getattr(policy, "reset", None)
    if callable(reset):
        reset()
    for step_count in range(max_steps):
        info = _runtime_info(
            simulator,
            opponent,
            step_count=step_count,
            max_steps=max_steps,
            score_carry=carry,
            sent_ojama=sent,
        )
        observation = _observation(
            simulator,
            opponent,
            step_count=step_count,
            max_steps=max_steps,
            sent_ojama=sent,
        )
        started = time.perf_counter()
        action = policy.select_action(observation, info)
        latencies.append((time.perf_counter() - started) * 1000.0)
        before = getattr(policy, "last_analyzer_diagnostics", None)
        trigger_analyzer = getattr(policy, "analyzer", analyzer)
        if before is None:
            before = trigger_analyzer.analyze(AnalyzerInput.from_runtime_info(info))
        before_chain = _main_chain_length(before)
        result = simulator.step(action_to_placement(int(action)))
        steps += 1
        max_chain = max(max_chain, int(result.chain_count))
        premature_fires += int(0 < int(result.chain_count) < 10)
        conversion = convert_score_to_ojama(result.attack_score_delta, carry)
        carry = conversion.carry
        sent += conversion.units
        if before_chain > 0 and result.chain_count == 0:
            trigger_opportunities += 1
            after_info = _runtime_info(
                simulator,
                opponent,
                step_count=step_count + 1,
                max_steps=max_steps,
                score_carry=carry,
                sent_ojama=sent,
            )
            after = trigger_analyzer.analyze(
                AnalyzerInput.from_runtime_info(after_info)
            )
            after_chain = _main_chain_length(after)
            trigger_losses += int(after_chain == 0 or after_chain < before_chain)
        if result.game_over:
            break
    return {
        "seed": seed,
        "steps": steps,
        "max_chain": max_chain,
        "premature_fire_count": premature_fires,
        "trigger_opportunities": trigger_opportunities,
        "trigger_loss_count": trigger_losses,
        "game_over_before_limit": simulator.game.game_over and steps < max_steps,
        "score_carry": carry,
        "sent_ojama": sent,
        "decision_p50_ms": percentile(latencies, 0.50),
        "decision_p95_ms": percentile(latencies, 0.95),
        "_decision_latencies_ms": latencies,
    }


def _safe_initializer(policy_spec: Mapping[str, Any], max_steps: int) -> None:
    global _SAFE_POLICY, _SAFE_MAX_STEPS
    _SAFE_POLICY = make_policy(**dict(policy_spec))
    _SAFE_MAX_STEPS = int(max_steps)


def _safe_worker(seed: int) -> dict[str, Any]:
    if _SAFE_POLICY is None:
        raise RuntimeError("safe-build worker is not initialized")
    return _run_safe_game(_SAFE_POLICY, seed, _SAFE_MAX_STEPS)


def aggregate_safe_suite(
    label: str,
    records: Sequence[Mapping[str, Any]],
    *,
    max_steps: int,
) -> dict[str, Any]:
    max_chains = [int(record["max_chain"]) for record in records]
    latencies = [
        float(latency)
        for record in records
        for latency in record.get("_decision_latencies_ms", ())
    ]
    decisions = sum(int(record["steps"]) for record in records)
    premature_count = sum(int(record["premature_fire_count"]) for record in records)
    trigger_opportunities = sum(
        int(record["trigger_opportunities"]) for record in records
    )
    trigger_losses = sum(int(record["trigger_loss_count"]) for record in records)
    early_game_overs = sum(bool(record["game_over_before_limit"]) for record in records)
    mean_max_chain = sum(max_chains) / max(1, len(max_chains))
    gates = {
        "mean_max_chain": {
            "passed": mean_max_chain >= 10.0,
            "actual": mean_max_chain,
            "expected": ">= 10.0",
        },
        "premature_fire": {
            "passed": premature_count == 0,
            "actual": premature_count,
            "expected": "0 safe/no-threat fires of chain length 1-9",
        },
        "game_over": {
            "passed": early_game_overs == 0,
            "actual": early_game_overs,
            "expected": f"0 before {max_steps} moves",
        },
    }
    return {
        "label": label,
        "games": len(records),
        "moves_per_game": max_steps,
        "mean_max_chain": mean_max_chain,
        "max_chain_p50": percentile(max_chains, 0.50),
        "max_chain_p90": percentile(max_chains, 0.90),
        "max_chain_max": max(max_chains, default=0),
        "premature_fire_count": premature_count,
        "premature_fire_rate": premature_count / max(1, decisions),
        "trigger_opportunities": trigger_opportunities,
        "trigger_loss_count": trigger_losses,
        "trigger_loss_rate": trigger_losses / max(1, trigger_opportunities),
        "game_over_before_limit": early_game_overs,
        "decision_p50_ms": percentile(latencies, 0.50),
        "decision_p95_ms": percentile(latencies, 0.95),
        "gates": gates,
        "passed": all(gate["passed"] for gate in gates.values()),
    }


def run_safe_suite(
    label: str,
    policy_spec: Mapping[str, Any],
    *,
    games: int,
    seed: int,
    max_steps: int,
    workers: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    seeds = list(range(seed, seed + games))
    if workers == 1:
        policy = make_policy(**dict(policy_spec))
        records = [_run_safe_game(policy, item, max_steps) for item in seeds]
    else:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            mp_context=mp.get_context("spawn"),
            initializer=_safe_initializer,
            initargs=(dict(policy_spec), max_steps),
        ) as executor:
            records = list(executor.map(_safe_worker, seeds))
    summary = aggregate_safe_suite(label, records, max_steps=max_steps)
    clean_records = [
        {key: value for key, value in record.items() if not key.startswith("_")}
        for record in records
    ]
    return summary, clean_records


def _resolve_attack(generated: int, incoming: int) -> dict[str, int]:
    canceled = min(max(0, generated), max(0, incoming))
    return {
        "generated": max(0, generated),
        "canceled": canceled,
        "outgoing": max(0, generated) - canceled,
        "received": max(0, incoming) - canceled,
    }


def load_response_scenarios(
    path: str | Path = DEFAULT_OUTCOME_DATASET,
) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if payload.get("schema_version") != OUTCOME_SCHEMA_VERSION:
        raise ValueError(
            f"unsupported response scenario schema: {payload.get('schema_version')}"
        )
    scenarios = payload.get("scenarios")
    if not isinstance(scenarios, list):
        raise ValueError("response scenario dataset must contain scenarios")
    names = [str(item.get("name", "")) for item in scenarios]
    if tuple(names) != REQUIRED_OUTCOMES:
        raise ValueError(f"response scenarios must be ordered as {REQUIRED_OUTCOMES}")
    return scenarios


def assess_outcome(name: str, actual: Mapping[str, Any]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if name == "mild_threat":
        if not actual.get("response_selected"):
            failures.append("response tactic was not selected")
    elif name == "short_burst":
        if not actual.get("response_selected"):
            failures.append("response tactic was not selected")
        if not actual.get("survived"):
            failures.append("policy did not survive")
    elif name == "full_cancel":
        if int(actual.get("canceled", 0)) < int(actual.get("incoming", 0)):
            failures.append("incoming attack was not fully canceled")
        if int(actual.get("received", 0)) != 0:
            failures.append("garbage was received after full-cancel scenario")
    elif name == "surplus_counter":
        if int(actual.get("outgoing", 0)) <= 0:
            failures.append("counter produced no outgoing surplus")
        if int(actual.get("received", 0)) != 0:
            failures.append("surplus counter still received garbage")
    elif name == "survival":
        if not actual.get("survived"):
            failures.append("policy did not survive the deficit")
    elif name == "resume_build":
        if actual.get("followup_tactic") != "build_main":
            failures.append("policy did not resume build_main")
    else:
        failures.append(f"unknown outcome scenario: {name}")
    return not failures, failures


def _scenario_by_name(name: str) -> Mapping[str, Any]:
    try:
        return next(
            scenario
            for scenario in load_analyzer_scenarios()
            if scenario["name"] == name
        )
    except StopIteration as exc:
        raise ValueError(f"unknown analyzer scenario: {name}") from exc


def evaluate_response_scenarios(
    checkpoint: str | Path,
    *,
    dataset_path: str | Path = DEFAULT_OUTCOME_DATASET,
) -> dict[str, Any]:
    policy = make_policy(
        POLICY_TYPE,
        checkpoint_path=checkpoint,
        device="cpu",
        deterministic=True,
    )
    results = []
    for definition in load_response_scenarios(dataset_path):
        source = _scenario_by_name(str(definition["analyzer_scenario"]))
        analyzer_input = scenario_input(source)
        simulator = simulator_from_snapshot(analyzer_input.own)
        opponent = simulator_from_snapshot(analyzer_input.opponent)
        incoming = [
            {"amount": packet.amount, "deadline": packet.deadline}
            for packet in analyzer_input.own.incoming
        ]
        info = _runtime_info(
            simulator,
            opponent,
            step_count=0,
            max_steps=2,
            score_carry=analyzer_input.own.score_carry,
            incoming=incoming,
        )
        observation = _observation(simulator, opponent, step_count=0, max_steps=2)
        action = policy.select_action(observation, info)
        selected_tactic = str(getattr(policy, "current_profile_name", "") or "")
        step_result = simulator.step(action_to_placement(int(action)))
        conversion = convert_score_to_ojama(
            step_result.attack_score_delta,
            analyzer_input.own.score_carry,
        )
        incoming_amount = sum(packet.amount for packet in analyzer_input.own.incoming)
        attack = _resolve_attack(conversion.units, incoming_amount)
        received = (
            attack["received"]
            if min(
                (packet.deadline for packet in analyzer_input.own.incoming), default=1
            )
            <= 1
            else 0
        )
        if received:
            simulator.game.field.drop_ojama(
                received,
                rng=random.Random(132),
                max_per_drop=30,
            )
            if not simulator.game.field.get_puyo(2, VISIBLE_HEIGHT - 1).is_empty():
                simulator.game.game_over = True
        followup_tactic = None
        if definition["name"] == "resume_build" and not simulator.game.game_over:
            followup_info = _runtime_info(
                simulator,
                opponent,
                step_count=1,
                max_steps=2,
                score_carry=conversion.carry,
                sent_ojama=attack["outgoing"],
                canceled_ojama=attack["canceled"],
                received_ojama=received,
            )
            followup_observation = _observation(
                simulator,
                opponent,
                step_count=1,
                max_steps=2,
                sent_ojama=attack["outgoing"],
            )
            policy.select_action(followup_observation, followup_info)
            followup_tactic = str(getattr(policy, "current_profile_name", "") or "")
        actual = {
            "selected_tactic": selected_tactic,
            "response_selected": selected_tactic
            in {"prepare_response", "counter_or_return", "survive"},
            "action": int(action),
            "chain_count": int(step_result.chain_count),
            "incoming": incoming_amount,
            **attack,
            "received": received,
            "survived": not simulator.game.game_over,
            "followup_tactic": followup_tactic,
        }
        passed, failures = assess_outcome(str(definition["name"]), actual)
        results.append(
            {
                "name": definition["name"],
                "description": definition["description"],
                "analyzer_scenario": definition["analyzer_scenario"],
                "passed": passed,
                "failures": failures,
                "actual": actual,
            }
        )
    passed_count = sum(bool(item["passed"]) for item in results)
    return {
        "schema_version": OUTCOME_SCHEMA_VERSION,
        "summary": {
            "scenarios": len(results),
            "passed": passed_count,
            "failed": len(results) - passed_count,
        },
        "results": results,
    }


def build_lifecycle_report(analyzer_report: Mapping[str, Any]) -> dict[str, Any]:
    carry_cases = []
    for score in (69, 70, 71):
        conversion = convert_score_to_ojama(score)
        expected_units = score // 70
        expected_carry = score % 70
        carry_cases.append(
            {
                "score": score,
                "units": conversion.units,
                "carry": conversion.carry,
                "passed": conversion.units == expected_units
                and conversion.carry == expected_carry,
            }
        )
    cancel_cases = []
    for name, generated, incoming, expected in (
        ("exact_cancel", 5, 5, {"canceled": 5, "outgoing": 0, "received": 0}),
        ("surplus_counter", 5, 3, {"canceled": 3, "outgoing": 2, "received": 0}),
        ("deficit", 5, 6, {"canceled": 5, "outgoing": 0, "received": 1}),
    ):
        actual = _resolve_attack(generated, incoming)
        cancel_cases.append(
            {
                "name": name,
                "actual": actual,
                "expected": expected,
                "passed": all(actual[key] == value for key, value in expected.items()),
            }
        )
    lifecycle_names = {
        "initial_empty_board_has_no_all_clear_event",
        "own_all_clear_state_is_independent",
        "pending_bonus_survives_non_clearing_turn",
        "pending_bonus_is_consumed_by_next_chain",
        "consumed_bonus_is_not_applied_again",
    }
    lifecycle_scenarios = [
        {
            "name": item["name"],
            "passed": bool(item["passed"]),
        }
        for item in analyzer_report.get("results", ())
        if item.get("name") in lifecycle_names
    ]
    passed = (
        len(lifecycle_scenarios) == len(lifecycle_names)
        and all(item["passed"] for item in lifecycle_scenarios)
        and all(item["passed"] for item in carry_cases)
        and all(item["passed"] for item in cancel_cases)
    )
    return {
        "schema_version": "puyo.v1_7_lifecycle_parity.v1",
        "passed": passed,
        "all_clear_lifecycle": lifecycle_scenarios,
        "carry_boundaries": carry_cases,
        "cancel_boundaries": cancel_cases,
    }


def _side_value(match: MatchResult, stem: str) -> int:
    suffix = "player_0" if match.policy_a_side == "player_0" else "player_1"
    return int(getattr(match, f"{stem}_{suffix}"))


def enrich_arena_summary(
    summary: dict[str, Any], result: ArenaResult
) -> dict[str, Any]:
    canceled = sum(_side_value(match, "canceled_ojama") for match in result.matches)
    received = sum(_side_value(match, "received_ojama") for match in result.matches)
    opportunities = sum(
        _side_value(match, "short_threat_opportunities") for match in result.matches
    )
    responses = sum(
        _side_value(match, "short_threat_responses") for match in result.matches
    )
    summary.update(
        {
            "max_chain_policy_a": max(
                (_side_value(match, "max_chain") for match in result.matches),
                default=0,
            ),
            "self_chokes_policy_a": sum(
                _side_value(match, "self_choke") for match in result.matches
            ),
            "cancel_rate_policy_a": canceled / max(1, canceled + received),
            "short_threat_opportunities_policy_a": opportunities,
            "short_threat_responses_policy_a": responses,
            "short_threat_response_rate_policy_a": responses / max(1, opportunities),
        }
    )
    summary["self_choke_rate_policy_a"] = summary["self_chokes_policy_a"] / max(
        1, len(result.matches)
    )
    return summary


def _run_gui_qa(
    *,
    checkpoint: Path,
    output_dir: Path,
    seed: int,
    max_ticks: int,
    max_frames: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    qa_path = output_dir / "gui_qa.json"
    replay_path = output_dir / "gui_qa_replay.json"
    command = [
        sys.executable,
        "-m",
        "eval.realtime_versus_ui",
        "--policy-a",
        POLICY_TYPE,
        "--checkpoint-a",
        str(checkpoint),
        "--policy-b",
        "v1_7_analyzer_manager",
        "--seed",
        str(seed),
        "--max-ticks",
        str(max_ticks),
        "--speed",
        "1",
        "--result-json",
        str(qa_path),
        "--replay",
        str(replay_path),
        "--qa-notes",
        "PUYO-132 post-PUYO-162 deterministic attack-profile GUI QA",
        "--latency-mode",
        "measured",
        "--qa-profile",
        "attack",
        "--max-frames",
        str(max_frames),
    ]
    environment = os.environ.copy()
    environment.update({"SDL_VIDEODRIVER": "dummy", "SDL_AUDIODRIVER": "dummy"})
    completed = subprocess.run(
        command,
        check=False,
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
    )
    if completed.returncode not in (0, 2):
        raise subprocess.CalledProcessError(completed.returncode, command)
    qa = _read_json(qa_path)
    replay = _read_json(replay_path)
    final_hash = replay_realtime_match(replay)
    lifecycle = audit_realtime_lifecycle(
        initial_all_clear_diagnostics=replay.get("initial_all_clear_diagnostics"),
        ticks=replay.get("ticks", ()),
    )
    if qa.get("diagnostics", {}).get("lifecycle_coverage") != lifecycle:
        raise AssertionError("GUI lifecycle diagnostics do not match replay audit")
    return qa, {"verified": True, "final_hash": final_hash, "lifecycle": lifecycle}


def build_completion(
    *,
    checkpoint: Mapping[str, Any],
    champion: Mapping[str, Any],
    analyzer_report: Mapping[str, Any],
    safe_summaries: Sequence[Mapping[str, Any]],
    outcome_report: Mapping[str, Any],
    lifecycle_report: Mapping[str, Any],
    arena_summaries: Sequence[Mapping[str, Any]],
    gui_qa: Mapping[str, Any] | None,
    gui_verification: Mapping[str, Any] | None,
) -> dict[str, Any]:
    safe_by_label = {str(item["label"]): item for item in safe_summaries}
    analyzer_summary = analyzer_report.get("summary", {})
    evaluation_checks = {
        "checkpoint_valid": not checkpoint.get("validation_errors"),
        "champion_valid": bool(champion.get("hash_matches_registry")),
        "analyzer_executed": analyzer_summary.get("scenarios") == 24,
        "safe_build_executed": set(safe_by_label) == set(SAFE_POLICIES)
        and all(int(item.get("games", 0)) > 0 for item in safe_summaries),
        "outcomes_executed": outcome_report.get("summary", {}).get("scenarios") == 6,
        "lifecycle_evaluator_valid": bool(lifecycle_report.get("passed")),
        "paired_arena_executed": len(arena_summaries) == len(ARENA_BASELINES),
        "gui_executed": bool(
            gui_qa
            and gui_qa.get("result", {}).get("execution_completed")
            and gui_verification
            and gui_verification.get("verified")
        ),
    }
    candidate = safe_by_label.get("v1_7_1", {})
    training_checks = {
        "safe_build_gate": bool(candidate.get("passed")),
        "outcome_gate": outcome_report.get("summary", {}).get("failed") == 0,
        "analyzer_gate": analyzer_summary.get("scenarios") == 24
        and analyzer_summary.get("failed") == 0,
        "lifecycle_gate": bool(lifecycle_report.get("passed")),
        "gui_attack_gate": bool(
            gui_qa and gui_qa.get("quality_gate", {}).get("passed")
        ),
    }
    return {
        "evaluation_checks": evaluation_checks,
        "training_checks": training_checks,
        "evaluation_completed": all(evaluation_checks.values()),
        "training_gate_passed": all(training_checks.values()),
    }


def _write_safe_csv(path: Path, records: Sequence[Mapping[str, Any]]) -> None:
    fieldnames = [
        "policy",
        "seed",
        "steps",
        "max_chain",
        "premature_fire_count",
        "trigger_opportunities",
        "trigger_loss_count",
        "game_over_before_limit",
        "score_carry",
        "sent_ojama",
        "decision_p50_ms",
        "decision_p95_ms",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(records)


def _write_report(path: Path, summary: Mapping[str, Any]) -> None:
    completion = summary["completion"]
    lines = [
        "# v1.7.2 Pre-training Benchmark / Scenario QA",
        "",
        f"- evaluator: **{'PASS' if completion['evaluation_completed'] else 'FAIL'}**",
        f"- training gate: **{'PASS' if completion['training_gate_passed'] else 'BLOCKED'}**",
        f"- v1.7.1 checkpoint: `{summary['checkpoint']['path']}`",
        f"- existing champion: `{summary['existing_checkpoint']['path']}`",
        "",
        "PUYO-132 の完了条件は evaluator と baseline artifact の成立です。training gate が BLOCKED の場合、PUYO-130 は開始しません。",
        "",
        "## Safe-build",
        "",
        "| policy | mean max chain | p50 | p90 | max | premature | trigger loss | game over | p95 ms | gate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for item in summary["safe_build"]:
        lines.append(
            f"| `{item['label']}` | {item['mean_max_chain']:.2f} | "
            f"{item['max_chain_p50']:.1f} | {item['max_chain_p90']:.1f} | "
            f"{item['max_chain_max']} | {item['premature_fire_count']} | "
            f"{item['trigger_loss_count']}/{item['trigger_opportunities']} | "
            f"{item['game_over_before_limit']} | {item['decision_p95_ms']:.2f} | "
            f"{'PASS' if item['passed'] else 'FAIL'} |"
        )
    lines.extend(
        [
            "",
            "## Paired arena",
            "",
            "| opponent | matches | win rate | max chain | response rate | cancel rate | self-choke | decision ms |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in summary["paired_arena"]:
        lines.append(
            f"| `{item['policy_b']}` | {item['games']} | {item['win_rate_policy_a']:.3f} | "
            f"{item['max_chain_policy_a']} | {item['short_threat_response_rate_policy_a']:.3f} | "
            f"{item['cancel_rate_policy_a']:.3f} | {item['self_choke_rate_policy_a']:.3f} | "
            f"{item['mean_decision_ms_policy_a']:.2f} |"
        )
    outcome = summary["outcomes"]["summary"]
    lines.extend(
        [
            "",
            "## Fixed scenarios and GUI",
            "",
            f"- Analyzer: {summary['analyzer']['passed']}/{summary['analyzer']['scenarios']}",
            f"- outcome scenarios: {outcome['passed']}/{outcome['scenarios']}",
            f"- lifecycle/carry/cancel parity: {'PASS' if summary['lifecycle']['passed'] else 'FAIL'}",
            f"- GUI attack profile: {'PASS' if (summary.get('gui') or {}).get('quality_gate', {}).get('passed') else 'FAIL'}",
            "",
            "## Human-visible QA",
            "",
            "`python3 main.py` から観戦を選び、1P に v1.7.1 checkpoint、2P に v1_7_analyzer_manager、seed 123、speed 1x を指定します。",
            "`gui_qa_replay.json` は `python3 -m eval.model_viewer` で attack/carry/lifecycle diagnostics を確認できます。",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = Path(args.checkpoint).resolve()
    registry_path = Path(args.model_registry).resolve()
    checkpoint, _ = load_checkpoint_evidence(checkpoint_path)
    champion = load_champion_evidence(registry_path)
    _write_json(output_dir / "checkpoint_evidence.json", checkpoint)
    _write_json(output_dir / "existing_checkpoint_evidence.json", champion)

    analyzer_report = build_analyzer_report(evaluate_analyzer_scenarios())
    write_analyzer_report(output_dir / "analyzer_report.json", analyzer_report)
    lifecycle_report = build_lifecycle_report(analyzer_report)
    _write_json(output_dir / "lifecycle_report.json", lifecycle_report)

    safe_specs = {
        "v1_7_1": _policy_spec(
            POLICY_TYPE,
            seed=args.seed,
            checkpoint_path=str(checkpoint_path),
        ),
        "forced_build_main": _policy_spec(
            POLICY_TYPE,
            seed=args.seed,
            checkpoint_path=str(checkpoint_path),
            forced_tactic_id="build_main",
        ),
        "worker_large": _policy_spec("worker_large", seed=args.seed),
        "standard_beam": _policy_spec(
            "beam",
            seed=args.seed,
            beam_depth=args.beam_depth,
            beam_width=args.beam_width,
        ),
    }
    safe_summaries = []
    safe_records = []
    for label in SAFE_POLICIES:
        suite, records = run_safe_suite(
            label,
            safe_specs[label],
            games=args.safe_games,
            seed=args.seed,
            max_steps=args.max_steps,
            workers=args.workers,
        )
        safe_summaries.append(suite)
        safe_records.extend({"policy": label, **record} for record in records)
        print(
            f"safe {label}: mean_max_chain={suite['mean_max_chain']:.2f} "
            f"premature={suite['premature_fire_count']} gate={suite['passed']}"
        )
    _write_json(output_dir / "safe_build_summary.json", {"policies": safe_summaries})
    _write_safe_csv(output_dir / "safe_build_games.csv", safe_records)

    outcome_report = evaluate_response_scenarios(
        checkpoint_path,
        dataset_path=args.outcome_dataset,
    )
    _write_json(output_dir / "outcome_scenarios.json", outcome_report)

    candidate_spec = _policy_spec(
        POLICY_TYPE,
        seed=args.seed,
        checkpoint_path=str(checkpoint_path),
    )
    arena_summaries = []
    for label, policy_type in ARENA_BASELINES:
        baseline_spec = _policy_spec(
            policy_type,
            seed=args.seed + 10_000,
            checkpoint_path=(
                str(champion["path"]) if policy_type == "checkpoint" else None
            ),
            beam_depth=args.beam_depth,
            beam_width=args.beam_width,
        )
        result = run_parallel_paired_series(
            candidate_spec,
            baseline_spec,
            games=args.arena_games,
            seed=args.seed,
            max_steps=args.max_steps,
            workers=args.workers,
        )
        summary = summarize_result(
            result,
            label=f"v1_7_1_vs_{label}",
            policy_a=POLICY_TYPE,
            policy_b=label,
            checkpoint_a=str(checkpoint_path),
            checkpoint_b=(
                str(champion["path"]) if policy_type == "checkpoint" else None
            ),
            games=len(result.matches),
            seed=args.seed,
            max_steps=args.max_steps,
        )
        enrich_arena_summary(summary, result)
        arena_summaries.append(summary)
        write_matches_csv(output_dir / f"arena_{label}_matches.csv", result.matches)
        _write_json(output_dir / f"arena_{label}_summary.json", summary)
        print(
            f"arena {label}: wins={summary['wins_policy_a']} "
            f"response={summary['short_threat_response_rate_policy_a']:.3f}"
        )

    gui_qa = None
    gui_verification = None
    if not args.skip_gui:
        gui_qa, gui_verification = _run_gui_qa(
            checkpoint=checkpoint_path,
            output_dir=output_dir,
            seed=args.gui_seed,
            max_ticks=args.gui_max_ticks,
            max_frames=args.gui_max_frames,
        )
        _write_json(output_dir / "gui_verification.json", gui_verification)

    completion = build_completion(
        checkpoint=checkpoint,
        champion=champion,
        analyzer_report=analyzer_report,
        safe_summaries=safe_summaries,
        outcome_report=outcome_report,
        lifecycle_report=lifecycle_report,
        arena_summaries=arena_summaries,
        gui_qa=gui_qa,
        gui_verification=gui_verification,
    )
    config = {
        "seed": args.seed,
        "safe_games": args.safe_games,
        "arena_games": args.arena_games,
        "paired_matches_per_baseline": args.arena_games * 2,
        "max_steps": args.max_steps,
        "workers": args.workers,
        "beam_depth": args.beam_depth,
        "beam_width": args.beam_width,
        "gui_seed": args.gui_seed,
        "gui_max_ticks": args.gui_max_ticks,
        "gui_max_frames": args.gui_max_frames,
    }
    summary_payload = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "created_at_utc": utc_timestamp(),
        "git_commit": git_commit(),
        "evaluation_completed": completion["evaluation_completed"],
        "training_gate_passed": completion["training_gate_passed"],
        "config": config,
        "checkpoint": checkpoint,
        "existing_checkpoint": champion,
        "analyzer": analyzer_report["summary"],
        "safe_build": safe_summaries,
        "outcomes": outcome_report,
        "lifecycle": lifecycle_report,
        "paired_arena": arena_summaries,
        "gui": gui_qa,
        "gui_verification": gui_verification,
        "completion": completion,
    }
    _write_json(output_dir / "benchmark_summary.json", summary_payload)
    _write_report(output_dir / "benchmark_report.md", summary_payload)

    artifact_paths = sorted(
        path
        for path in output_dir.iterdir()
        if path.is_file() and path.name != "benchmark_manifest.json"
    )
    manifest = {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "name": "puyo-v1-7-2-baseline",
        "created_at_utc": summary_payload["created_at_utc"],
        "evaluation_completed": completion["evaluation_completed"],
        "training_gate_passed": completion["training_gate_passed"],
        "checkpoint": {"path": str(checkpoint_path), "sha256": checkpoint["sha256"]},
        "existing_checkpoint": {
            "path": champion["path"],
            "sha256": champion["sha256"],
        },
        "config": config,
        "artifacts": [
            describe_artifact(path, run_dir=output_dir, role=path.stem)
            for path in artifact_paths
        ],
    }
    _write_json(output_dir / "benchmark_manifest.json", manifest)
    print(
        "PUYO-132 evaluator: "
        f"{'PASS' if completion['evaluation_completed'] else 'FAIL'}; "
        "training gate: "
        f"{'PASS' if completion['training_gate_passed'] else 'BLOCKED'}"
    )
    return summary_payload


def verify_benchmark(args: argparse.Namespace) -> dict[str, Any]:
    output_dir = Path(args.artifact_dir)
    manifest = _read_json(output_dir / "benchmark_manifest.json")
    issues = []
    if manifest.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        issues.append(f"unexpected benchmark schema: {manifest.get('schema_version')}")
    for artifact in manifest.get("artifacts", ()):
        path = output_dir / str(artifact.get("path", ""))
        if not path.is_file():
            issues.append(f"missing artifact: {path}")
        elif artifact.get("sha256") != file_sha256(path):
            issues.append(f"artifact hash mismatch: {path}")
    checkpoint, _ = load_checkpoint_evidence(args.checkpoint)
    if checkpoint["sha256"] != manifest.get("checkpoint", {}).get("sha256"):
        issues.append("v1.7.1 checkpoint hash does not match manifest")
    champion = load_champion_evidence(args.model_registry)
    if champion["sha256"] != manifest.get("existing_checkpoint", {}).get("sha256"):
        issues.append("existing checkpoint hash does not match manifest")
    replay_path = output_dir / "gui_qa_replay.json"
    if replay_path.is_file():
        replay_realtime_match(_read_json(replay_path))
    summary = _read_json(output_dir / "benchmark_summary.json")
    if not summary.get("evaluation_completed"):
        issues.append("benchmark evaluator did not complete")
    if args.require_training_gate and not summary.get("training_gate_passed"):
        issues.append("training gate is blocked")
    result = {
        "passed": not issues,
        "issues": issues,
        "evaluation_completed": bool(summary.get("evaluation_completed")),
        "training_gate_passed": bool(summary.get("training_gate_passed")),
        "checkpoint": checkpoint["sha256"],
        "existing_checkpoint": champion["sha256"],
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run or verify the PUYO-132 v1.7.2 pre-training benchmark."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser(
        "run", help="run benchmark and write baseline artifacts"
    )
    run.add_argument("--checkpoint", required=True)
    run.add_argument("--model-registry", default=DEFAULT_MODEL_REGISTRY)
    run.add_argument("--outcome-dataset", default=str(DEFAULT_OUTCOME_DATASET))
    run.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    run.add_argument("--safe-games", type=int, default=30)
    run.add_argument("--arena-games", type=int, default=20)
    run.add_argument("--seed", type=int, default=123)
    run.add_argument("--max-steps", type=int, default=40)
    run.add_argument("--workers", type=int, default=8)
    run.add_argument("--beam-depth", type=int, default=10)
    run.add_argument("--beam-width", type=int, default=48)
    run.add_argument("--gui-seed", type=int, default=123)
    run.add_argument("--gui-max-ticks", type=int, default=1200)
    run.add_argument("--gui-max-frames", type=int, default=1280)
    run.add_argument("--skip-gui", action="store_true")
    verify = subparsers.add_parser(
        "verify", help="verify artifact hashes and completion"
    )
    verify.add_argument("--checkpoint", required=True)
    verify.add_argument("--model-registry", default=DEFAULT_MODEL_REGISTRY)
    verify.add_argument("--artifact-dir", default=DEFAULT_OUTPUT_DIR)
    verify.add_argument("--require-training-gate", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "run":
        positive = (
            args.safe_games,
            args.arena_games,
            args.max_steps,
            args.workers,
            args.beam_depth,
            args.beam_width,
            args.gui_max_ticks,
            args.gui_max_frames,
        )
        if any(value <= 0 for value in positive):
            parser.error("benchmark counts and limits must be positive")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "run":
        result = run_benchmark(args)
        return 0 if result["evaluation_completed"] else 1
    result = verify_benchmark(args)
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
