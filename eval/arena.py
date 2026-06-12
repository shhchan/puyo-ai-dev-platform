"""Headless arena for evaluating two Puyo policies."""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from puyo_env.versus_env import VersusPuyoEnv
from selfplay.rating import update_elo
from selfplay.policies import Policy, make_policy


@dataclass(frozen=True)
class MatchResult:
    seed: int
    winner: str | None
    steps: int
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
    policy_a_side: str = "player_0"
    mean_decision_ms_player_0: float = 0.0
    mean_decision_ms_player_1: float = 0.0
    mean_expanded_nodes_player_0: float = 0.0
    mean_expanded_nodes_player_1: float = 0.0
    strategy_switches_player_0: int = 0
    strategy_switches_player_1: int = 0
    profile_counts_player_0: str = "{}"
    profile_counts_player_1: str = "{}"
    switch_reasons_player_0: str = "{}"
    switch_reasons_player_1: str = "{}"
    mean_target_attack_player_0: float = 0.0
    mean_target_attack_player_1: float = 0.0
    mean_incoming_attack_player_0: float = 0.0
    mean_incoming_attack_player_1: float = 0.0
    missed_lethal_player_0: int = 0
    missed_lethal_player_1: int = 0
    failed_counter_player_0: int = 0
    failed_counter_player_1: int = 0

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

    @property
    def decision_ms_for_policy_a(self) -> float:
        return (
            self.mean_decision_ms_player_0
            if self.policy_a_side == "player_0"
            else self.mean_decision_ms_player_1
        )

    @property
    def expanded_nodes_for_policy_a(self) -> float:
        return (
            self.mean_expanded_nodes_player_0
            if self.policy_a_side == "player_0"
            else self.mean_expanded_nodes_player_1
        )

    @property
    def switches_for_policy_a(self) -> int:
        return (
            self.strategy_switches_player_0
            if self.policy_a_side == "player_0"
            else self.strategy_switches_player_1
        )


@dataclass
class _PolicyDiagnostics:
    decisions: int = 0
    elapsed_seconds: float = 0.0
    expanded_nodes: int = 0
    switches: int = 0
    previous_profile: int | None = None
    profile_counts: dict[str, int] = field(default_factory=dict)
    switch_reasons: dict[str, int] = field(default_factory=dict)
    target_attack_total: int = 0
    incoming_attack_total: int = 0
    missed_lethal: int = 0
    failed_counter: int = 0

    def record(self, policy: Policy) -> None:
        proposal = getattr(policy, "last_proposal", None)
        if proposal is not None:
            self.decisions += 1
            self.elapsed_seconds += float(proposal.elapsed_seconds)
            self.expanded_nodes += int(proposal.expanded_nodes)
            name = str(proposal.profile_name)
            self.profile_counts[name] = self.profile_counts.get(name, 0) + 1
            reason = str(proposal.reason or "unspecified")
            self.switch_reasons[reason] = self.switch_reasons.get(reason, 0) + 1
            self.target_attack_total += int(proposal.target_attack)
            self.incoming_attack_total += int(proposal.incoming_attack)
            if proposal.strategy == "punish" and proposal.predicted_attack < proposal.target_attack:
                self.missed_lethal += 1
            if proposal.strategy == "counter" and proposal.predicted_attack < proposal.target_attack:
                self.failed_counter += 1
            if self.previous_profile is not None and proposal.profile_id != self.previous_profile:
                self.switches += 1
            self.previous_profile = int(proposal.profile_id)
            return
        diagnostics = getattr(policy, "last_diagnostics", None)
        if diagnostics is not None:
            self.decisions += 1
            self.elapsed_seconds += float(diagnostics.elapsed_seconds)
            self.expanded_nodes += int(diagnostics.expanded_nodes)

    @property
    def mean_decision_ms(self) -> float:
        return 0.0 if self.decisions == 0 else self.elapsed_seconds * 1000.0 / self.decisions

    @property
    def mean_expanded_nodes(self) -> float:
        return 0.0 if self.decisions == 0 else self.expanded_nodes / self.decisions

    @property
    def mean_target_attack(self) -> float:
        return 0.0 if self.decisions == 0 else self.target_attack_total / self.decisions

    @property
    def mean_incoming_attack(self) -> float:
        return 0.0 if self.decisions == 0 else self.incoming_attack_total / self.decisions


