"""Paired fixed-seed safe-build diagnostic with real scheduler receipts.

This small corpus cannot pass G2. Both policies use the same realtime solo
environment, public current/NEXT/NEXT2, configured zero inference ticks, and
40 resolved placements. Wall decision times are recorded separately.
"""

from __future__ import annotations

import argparse
import gzip
import json
import time
from dataclasses import asdict
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import transition
from agents.deep_chain_builder import DeepChainBuilderPolicy
from agents.nextgen_profiles import NEXTGEN_PROFILE_CHOICES, nextgen_search_settings
from agents.nextgen_shared_search import _pairs, _public_state
from agents.nextgen_tactic_manager import NextgenTacticManagerPolicy
from eval.nextgen_gate_benchmark import SafeNoThreatMatch
from eval.nextgen_latency_benchmark import distribution
from eval.nextgen_realtime_diagnostic import source_identity
from puyo_env.realtime_ai import RealtimeDecisionConfig, RealtimePolicyController
from src.ui.launcher_settings import resolve_nextgen_catalog

SEEDS = (55, 123, 124)


def make_policy(kind, seed, profile):
    if kind == "deep_chain":
        return DeepChainBuilderPolicy(profile="reference", backend="native")
    catalog, _ = resolve_nextgen_catalog(
        catalog_path="train/config/nextgen_templates.yaml", templates="gtr",
        mode="argmax", temperature=.1, commit_turns=14,
        repo_root=Path(__file__).resolve().parents[1],
    )
    budget, search = nextgen_search_settings(profile, seed=seed)
    return NextgenTacticManagerPolicy(
        catalog=catalog, seed=seed, profile=budget, search_config=search,
        backend="native",
    )


def measure(kind, seed, profile, *, placements=40):
    policy = make_policy(kind, seed, profile)
    match = SafeNoThreatMatch(seed)
    controller = RealtimePolicyController(
        policy, config=RealtimeDecisionConfig(latency_mode="configured"),
    )
    rows, chains, inputs = [], [], []
    seen = None
    started = time.perf_counter()
    for _ in range(30000):
        value = {} if match.ending else {
            "player_0": controller.next_input(match, "player_0"),
        }
        context = policy.last_context
        if context is not None and context is not seen:
            seen = context
            row = {"tick": match.tick, "seconds": context.trace.elapsed_seconds}
            if kind == "nextgen":
                request, search = context.require("request"), context.require("search")
                candidate = context.require("candidate")
                state, _ = _public_state(request)
                predicted = transition(state, _pairs(request.public.own.known_pieces)[0], candidate.root_action)
                row.update(
                    action=candidate.root_action,
                    shared_best=search.root_rankings[0],
                    shared_rank=search.root_rankings.index(candidate.root_action),
                    predicted_chain=predicted.chain_count,
                    selection=context.require("selection").to_dict(),
                    phase=context.require("phase").diagnostics(),
                    search=search.diagnostics,
                    counters=search.batch.counters.to_dict(),
                    root_evidence=[r.to_dict() for r in search.shared_result.ranked_roots],
                )
            else:
                row.update(action=context.require("selected_action"))
            rows.append(row)
        result = match.step(value)
        inputs.append({"tick": result.tick, "inputs": {k: v.to_json() for k, v in value.items()}})
        for event in result.player_results["player_0"].events:
            if event.type == "resolution_complete":
                chains.append(event.data["chain_count"])
                print(kind, seed, len(chains), "chain", chains[-1], flush=True)
        if len(chains) >= placements or match.finished:
            break
    runtime = controller.nextgen_scheduler
    ledger = [d.to_dict() for d in runtime.ledger] if runtime else []
    semantic = {
        "inputs": inputs, "chains": chains, "final_hash": match.state_hash(),
        "decisions": [
            {"action": r["action"], "selection": r.get("selection"), "phase": r.get("phase")}
            for r in rows
        ],
        "receipts": [d["receipt"] for d in ledger],
    }
    return {
        "policy": kind, "seed": seed,
        "profile": policy.profile.to_dict(),
        "search_config": asdict(policy.search_config) if kind == "nextgen" else None,
        "chains": chains, "max_chain": max(chains, default=0),
        "premature": sum(0 < n < 10 for n in chains),
        "game_over": match.player_states["player_0"].simulator.game.game_over,
        "placements": len(chains), "ticks": match.tick,
        "elapsed_seconds": time.perf_counter() - started,
        "decision_seconds": distribution([r["seconds"] for r in rows]),
        "rows": rows, "ledger": ledger,
        "controller": controller.diagnostics.to_dict(),
        "errors": runtime.errors if runtime else [],
        "semantic": semantic, "semantic_digest": c.semantic_digest(semantic),
    }


def run(output, *, profile="nextgen_safe_build", repeats=2):
    output.mkdir(parents=True, exist_ok=False)
    source = source_identity()
    declaration = {
        "source": source, "seeds": SEEDS, "repeats": repeats, "profile": profile,
        "placements": 40, "backend": "native", "latency_mode": "configured",
        "quality_status": "diagnostic only; formal G2 and human GUI QA not established",
        "comparison_limits": [
            "Nextgen uses GTR then free build; deep_chain has no template phase.",
            "Both use legacy-fixed-six completion but different public seed derivation.",
            "Nextgen excludes hidden rows; deep_chain's existing adapter sees own ghost rows.",
        ],
    }
    (output / "declaration.json").write_text(json.dumps(declaration, indent=2) + "\n")
    results = []
    for kind in ("nextgen", "deep_chain"):
        for seed in SEEDS:
            for repeat in range(1, repeats + 1):
                result = measure(kind, seed, profile)
                result["repeat"] = repeat
                path = output / f"{kind}-{seed}-{repeat}.json.gz"
                path.write_bytes(gzip.compress(json.dumps(result, allow_nan=False).encode(), mtime=0))
                results.append(result)
    summary = {"declaration": declaration, "source_changed_during_run": source_identity() != source, "policies": {}}
    for kind in ("nextgen", "deep_chain"):
        rows = [r for r in results if r["policy"] == kind]
        summary["policies"][kind] = {
            "runs": len(rows), "mean_max_chain": sum(r["max_chain"] for r in rows) / len(rows),
            "premature": sum(r["premature"] for r in rows),
            "game_overs": sum(r["game_over"] for r in rows),
            "decision_seconds": distribution([d["seconds"] for r in rows for d in r["rows"]]),
            "repeat_match": all(len({r["semantic_digest"] for r in rows if r["seed"] == seed}) == 1 for seed in SEEDS),
            "results": [{k: r[k] for k in ("seed", "repeat", "max_chain", "premature", "game_over", "placements", "semantic_digest")} for r in rows],
        }
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary["policies"], indent=2), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--profile", choices=NEXTGEN_PROFILE_CHOICES, default="nextgen_safe_build")
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("repeats must be positive")
    run(args.output, profile=args.profile, repeats=args.repeats)


if __name__ == "__main__":
    main()
