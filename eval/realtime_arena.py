"""Realtime arena for evaluating placement policies through tick inputs."""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from puyo_env.realtime_ai import (
    RealtimeDecisionConfig,
    RealtimePolicyController,
    RealtimePuyoEnv,
)
from selfplay.policies import Policy, make_policy
from src.core.realtime import TickInput


def _policy_search_objective_diagnostics(policy: Policy) -> dict[str, Any]:
    diagnostics = getattr(policy, "tactical_diagnostics", None)
    if isinstance(diagnostics, dict) and (
        diagnostics.get("objective") or diagnostics.get("objective_result") or diagnostics.get("plan")
    ):
        return {
            "search_objective": diagnostics.get("objective", {}),
            "search_objective_result": diagnostics.get("objective_result", {}),
            "plan": diagnostics.get("plan", {}),
            "plan_id": diagnostics.get("plan_id", ""),
            "plan_update_reason": diagnostics.get("plan_update_reason", ""),
        }
    proposal = getattr(policy, "last_proposal", None)
    plan = getattr(policy, "last_plan", None)
    if proposal is None and plan is None:
        return {
            "search_objective": {},
            "search_objective_result": {},
            "plan": {},
            "plan_id": "",
            "plan_update_reason": "",
        }
    return {
        "search_objective": {} if proposal is None else getattr(proposal, "objective_dict", {}),
        "search_objective_result": {} if proposal is None else getattr(proposal, "objective_result_dict", {}),
        "plan": {} if plan is None else plan.to_dict(),
        "plan_id": "" if plan is None else plan.plan_id,
        "plan_update_reason": "" if plan is None else plan.update_reason,
    }


@dataclass(frozen=True)
class RealtimeArenaMatchResult:
    seed: int
    winner: str | None
    ticks: int
    score_player_0: int
    score_player_1: int
    sent_ojama_player_0: int
    sent_ojama_player_1: int
    received_ojama_player_0: int
    received_ojama_player_1: int
    generated_ojama_player_0: int
    generated_ojama_player_1: int
    canceled_ojama_player_0: int
    canceled_ojama_player_1: int
    max_chain_player_0: int
    max_chain_player_1: int
    decisions_player_0: int
    decisions_player_1: int
    timeouts_player_0: int
    timeouts_player_1: int
    deadline_misses_player_0: int
    deadline_misses_player_1: int
    unreachable_plans_player_0: int
    unreachable_plans_player_1: int
    replans_player_0: int
    replans_player_1: int
    emitted_input_ticks_player_0: int
    emitted_input_ticks_player_1: int
    idle_ticks_player_0: int
    idle_ticks_player_1: int
    mean_policy_elapsed_ms_player_0: float
    mean_policy_elapsed_ms_player_1: float
    mean_inference_latency_ticks_player_0: float
    mean_inference_latency_ticks_player_1: float
    final_hash: str
    policy_a_side: str = "player_0"
    replay: Mapping[str, Any] | None = field(default=None, compare=False, repr=False)

    @property
    def score_for_player_0(self) -> float:
        if self.winner == "player_0":
            return 1.0
        if self.winner == "player_1":
            return 0.0
        return 0.5

    @property
    def score_for_policy_a(self) -> float:
        score = self.score_for_player_0
        return score if self.policy_a_side == "player_0" else 1.0 - score


@dataclass(frozen=True)
class RealtimeArenaResult:
    matches: tuple[RealtimeArenaMatchResult, ...]

    @property
    def wins_player_0(self) -> int:
        return sum(1 for match in self.matches if match.winner == "player_0")

    @property
    def wins_player_1(self) -> int:
        return sum(1 for match in self.matches if match.winner == "player_1")

    @property
    def draws(self) -> int:
        return sum(1 for match in self.matches if match.winner is None)

    @property
    def wins_policy_a(self) -> int:
        return sum(1 for match in self.matches if match.score_for_policy_a == 1.0)

    @property
    def win_rate_policy_a(self) -> float:
        if not self.matches:
            return 0.0
        return self.wins_policy_a / len(self.matches)

    @property
    def mean_ticks(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.ticks for match in self.matches) / len(self.matches)