@dataclass(frozen=True)
class ArenaResult:
    matches: tuple[MatchResult, ...]

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
    def win_rate_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return self.wins_player_0 / len(self.matches)

    @property
    def wins_policy_a(self) -> int:
        return sum(1 for match in self.matches if match.score_for_policy_a == 1.0)

    @property
    def wins_policy_b(self) -> int:
        return sum(1 for match in self.matches if match.score_for_policy_a == 0.0)

    @property
    def win_rate_policy_a(self) -> float:
        if not self.matches:
            return 0.0
        return self.wins_policy_a / len(self.matches)

    @property
    def mean_score_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.score_player_0 for match in self.matches) / len(self.matches)

    @property
    def mean_score_player_1(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.score_player_1 for match in self.matches) / len(self.matches)

    @property
    def mean_steps(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.steps for match in self.matches) / len(self.matches)

    @property
    def mean_max_chain_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.max_chain_player_0 for match in self.matches) / len(self.matches)

    @property
    def mean_max_chain_player_1(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.max_chain_player_1 for match in self.matches) / len(self.matches)

    @property
    def mean_sent_ojama_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.sent_ojama_player_0 for match in self.matches) / len(self.matches)

    @property
    def mean_sent_ojama_player_1(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.sent_ojama_player_1 for match in self.matches) / len(self.matches)

    @property
    def mean_received_ojama_player_0(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.received_ojama_player_0 for match in self.matches) / len(self.matches)

    @property
    def mean_received_ojama_player_1(self) -> float:
        if not self.matches:
            return 0.0
        return sum(match.received_ojama_player_1 for match in self.matches) / len(self.matches)


def run_match(
    policy_player_0: Policy,
    policy_player_1: Policy,
    *,
    seed: int,
    max_steps: int = 500,
) -> MatchResult:
    env = VersusPuyoEnv(seed=seed, max_steps=max_steps)
    observations, infos = env.reset(seed=seed)
    diagnostics = {"player_0": _PolicyDiagnostics(), "player_1": _PolicyDiagnostics()}

    while env.agents:
        actions = {}
        for agent in env.agents:
            policy = policy_player_0 if agent == "player_0" else policy_player_1
            actions[agent] = policy.select_action(observations[agent], infos[agent])
            diagnostics[agent].record(policy)
        observations, _, _, _, infos = env.step(actions)

    info_0 = infos["player_0"]
    info_1 = infos["player_1"]
    return MatchResult(
        seed=seed,
        winner=info_0.get("winner"),
        steps=int(info_0.get("step_count", max_steps)),
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
        max_chain_player_0=int(info_0["max_chain_count"]),
        max_chain_player_1=int(info_1["max_chain_count"]),
        mean_decision_ms_player_0=diagnostics["player_0"].mean_decision_ms,
        mean_decision_ms_player_1=diagnostics["player_1"].mean_decision_ms,
        mean_expanded_nodes_player_0=diagnostics["player_0"].mean_expanded_nodes,
        mean_expanded_nodes_player_1=diagnostics["player_1"].mean_expanded_nodes,
        strategy_switches_player_0=diagnostics["player_0"].switches,
        strategy_switches_player_1=diagnostics["player_1"].switches,
        profile_counts_player_0=json.dumps(diagnostics["player_0"].profile_counts, sort_keys=True),
        profile_counts_player_1=json.dumps(diagnostics["player_1"].profile_counts, sort_keys=True),
        switch_reasons_player_0=json.dumps(diagnostics["player_0"].switch_reasons, sort_keys=True),
        switch_reasons_player_1=json.dumps(diagnostics["player_1"].switch_reasons, sort_keys=True),
        mean_target_attack_player_0=diagnostics["player_0"].mean_target_attack,
        mean_target_attack_player_1=diagnostics["player_1"].mean_target_attack,
        mean_incoming_attack_player_0=diagnostics["player_0"].mean_incoming_attack,
        mean_incoming_attack_player_1=diagnostics["player_1"].mean_incoming_attack,
        missed_lethal_player_0=diagnostics["player_0"].missed_lethal,
        missed_lethal_player_1=diagnostics["player_1"].missed_lethal,
        failed_counter_player_0=diagnostics["player_0"].failed_counter,
        failed_counter_player_1=diagnostics["player_1"].failed_counter,
    )


def run_series(
    policy_player_0: Policy,
    policy_player_1: Policy,
    *,
    games: int = 20,
    seed: int = 1,
    max_steps: int = 500,
) -> ArenaResult:
    matches = tuple(
        run_match(
            policy_player_0,
            policy_player_1,
            seed=seed + game_index,
            max_steps=max_steps,
        )
        for game_index in range(games)
    )
    return ArenaResult(matches=matches)


