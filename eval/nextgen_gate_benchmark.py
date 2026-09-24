"""Frozen, resumable nextgen diagnostic measurement (one process per identity).

Examples: --output runs/255 init; --output runs/255 safe --seed 123 --repeat 1
A smoke run retains all 60 identities but cannot qualify the reference G2 gate.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.metadata
import json
import os
import platform
import resource
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from pathlib import Path

from agents import nextgen_contracts as c
from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy, RuleTacticSelector
from eval.nextgen_gates import (
    REPEATS,
    SEEDS,
    THRESHOLDS,
    digest,
    distribution,
    evaluate,
    throughput_estimate,
)
from puyo_env.realtime_ai import RealtimeDecisionConfig, RealtimePolicyController
from puyo_env.realtime_versus import RealtimeVersusMatch
from train.artifacts import file_sha256

ROOT = Path(__file__).resolve().parents[1]


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if path.suffix == ".gz":
        path.write_bytes(gzip.compress(content.encode(), mtime=0))
    else:
        path.write_text(content)


def read(path):
    return json.loads(
        gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_text()
    )


def source_provenance():
    paths = subprocess.check_output(
        ["git", "ls-files", "agents", "puyo_env", "src/core", "eval", "train", "tests"],
        cwd=ROOT,
        text=True,
    ).splitlines()
    # New implementation must be committed before freezing the experiment.
    hashes = {p: file_sha256(ROOT / p) for p in paths if (ROOT / p).is_file()}
    return {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "source_sha256": digest(hashes),
        "files": hashes,
    }


def build_provenance():
    return {
        "backend": "python",
        "native_binary_sha256": None,
        "python": sys.version,
        "python_binary_sha256": file_sha256(Path(sys.executable).resolve()),
        "packages": {
            p: importlib.metadata.version(p)
            for p in ("numpy", "PyYAML", "pygame", "gymnasium")
        },
    }


def initialize(output, *, realtime_clock=False):
    path = output / "manifest.json"
    if path.exists():
        raise ValueError("manifest is immutable; use another output directory")
    if subprocess.check_output(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=ROOT, text=True
    ).strip():
        raise ValueError("commit source before freezing evaluation")
    p = NextgenTacticManagerPolicy(seed=0)
    config = {
        "seeds": list(SEEDS),
        "repeats": list(REPEATS),
        "thresholds": THRESHOLDS,
        "runtime_information": "public_only",
        "reference_profile_calibrated": False,
        "profile": p.profile.to_dict(),
        "search": asdict(p.search_config),
        "template_catalog_digest": p.catalog.semantic_digest,
        "template_binding_budget": p.template_binding_budget,
        "safe_environment": "realtime_safe_no_threat; opponent countdown frozen; outgoing packets suppressed",
        "safe_latency_mode": "configured",
        "seed_streams": {
            "environment": "run_seed",
            "selector": "run_seed",
            "search": "run_seed XOR 0x4E4753",
        },
        "safe_max_ticks": 30000,
        "paired_seeds": [123],
        "paired_max_ticks": 600,
        "paired_modes": ["measured"] if realtime_clock else ["configured", "measured"],
        "paired_execution": "single_worker_realtime_clock"
        if realtime_clock
        else "synchronous",
        "worker_count": 1,
        "native_threads": 1,
        "matrix": {
            "search_only": "same batch, fixed fire_main/build_main selector",
            "rule": "RuleTacticSelector",
            "bootstrap": "BLOCKED: no checkpoint",
            "rl": "BLOCKED: no checkpoint",
            "template_off": "not measured: policy requires enabled catalog",
            "reward_ablation": "not applicable without learning",
        },
        "g4_plan": {
            "paired_seeds_per_opponent": list(range(1000, 1100)),
            "training_seeds": [1, 2, 3, 4, 5],
            "overall_ci_lower_gt": 0,
            "stratum_ci_lower_gte": -0.05,
            "adverse_ci_upper_lte": 0.02,
            "latency_thresholds": None,
            "status": "BLOCKED: human latency thresholds pending",
        },
    }
    build = build_provenance()
    manifest = {
        "schema": "puyo.nextgen.gate_experiment.v1",
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": source_provenance(),
        "build": build,
        "build_sha256": digest(build),
        "config": config,
        "config_sha256": digest(config),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "cpu_count": os.cpu_count(),
            "cpu_model": next(
                (
                    line.split(":", 1)[1].strip()
                    for line in Path("/proc/cpuinfo").read_text().splitlines()
                    if line.startswith("model name")
                ),
                "unknown",
            ),
            "thread_environment": {
                key: os.environ.get(key)
                for key in (
                    "OMP_NUM_THREADS",
                    "OPENBLAS_NUM_THREADS",
                    "MKL_NUM_THREADS",
                )
            },
        },
    }
    manifest["sha256"] = digest(manifest)
    write(path, manifest)
    return manifest


def load_manifest(output, *, execution=False):
    manifest = read(output / "manifest.json")
    if (
        digest({k: v for k, v in manifest.items() if k != "sha256"})
        != manifest["sha256"]
    ):
        raise ValueError("manifest checksum mismatch")
    if (
        digest(manifest["config"]) != manifest["config_sha256"]
        or digest(manifest["build"]) != manifest["build_sha256"]
    ):
        raise ValueError("configuration/build checksum mismatch")
    if execution and (
        source_provenance() != manifest["source"]
        or build_provenance() != manifest["build"]
    ):
        raise ValueError("source/build changed after declaration")
    return manifest


class SearchOnlySelector:
    """Same public batch, no template/response tactical preference."""

    def select(self, request, batch, features, timing):
        rows = {r.tactic_id: r for r in batch.tactics}
        tactic = "fire_main" if rows["fire_main"].available else "build_main"
        if not rows[tactic].available:
            return RuleTacticSelector().select(request, batch, features, timing)
        return c.Selection(
            tactic,
            rows[tactic].best_id,
            batch.digest,
            "rule",
            None,
            None,
            None,
            "search_only_fixed_rank",
        )


class SafeNoThreatMatch(RealtimeVersusMatch):
    """Evaluation-only sandbox. Player 1 is an inert public countdown board."""

    def __init__(self, seed):
        super().__init__(seed=seed)
        opponent = self.player_states["player_1"].simulator.game
        opponent.state = "countdown"
        opponent.countdown_time_left = 1e9

    def schedule_attack(self, *args, **kwargs):
        # A solo safe-build experiment has no opponent attacks in either direction.
        return None


def measure(
    *,
    seed,
    max_ticks,
    safe,
    latency_mode="configured",
    swap=False,
    realtime_clock=False,
):
    policy = NextgenTacticManagerPolicy(seed=seed)
    opponent = NextgenTacticManagerPolicy(seed=seed, selector=SearchOnlySelector())
    policies = [opponent, policy] if swap else [policy, opponent]
    match = SafeNoThreatMatch(seed) if safe else RealtimeVersusMatch(seed=seed)
    executor = ThreadPoolExecutor(max_workers=1) if realtime_clock else None
    controllers = [
        RealtimePolicyController(
            p,
            config=RealtimeDecisionConfig(latency_mode=latency_mode),
            decision_executor=executor,
        )
        for p in policies
    ]
    count = 1 if safe else 2
    seen = [None, None]
    decision_seconds, phase_seconds, chains, inputs = [], {}, [], []
    started, cpu = time.perf_counter(), time.process_time()
    for _ in range(max_ticks):
        tick_inputs = {}
        for i in () if match.ending else range(count):
            tick_inputs[f"player_{i}"] = controllers[i].next_input(match, f"player_{i}")
            context = policies[i].last_context
            if context is not None and id(context) != seen[i]:
                seen[i] = id(context)
                decision_seconds.append(context.trace.elapsed_seconds)
                for entry in context.trace.steps:
                    phase_seconds.setdefault(entry.step_id, []).append(
                        entry.elapsed_seconds
                    )
        result = match.step(tick_inputs)
        inputs.append(
            {
                "tick": result.tick,
                "inputs": {k: v.to_json() for k, v in tick_inputs.items()},
            }
        )
        for event in result.player_results["player_0"].events:
            if event.type == "resolution_complete":
                chains.append(event.data["chain_count"])
        if safe and len(chains) >= 40 or match.finished:
            break
        if realtime_clock:
            remaining = match.tick / match.timing.tick_rate - (
                time.perf_counter() - started
            )
            if remaining > 0:
                time.sleep(remaining)
    if executor is not None:
        executor.shutdown(wait=True, cancel_futures=True)
    elapsed, cpu_seconds = time.perf_counter() - started, time.process_time() - cpu
    ledgers = [
        [d.to_dict() for d in controller.nextgen_scheduler.ledger]
        for controller in controllers[:count]
    ]
    errors = [
        e
        for controller in controllers[:count]
        for e in controller.nextgen_scheduler.errors
    ]
    game_over = match.player_states["player_0"].simulator.game.game_over
    semantic = {
        "inputs": inputs,
        "chains": chains,
        "final_hash": match.state_hash(),
        "selections": [
            [
                {
                    "batch": d.batch.digest,
                    "selection": d.selection.to_dict(),
                    "receipt": d.receipt.to_dict(),
                }
                for d in controller.nextgen_scheduler.ledger
            ]
            for controller in controllers[:count]
        ],
    }
    own_side = int(swap) if not safe else 0
    decisions = [len(ledger) for ledger in ledgers]
    return {
        "seed": seed,
        "execution_mode": "single_worker_realtime_clock"
        if realtime_clock
        else "synchronous",
        "started_decisions": [
            v.diagnostics.decisions_started for v in controllers[:count]
        ],
        "controller_metrics": [v.diagnostics.to_dict() for v in controllers[:count]],
        "latency_mode": latency_mode,
        "policy_a_side": own_side,
        "termination": "game_over"
        if game_over and safe
        else "placements"
        if safe and len(chains) == 40
        else "terminal"
        if match.finished
        else "tick_limit",
        "placements": len(chains),
        "max_chain": max(chains, default=0),
        "premature": sum(0 < n < 10 for n in chains),
        "game_over": game_over,
        "chains": chains,
        "ticks": match.tick,
        "winner": result.winner,
        "semantic_digest": digest(semantic),
        "elapsed_seconds": elapsed,
        "cpu_seconds": cpu_seconds,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rss_peak_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "decision_seconds": decision_seconds,
        "phase_seconds": phase_seconds,
        "decisions": decisions,
        "errors": errors,
        "ledgers": ledgers,
        "replay": semantic,
        "throughput": throughput_estimate(
            decisions[own_side], decisions[1 - own_side] if not safe else 0, elapsed
        ),
    }


def run_safe(output, seed, repeat):
    manifest = load_manifest(output, execution=True)
    path = output / "safe" / f"seed-{seed}-repeat-{repeat}.json.gz"
    if path.exists():
        raise ValueError("run exists; immutable evidence cannot be overwritten")
    result = measure(
        seed=seed, max_ticks=manifest["config"]["safe_max_ticks"], safe=True
    )
    result.update(repeat=repeat, manifest_sha256=manifest["sha256"])
    write(path, result)
    return {
        k: v
        for k, v in result.items()
        if k not in ("ledgers", "replay", "decision_seconds", "phase_seconds")
    }


def run_paired(output):
    manifest = load_manifest(output, execution=True)
    for mode in manifest["config"]["paired_modes"]:
        for seed in manifest["config"]["paired_seeds"]:
            for swap in (False, True):
                path = output / "paired" / f"{mode}-{seed}-side-{int(swap)}.json.gz"
                if path.exists():
                    raise ValueError("paired result exists")
                result = measure(
                    seed=seed,
                    max_ticks=manifest["config"]["paired_max_ticks"],
                    safe=False,
                    latency_mode=mode,
                    swap=swap,
                    realtime_clock=manifest["config"].get("paired_execution")
                    == "single_worker_realtime_clock",
                )
                result["manifest_sha256"] = manifest["sha256"]
                write(path, result)
    return {
        "paired_files": len(manifest["config"]["paired_modes"])
        * len(manifest["config"]["paired_seeds"])
        * 2
    }


def finalize(output):
    manifest = load_manifest(output)
    paths = sorted((output / "safe").glob("*.json.gz"))
    rows = [read(path) for path in paths]
    paired_paths = sorted((output / "paired").glob("*.json.gz"))
    paired = [read(path) for path in paired_paths]
    if any(r["manifest_sha256"] != manifest["sha256"] for r in rows + paired):
        raise ValueError("run from another declaration")
    for r in rows + paired:
        if (
            r["placements"] != len(r["chains"])
            or r["max_chain"] != max(r["chains"], default=0)
            or r["premature"] != sum(0 < n < 10 for n in r["chains"])
        ):
            raise ValueError("actual chain summary mismatch")
        if digest(r["replay"]) != r["semantic_digest"]:
            raise ValueError("semantic evidence checksum mismatch")
        for ledger in r["ledgers"]:
            for d in ledger:
                c.Diagnostics.from_dict(
                    d
                )  # Strict runtime schema rejects oracle/private additions.
    evidence = (
        read(output / "evidence.json") if (output / "evidence.json").exists() else {}
    )
    report = evaluate(
        rows=rows,
        contract=manifest["config"],
        **{k: evidence.get(k) for k in ("g0", "g1", "g3", "g4", "threats")},
    )
    report["manifest_sha256"] = manifest["sha256"]
    report["analysis_source"] = {
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "files": {
            name: file_sha256(ROOT / name)
            for name in ("eval/nextgen_gates.py", "eval/nextgen_gate_benchmark.py")
        },
    }
    report["evidence_sha256"] = (
        file_sha256(output / "evidence.json")
        if (output / "evidence.json").exists()
        else None
    )
    report["raw_files"] = {
        str(p.relative_to(output)): file_sha256(p) for p in paths + paired_paths
    }
    report["phase_seconds"] = {
        phase: distribution(
            [v for r in rows for v in r["phase_seconds"].get(phase, [])]
        )
        for phase in {p for r in rows for p in r["phase_seconds"]}
    }
    report["paired"] = [
        {
            k: v
            for k, v in r.items()
            if k not in ("ledgers", "replay", "decision_seconds", "phase_seconds")
        }
        for r in paired
    ]
    report["timing_definition"] = (
        "decision_seconds are measured policy trace wall time; rollout wall includes scheduler, engine, serialization; effective activation tick delay reported separately"
    )
    report["safe_resources"] = {
        "wall_seconds": sum(r["elapsed_seconds"] for r in rows),
        "cpu_seconds": sum(r["cpu_seconds"] for r in rows),
        "peak_rss_kib": max((r["rss_peak_kib"] for r in rows), default=None),
    }
    report["paired_by_mode"] = {}
    for mode in manifest["config"]["paired_modes"]:
        group = [r for r in paired if r["latency_mode"] == mode]
        pairs = {
            seed: [r for r in group if r["seed"] == seed]
            for seed in manifest["config"]["paired_seeds"]
        }
        complete_pairs = [
            seed
            for seed, values in pairs.items()
            if len(values) == 2 and {r["policy_a_side"] for r in values} == {0, 1}
        ]
        wall = sum(r["elapsed_seconds"] for r in group)
        receipts = [
            d["receipt"] for r in group for ledger in r["ledgers"] for d in ledger
        ]
        phases = {p for r in group for p in r["phase_seconds"]}
        report["paired_by_mode"][mode] = {
            "observed_runs": len(group),
            "expected_runs": 2 * len(pairs),
            "complete_side_swap_pairs": complete_pairs,
            "terminal_runs": sum(r["termination"] == "terminal" for r in group),
            "truncated_runs": sum(r["termination"] == "tick_limit" for r in group),
            "decision_latency_seconds": distribution(
                [v for r in group for v in r["decision_seconds"]]
            ),
            "phase_seconds": {
                p: distribution(
                    [v for r in group for v in r["phase_seconds"].get(p, [])]
                )
                for p in phases
            },
            "activation_delay_ticks": distribution(
                [
                    r["activation_tick"] - r["request_tick"]
                    for r in receipts
                    if r["activation_tick"] is not None and r["outcome"] == "activated"
                ]
            ),
            "receipt_completion_delay_ticks": distribution(
                [r["completion_tick"] - r["request_tick"] for r in receipts]
            ),
            "timeout_limit_ticks": None,
            "actor_training_throughput": throughput_estimate(
                sum(
                    d["receipt"]["outcome"] == "activated"
                    for r in group
                    for d in r["ledgers"][r["policy_a_side"]]
                ),
                sum(
                    d["receipt"]["outcome"] == "activated"
                    for r in group
                    for d in r["ledgers"][1 - r["policy_a_side"]]
                ),
                wall,
            ),
            "outcomes": {
                outcome: sum(r["outcome"] == outcome for r in receipts)
                for outcome in sorted({r["outcome"] for r in receipts})
            },
            "started_decisions": sum(
                sum(r.get("started_decisions", [len(r["decision_seconds"])]))
                for r in group
            ),
            "execution_modes": sorted(
                {r.get("execution_mode", "synchronous") for r in group}
            ),
            "realtime_latency_qualified": False,
            "timeouts": sum(r["outcome"] == "timeout" for r in receipts),
            "ledger_decisions": len(receipts),
            "pending_at_boundary": sum(
                sum(r.get("started_decisions", [len(r["decision_seconds"])]))
                for r in group
            )
            - len(receipts),
            "wall_seconds": wall,
            "cpu_seconds": sum(r["cpu_seconds"] for r in group),
            "peak_rss_kib": max((r["rss_peak_kib"] for r in group), default=None),
            "throughput": throughput_estimate(
                sum(r["decisions"][r["policy_a_side"]] for r in group),
                sum(r["decisions"][1 - r["policy_a_side"]] for r in group),
                wall,
            ),
        }
    report["paired_interpretation"] = (
        "Side-swapped seed is one unit; tick-limit draws are truncated, not completed wins. Configured/measured never pooled. One seed is not G4 evidence."
    )
    report["classification"] = evidence.get(
        "classification",
        {
            "candidate_gap": None,
            "candidate_ranking_failure": None,
            "tactic_selection_failure": None,
            "denominator": 0,
            "reason": "no independent public reference sidecar",
        },
    )
    write(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("command", choices=("init", "safe", "paired", "finalize"))
    parser.add_argument("--seed", type=int, choices=SEEDS)
    parser.add_argument("--repeat", type=int, choices=REPEATS)
    parser.add_argument(
        "--realtime-clock",
        action="store_true",
        help="init: freeze a single-worker async measured paired preflight",
    )
    args = parser.parse_args()
    if args.command == "safe" and (args.seed is None or args.repeat is None):
        parser.error("safe requires seed and repeat")
    result = {
        "init": lambda: initialize(args.output, realtime_clock=args.realtime_clock),
        "safe": lambda: run_safe(args.output, args.seed, args.repeat),
        "paired": lambda: run_paired(args.output),
        "finalize": lambda: finalize(args.output),
    }[args.command]()
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