def run_realtime_match(
    policy_player_0: Policy,
    policy_player_1: Policy,
    *,
    seed: int,
    max_ticks: int = 10_000,
    decision_config: RealtimeDecisionConfig | None = None,
    record_replay: bool = False,
) -> RealtimeArenaMatchResult:
    env = RealtimePuyoEnv(seed=seed, max_ticks=max_ticks)
    observations, infos = env.reset(seed=seed)
    controllers = {
        "player_0": RealtimePolicyController(policy_player_0, config=decision_config),
        "player_1": RealtimePolicyController(policy_player_1, config=decision_config),
    }
    replay_ticks: list[dict[str, Any]] = []
    last_infos = infos
    while env.agents:
        inputs: dict[str, TickInput] = {}
        for agent in env.agents:
            inputs[agent] = controllers[agent].next_input(
                env.match,
                agent,
                observations[agent],
                infos[agent],
            )
        observations, _, _, _, infos = env.step(inputs)
        last_infos = infos
        if record_replay:
            match_result = infos["player_0"]["match_result"]
            replay_ticks.append(
                {
                    "tick": match_result.tick,
                    "inputs": {
                        agent: tick_input.to_json()
                        for agent, tick_input in sorted(inputs.items())
                    },
                    "policy_diagnostics": {
                        "player_0": _policy_search_objective_diagnostics(policy_player_0),
                        "player_1": _policy_search_objective_diagnostics(policy_player_1),
                    },
                    "snapshot_hash": match_result.snapshot_hash,
                }
            )

    info_0 = last_infos["player_0"]
    info_1 = last_infos["player_1"]
    diag_0 = controllers["player_0"].diagnostics
    diag_1 = controllers["player_1"].diagnostics
    replay = None
    if record_replay:
        replay = {
            "format": "puyo-realtime-match-v1",
            "seed": seed,
            "max_ticks": max_ticks,
            "ticks": replay_ticks,
            "expected_final_hash": env.match.state_hash(),
        }
    return RealtimeArenaMatchResult(
        seed=seed,
        winner=info_0.get("winner"),
        ticks=int(info_0.get("tick_count", max_ticks)),
        score_player_0=int(info_0["score"]),
        score_player_1=int(info_1["score"]),
        sent_ojama_player_0=int(info_0["sent_ojama_total"]),
        sent_ojama_player_1=int(info_1["sent_ojama_total"]),
        received_ojama_player_0=int(info_0["received_ojama_total"]),
        received_ojama_player_1=int(info_1["received_ojama_total"]),
        generated_ojama_player_0=int(info_0["generated_ojama_total"]),
        generated_ojama_player_1=int(info_1["generated_ojama_total"]),
        canceled_ojama_player_0=int(info_0["canceled_ojama_total"]),
        canceled_ojama_player_1=int(info_1["canceled_ojama_total"]),
        max_chain_player_0=int(info_0.get("max_chain_count", 0)),
        max_chain_player_1=int(info_1.get("max_chain_count", 0)),
        decisions_player_0=diag_0.decisions_started,
        decisions_player_1=diag_1.decisions_started,
        timeouts_player_0=diag_0.timeouts,
        timeouts_player_1=diag_1.timeouts,
        deadline_misses_player_0=diag_0.deadline_misses,
        deadline_misses_player_1=diag_1.deadline_misses,
        unreachable_plans_player_0=diag_0.unreachable_plans,
        unreachable_plans_player_1=diag_1.unreachable_plans,
        replans_player_0=diag_0.replans,
        replans_player_1=diag_1.replans,
        emitted_input_ticks_player_0=diag_0.emitted_input_ticks,
        emitted_input_ticks_player_1=diag_1.emitted_input_ticks,
        idle_ticks_player_0=diag_0.idle_ticks,
        idle_ticks_player_1=diag_1.idle_ticks,
        mean_policy_elapsed_ms_player_0=diag_0.mean_policy_elapsed_ms,
        mean_policy_elapsed_ms_player_1=diag_1.mean_policy_elapsed_ms,
        mean_inference_latency_ticks_player_0=diag_0.mean_inference_latency_ticks,
        mean_inference_latency_ticks_player_1=diag_1.mean_inference_latency_ticks,
        final_hash=env.match.state_hash(),
        replay=replay,
    )