def run_paired_series(
    policy_a: Policy,
    policy_b: Policy,
    *,
    games: int = 20,
    seed: int = 1,
    max_steps: int = 500,
) -> ArenaResult:
    """Evaluate each seed with both policy-to-side assignments."""

    matches = []
    for game_index in range(games):
        match_seed = seed + game_index
        matches.append(run_match(policy_a, policy_b, seed=match_seed, max_steps=max_steps))
        swapped = run_match(policy_b, policy_a, seed=match_seed, max_steps=max_steps)
        matches.append(replace(swapped, policy_a_side="player_1"))
    return ArenaResult(matches=tuple(matches))


def write_matches_csv(path: str | Path, matches: tuple[MatchResult, ...]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[item.name for item in fields(MatchResult)])
        writer.writeheader()
        for match in matches:
            writer.writerow(asdict(match))


def elo_after_matches(
    matches: tuple[MatchResult, ...],
    *,
    rating_player_0: float = 1000.0,
    rating_player_1: float = 1000.0,
    k_factor: float = 32.0,
) -> tuple[float, float]:
    rating_0 = rating_player_0
    rating_1 = rating_player_1
    for match in matches:
        rating_0, rating_1 = update_elo(
            rating_0,
            rating_1,
            match.score_for_policy_a,
            k_factor=k_factor,
        )
    return rating_0, rating_1


def summarize_result(
    result: ArenaResult,
    *,
    label: str,
    policy_a: str,
    policy_b: str,
    checkpoint_a: str | None,
    checkpoint_b: str | None,
    games: int,
    seed: int,
    max_steps: int,
    rating_a: float = 1000.0,
    rating_b: float = 1000.0,
    k_factor: float = 32.0,
) -> dict[str, Any]:
    final_rating_a, final_rating_b = elo_after_matches(
        result.matches,
        rating_player_0=rating_a,
        rating_player_1=rating_b,
        k_factor=k_factor,
    )
    score_mean, score_low, score_high = _policy_a_confidence_interval(result)
    return {
        "label": label,
        "policy_a": policy_a,
        "policy_b": policy_b,
        "checkpoint_a": checkpoint_a or "",
        "checkpoint_b": checkpoint_b or "",
        "games": games,
        "seed": seed,
        "max_steps": max_steps,
        "wins_player_0": result.wins_player_0,
        "wins_player_1": result.wins_player_1,
        "draws": result.draws,
        "win_rate_player_0": result.win_rate_player_0,
        "wins_policy_a": result.wins_policy_a,
        "wins_policy_b": result.wins_policy_b,
        "win_rate_policy_a": result.win_rate_policy_a,
        "score_rate_policy_a": score_mean,
        "score_rate_policy_a_ci95_low": score_low,
        "score_rate_policy_a_ci95_high": score_high,
        "mean_steps": result.mean_steps,
        "mean_score_player_0": result.mean_score_player_0,
        "mean_score_player_1": result.mean_score_player_1,
        "mean_max_chain_player_0": result.mean_max_chain_player_0,
        "mean_max_chain_player_1": result.mean_max_chain_player_1,
        "mean_sent_ojama_player_0": result.mean_sent_ojama_player_0,
        "mean_sent_ojama_player_1": result.mean_sent_ojama_player_1,
        "mean_received_ojama_player_0": result.mean_received_ojama_player_0,
        "mean_received_ojama_player_1": result.mean_received_ojama_player_1,
        "mean_generated_ojama_player_0": _mean_match_metric(result, "generated_ojama_player_0"),
        "mean_generated_ojama_player_1": _mean_match_metric(result, "generated_ojama_player_1"),
        "mean_canceled_ojama_player_0": _mean_match_metric(result, "canceled_ojama_player_0"),
        "mean_canceled_ojama_player_1": _mean_match_metric(result, "canceled_ojama_player_1"),
        "mean_decision_ms_player_0": _mean_match_metric(result, "mean_decision_ms_player_0"),
        "mean_decision_ms_player_1": _mean_match_metric(result, "mean_decision_ms_player_1"),
        "mean_expanded_nodes_player_0": _mean_match_metric(result, "mean_expanded_nodes_player_0"),
        "mean_expanded_nodes_player_1": _mean_match_metric(result, "mean_expanded_nodes_player_1"),
        "mean_strategy_switches_player_0": _mean_match_metric(result, "strategy_switches_player_0"),
        "mean_strategy_switches_player_1": _mean_match_metric(result, "strategy_switches_player_1"),
        "mean_decision_ms_policy_a": _mean_policy_a_metric(result, "decision_ms_for_policy_a"),
        "mean_expanded_nodes_policy_a": _mean_policy_a_metric(result, "expanded_nodes_for_policy_a"),
        "mean_strategy_switches_policy_a": _mean_policy_a_metric(result, "switches_for_policy_a"),
        "mean_target_attack_policy_a": _mean_policy_side_metric(result, "mean_target_attack"),
        "mean_incoming_attack_policy_a": _mean_policy_side_metric(result, "mean_incoming_attack"),
        "mean_missed_lethal_policy_a": _mean_policy_side_metric(result, "missed_lethal"),
        "mean_failed_counter_policy_a": _mean_policy_side_metric(result, "failed_counter"),
        "initial_rating_player_0": rating_a,
        "initial_rating_player_1": rating_b,
        "final_rating_player_0": final_rating_a,
        "final_rating_player_1": final_rating_b,
        "final_rating_policy_a": final_rating_a,
        "final_rating_policy_b": final_rating_b,
        "elo_delta_player_0": final_rating_a - rating_a,
        "k_factor": k_factor,
    }


def _mean_match_metric(result: ArenaResult, name: str) -> float:
    if not result.matches:
        return 0.0
    return sum(float(getattr(match, name)) for match in result.matches) / len(result.matches)


def _mean_policy_a_metric(result: ArenaResult, name: str) -> float:
    if not result.matches:
        return 0.0
    return sum(float(getattr(match, name)) for match in result.matches) / len(result.matches)


def _mean_policy_side_metric(result: ArenaResult, stem: str) -> float:
    if not result.matches:
        return 0.0
    values = []
    for match in result.matches:
        suffix = "player_0" if match.policy_a_side == "player_0" else "player_1"
        values.append(float(getattr(match, f"{stem}_{suffix}")))
    return sum(values) / len(values)


def _policy_a_confidence_interval(result: ArenaResult) -> tuple[float, float, float]:
    scores = [match.score_for_policy_a for match in result.matches]
    if not scores:
        return 0.0, 0.0, 0.0
    mean = sum(scores) / len(scores)
    if len(scores) == 1:
        return mean, mean, mean
    variance = sum((score - mean) ** 2 for score in scores) / (len(scores) - 1)
    margin = 1.96 * math.sqrt(variance / len(scores))
    return mean, max(0.0, mean - margin), min(1.0, mean + margin)


def write_summary_csv(path: str | Path, summary: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)