def run_realtime_series(
    policy_player_0: Policy,
    policy_player_1: Policy,
    *,
    games: int = 20,
    seed: int = 1,
    max_ticks: int = 10_000,
    decision_config: RealtimeDecisionConfig | None = None,
) -> RealtimeArenaResult:
    return RealtimeArenaResult(
        matches=tuple(
            run_realtime_match(
                policy_player_0,
                policy_player_1,
                seed=seed + game_index,
                max_ticks=max_ticks,
                decision_config=decision_config,
            )
            for game_index in range(games)
        )
    )


def run_realtime_paired_series(
    policy_a: Policy,
    policy_b: Policy,
    *,
    games: int = 20,
    seed: int = 1,
    max_ticks: int = 10_000,
    decision_config: RealtimeDecisionConfig | None = None,
) -> RealtimeArenaResult:
    matches = []
    for game_index in range(games):
        match_seed = seed + game_index
        matches.append(
            run_realtime_match(
                policy_a,
                policy_b,
                seed=match_seed,
                max_ticks=max_ticks,
                decision_config=decision_config,
            )
        )
        swapped = run_realtime_match(
            policy_b,
            policy_a,
            seed=match_seed,
            max_ticks=max_ticks,
            decision_config=decision_config,
        )
        matches.append(replace(swapped, policy_a_side="player_1"))
    return RealtimeArenaResult(matches=tuple(matches))


def replay_realtime_match(replay: Mapping[str, Any]) -> str:
    from puyo_env.realtime_versus import RealtimeVersusMatch

    match = RealtimeVersusMatch(seed=replay.get("seed"))
    for entry in replay.get("ticks", ()):
        inputs = {
            agent: TickInput.from_names(
                press=payload.get("press", ()),
                release=payload.get("release", ()),
            )
            for agent, payload in entry.get("inputs", {}).items()
        }
        result = match.step(inputs)
        expected_hash = entry.get("snapshot_hash")
        if expected_hash is not None and expected_hash != result.snapshot_hash:
            raise AssertionError(
                f"snapshot hash mismatch at tick {result.tick}: "
                f"expected {expected_hash}, got {result.snapshot_hash}"
            )
    expected_final_hash = replay.get("expected_final_hash")
    final_hash = match.state_hash()
    if expected_final_hash is not None and final_hash != expected_final_hash:
        raise AssertionError(
            f"final hash mismatch: expected {expected_final_hash}, got {final_hash}"
        )
    return final_hash