def write_markdown_report(path: str | Path, summary: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("games", summary["games"]),
        ("wins_player_0", summary["wins_player_0"]),
        ("wins_player_1", summary["wins_player_1"]),
        ("draws", summary["draws"]),
        ("win_rate_policy_a", f"{summary['win_rate_policy_a']:.3f}"),
        (
            "score_rate_policy_a_ci95",
            f"{summary['score_rate_policy_a']:.3f} "
            f"[{summary['score_rate_policy_a_ci95_low']:.3f}, {summary['score_rate_policy_a_ci95_high']:.3f}]",
        ),
        ("mean_score_player_0", f"{summary['mean_score_player_0']:.2f}"),
        ("mean_score_player_1", f"{summary['mean_score_player_1']:.2f}"),
        ("mean_max_chain_player_0", f"{summary['mean_max_chain_player_0']:.2f}"),
        ("mean_max_chain_player_1", f"{summary['mean_max_chain_player_1']:.2f}"),
        ("mean_decision_ms_policy_a", f"{summary['mean_decision_ms_policy_a']:.2f}"),
        ("mean_expanded_nodes_policy_a", f"{summary['mean_expanded_nodes_policy_a']:.2f}"),
        ("mean_strategy_switches_policy_a", f"{summary['mean_strategy_switches_policy_a']:.2f}"),
        ("mean_missed_lethal_policy_a", f"{summary['mean_missed_lethal_policy_a']:.2f}"),
        ("mean_failed_counter_policy_a", f"{summary['mean_failed_counter_policy_a']:.2f}"),
        ("elo_delta_policy_a", f"{summary['elo_delta_player_0']:.2f}"),
    ]
    lines = [
        f"# Arena Report: {summary['label']}",
        "",
        f"- policy_a: `{summary['policy_a']}`",
        f"- policy_b: `{summary['policy_b']}`",
        f"- checkpoint_a: `{summary['checkpoint_a'] or '-'}`",
        f"- checkpoint_b: `{summary['checkpoint_b'] or '-'}`",
        f"- seed: `{summary['seed']}`",
        f"- max_steps: `{summary['max_steps']}`",
        "",
        "| metric | value |",
        "|---|---:|",
    ]
    lines.extend(f"| {key} | {value} |" for key, value in rows)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _policy_from_args(args, side: str) -> Policy:
    return make_policy(
        getattr(args, f"policy_{side}"),
        seed=args.seed + (0 if side == "a" else 10_000),
        checkpoint_path=getattr(args, f"checkpoint_{side}"),
        device=args.device,
        deterministic=args.deterministic,
        beam_depth=args.beam_depth,
        beam_width=args.beam_width,
        beam_scenarios=args.beam_scenarios,
        beam_minimum_chain=args.beam_minimum_chain,
    )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Evaluate two Puyo policies headlessly.")
    policy_choices = [
        "first", "random", "greedy", "beam", "checkpoint", "manager", "manager_rule",
        "worker_large", "worker_quick", "worker_punish", "worker_counter",
        "worker_fire", "worker_fire_max", "worker_survival",
    ]
    parser.add_argument("--policy-a", choices=policy_choices, default="greedy")
    parser.add_argument("--policy-b", choices=policy_choices, default="random")
    parser.add_argument("--checkpoint-a", default=None)
    parser.add_argument("--checkpoint-b", default=None)
    parser.add_argument("--games", type=int, default=20)
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--max-steps", type=int, default=500)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--deterministic", action="store_true", default=True)
    parser.add_argument("--beam-depth", type=int, default=10)
    parser.add_argument("--beam-width", type=int, default=48)
    parser.add_argument("--beam-scenarios", type=int, default=1)
    parser.add_argument("--beam-minimum-chain", type=int, default=6)
    parser.add_argument("--csv", default=None, help="Optional path to write per-match results.")
    parser.add_argument("--summary-csv", default=None, help="Optional path to write one-row aggregate metrics.")
    parser.add_argument("--markdown", default=None, help="Optional path to write a Markdown arena report.")
    parser.add_argument("--label", default=None, help="Label stored in aggregate reports.")
    parser.add_argument("--rating-a", type=float, default=1000.0)
    parser.add_argument("--rating-b", type=float, default=1000.0)
    parser.add_argument("--k-factor", type=float, default=32.0)
    parser.add_argument("--paired-sides", action="store_true", help="Run every seed with both side assignments.")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    runner = run_paired_series if args.paired_sides else run_series
    result = runner(
        _policy_from_args(args, "a"),
        _policy_from_args(args, "b"),
        games=args.games,
        seed=args.seed,
        max_steps=args.max_steps,
    )
    if args.csv:
        write_matches_csv(args.csv, result.matches)
    label = args.label or f"{args.policy_a}_vs_{args.policy_b}"
    summary = summarize_result(
        result,
        label=label,
        policy_a=args.policy_a,
        policy_b=args.policy_b,
        checkpoint_a=args.checkpoint_a,
        checkpoint_b=args.checkpoint_b,
        games=len(result.matches),
        seed=args.seed,
        max_steps=args.max_steps,
        rating_a=args.rating_a,
        rating_b=args.rating_b,
        k_factor=args.k_factor,
    )
    if args.summary_csv:
        write_summary_csv(args.summary_csv, summary)
    if args.markdown:
        write_markdown_report(args.markdown, summary)

    total = len(result.matches)
    print(f"games: {total}")
    print(f"player_0_wins: {result.wins_player_0}")
    print(f"player_1_wins: {result.wins_player_1}")
    print(f"draws: {result.draws}")
    print(f"player_0_win_rate: {result.win_rate_player_0:.3f}")
    print(f"policy_a_win_rate: {result.win_rate_policy_a:.3f}")
    print(f"mean_score_player_0: {result.mean_score_player_0:.2f}")
    print(f"mean_score_player_1: {result.mean_score_player_1:.2f}")
    print(f"mean_max_chain_player_0: {result.mean_max_chain_player_0:.2f}")
    print(f"mean_max_chain_player_1: {result.mean_max_chain_player_1:.2f}")
    print(f"final_rating_policy_a: {summary['final_rating_policy_a']:.2f}")
    print(f"elo_delta_policy_a: {summary['elo_delta_player_0']:.2f}")


if __name__ == "__main__":
    main()