def write_realtime_replay(path: str | Path, replay: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(replay, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def summarize_realtime_result(
    result: RealtimeArenaResult,
    *,
    label: str,
    policy_a: str,
    policy_b: str,
    games: int,
    seed: int,
    max_ticks: int,
) -> dict[str, Any]:
    score_mean, score_low, score_high = _policy_a_confidence_interval(result)
    return {
        "label": label,
        "policy_a": policy_a,
        "policy_b": policy_b,
        "games": games,
        "seed": seed,
        "max_ticks": max_ticks,
        "wins_player_0": result.wins_player_0,
        "wins_player_1": result.wins_player_1,
        "draws": result.draws,
        "wins_policy_a": result.wins_policy_a,
        "win_rate_policy_a": result.win_rate_policy_a,
        "score_rate_policy_a": score_mean,
        "score_rate_policy_a_ci95_low": score_low,
        "score_rate_policy_a_ci95_high": score_high,
        "mean_ticks": result.mean_ticks,
        "mean_decisions_policy_a": _mean_policy_side_metric(result, "decisions"),
        "mean_timeouts_policy_a": _mean_policy_side_metric(result, "timeouts"),
        "mean_deadline_misses_policy_a": _mean_policy_side_metric(result, "deadline_misses"),
        "mean_unreachable_plans_policy_a": _mean_policy_side_metric(result, "unreachable_plans"),
        "mean_replans_policy_a": _mean_policy_side_metric(result, "replans"),
        "mean_emitted_input_ticks_policy_a": _mean_policy_side_metric(result, "emitted_input_ticks"),
        "mean_idle_ticks_policy_a": _mean_policy_side_metric(result, "idle_ticks"),
        "mean_policy_elapsed_ms_policy_a": _mean_policy_side_metric(result, "mean_policy_elapsed_ms"),
        "mean_inference_latency_ticks_policy_a": _mean_policy_side_metric(
            result,
            "mean_inference_latency_ticks",
        ),
    }


def _mean_policy_side_metric(result: RealtimeArenaResult, stem: str) -> float:
    if not result.matches:
        return 0.0
    values = []
    for match in result.matches:
        suffix = "player_0" if match.policy_a_side == "player_0" else "player_1"
        values.append(float(getattr(match, f"{stem}_{suffix}")))
    return sum(values) / len(values)


def _policy_a_confidence_interval(result: RealtimeArenaResult) -> tuple[float, float, float]:
    scores = [match.score_for_policy_a for match in result.matches]
    if not scores:
        return 0.0, 0.0, 0.0
    mean = sum(scores) / len(scores)
    if len(scores) == 1:
        return mean, mean, mean
    variance = sum((score - mean) ** 2 for score in scores) / (len(scores) - 1)
    margin = 1.96 * math.sqrt(variance / len(scores))
    return mean, max(0.0, mean - margin), min(1.0, mean + margin)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate two policies in the realtime arena.")
    policy_choices = [
        "first", "random", "greedy", "beam", "checkpoint", "manager", "manager_rule",
        "worker_large", "worker_quick", "worker_punish", "worker_counter",
        "worker_fire", "worker_fire_max", "worker_survival",
    ]
    parser.add_argument("--policy-a", choices=policy_choices, default="first")
    parser.add_argument("--policy-b", choices=policy_choices, default="random")
    parser.add_argument("--checkpoint-a", default=None)
    parser.add_argument("--checkpoint-b", default=None)
    parser.add_argument("--games", type=int, default=4)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-ticks", type=int, default=10_000)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--beam-depth", type=int, default=10)
    parser.add_argument("--beam-width", type=int, default=48)
    parser.add_argument("--beam-scenarios", type=int, default=1)
    parser.add_argument("--beam-minimum-chain", type=int, default=6)
    parser.add_argument("--inference-latency-ticks", type=int, default=0)
    parser.add_argument("--timeout-ticks", type=int, default=None)
    parser.add_argument("--action-deadline-ticks", type=int, default=None)
    parser.add_argument("--paired-sides", action="store_true")
    parser.add_argument("--replay", type=Path, default=None, help="Write a replay for one policy_a/player_0 match.")
    return parser.parse_args(argv)


def _policy_spec_from_args(args, side: str) -> dict[str, Any]:
    return dict(
        policy_type=getattr(args, f"policy_{side}"),
        seed=args.seed + (0 if side == "a" else 10_000),
        checkpoint_path=getattr(args, f"checkpoint_{side}"),
        device=args.device,
        deterministic=args.deterministic,
        beam_depth=args.beam_depth,
        beam_width=args.beam_width,
        beam_scenarios=args.beam_scenarios,
        beam_minimum_chain=args.beam_minimum_chain,
    )


def main(argv=None):
    args = parse_args(argv)
    decision_config = RealtimeDecisionConfig(
        inference_latency_ticks=args.inference_latency_ticks,
        timeout_ticks=args.timeout_ticks,
        action_deadline_ticks=args.action_deadline_ticks,
    )
    policy_a = make_policy(**_policy_spec_from_args(args, "a"))
    policy_b = make_policy(**_policy_spec_from_args(args, "b"))
    if args.replay is not None:
        match = run_realtime_match(
            policy_a,
            policy_b,
            seed=args.seed,
            max_ticks=args.max_ticks,
            decision_config=decision_config,
            record_replay=True,
        )
        write_realtime_replay(args.replay, match.replay or {})
        result = RealtimeArenaResult(matches=(match,))
    else:
        runner = run_realtime_paired_series if args.paired_sides else run_realtime_series
        result = runner(
            policy_a,
            policy_b,
            games=args.games,
            seed=args.seed,
            max_ticks=args.max_ticks,
            decision_config=decision_config,
        )
    summary = summarize_realtime_result(
        result,
        label=f"realtime_{args.policy_a}_vs_{args.policy_b}",
        policy_a=args.policy_a,
        policy_b=args.policy_b,
        games=len(result.matches),
        seed=args.seed,
        max_ticks=args.max_ticks,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
