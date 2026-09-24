"""Fail-closed nextgen G0--G4 evaluation, independent of Jira completion.

A report records missing observations as unknown. Oracle references are accepted
only by offline diagnosis; runtime Diagnostics retain their strict public schema.
"""

from __future__ import annotations

import hashlib
import json
import math

from agents import nextgen_contracts as c

SCHEMA = "puyo.nextgen.gates.v1"
SEEDS = tuple(range(123, 153))
REPEATS = (1, 2)
THRESHOLDS = {
    "mean_max_chain": 10,
    "premature": 0,
    "game_over": 0,
    "placements": 40,
    "safe_build_p95_seconds": 1.0,
}
G0_CHECKS = (
    "schema",
    "fixtures",
    "public_boundary",
    "legality",
    "replay",
    "parity",
    "six_tactic_masks",
)
G1_CHECKS = (
    "rule_ledger",
    "gui_ledger",
    "missing_zero",
    "illegal_zero",
    "leak_zero",
    "template_fixtures",
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def distribution(values):
    values = sorted(values)
    if not values:
        return {"n": 0, "mean": None, "p50": None, "p95": None}
    if any(not math.isfinite(x) or x < 0 for x in values):
        raise ValueError("timings must be nonnegative finite observations")

    def percentile(q):
        position = (len(values) - 1) * q
        low = int(position)
        high = min(low + 1, len(values) - 1)
        return values[low] + (values[high] - values[low]) * (position - low)

    return {
        "n": len(values),
        "mean": sum(values) / len(values),
        "p50": percentile(0.5),
        "p95": percentile(0.95),
    }


def diagnose_selection(diagnostics, reference):
    """One-step public witness diagnosis, never a closed-loop causal claim.

    Reference actions are independently supplied, not derived from fixed ranks.
    Hidden-future oracle sidecars may use the same format, but cannot qualify G2.
    Unknown references must be omitted rather than fabricated from old raw.
    """
    d = c.Diagnostics.from_dict(diagnostics)
    if set(reference) != {"snapshot_digest", "tactic", "good_root_actions", "source"}:
        raise ValueError("reference fields mismatch")
    if reference["source"] not in ("public_reference", "hidden_future_oracle"):
        raise ValueError("reference source mismatch")
    if reference["snapshot_digest"] != d.request.public.digest:
        raise ValueError("reference snapshot mismatch")
    if reference["tactic"] not in c.TACTIC_IDS or not reference["good_root_actions"]:
        raise ValueError("a validated tactic witness is required")
    actions = reference["good_root_actions"]
    if any(type(a) is not int or not 0 <= a < c.NUM_ACTIONS for a in actions):
        raise ValueError("invalid reference action")
    row = next(t for t in d.batch.tactics if t.tactic_id == reference["tactic"])
    good = {
        v.candidate_id
        for v in d.batch.candidates
        if v.candidate_id in row.candidate_ids
        and v.root_action in actions
        and not v.fallback
    }
    gap = not good
    ranking = bool(good) and row.best_id not in good
    selection = (
        bool(good) and not ranking and d.selection.selected_tactic_id != row.tactic_id
    )
    return {
        "source": reference["source"],
        "candidate_gap": int(gap),
        "candidate_ranking_failure": int(ranking),
        "tactic_selection_failure": int(selection),
        "candidate_denominator": 1,
        "ranking_denominator": int(not gap),
        "selection_denominator": int(not gap and not ranking),
        "interpretation": "single_step_witness; not closed_loop_regret",
    }


def safe_build_summary(rows):
    expected = {(s, r) for s in SEEDS for r in REPEATS}
    identities = [(r["seed"], r["repeat"]) for r in rows]
    if len(set(identities)) != len(identities) or set(identities) - expected:
        raise ValueError("duplicate or unexpected safe-build identity")
    completed = [
        r
        for r in rows
        if r["termination"] in ("placements", "game_over")
        and (r["placements"] == 40 or r["game_over"])
    ]
    missing = sorted(expected - set(identities))
    unfinished = [[r["seed"], r["repeat"]] for r in rows if r not in completed]
    by_id = {(r["seed"], r["repeat"]): r for r in completed}
    paired = [s for s in SEEDS if all((s, r) in by_id for r in REPEATS)]
    mismatch = [
        s
        for s in paired
        if by_id[s, 1]["semantic_digest"] != by_id[s, 2]["semantic_digest"]
    ]
    # Unique-seed quality estimate is repeat 1; repeated runs are not IID seeds.
    unique = [by_id[s, 1]["max_chain"] for s in SEEDS if (s, 1) in by_id]
    latency = distribution([v for r in rows for v in r.get("decision_seconds", [])])
    return {
        "expected_runs": 60,
        "observed_runs": len(rows),
        "completed_runs": len(completed),
        "missing": missing,
        "unfinished": unfinished,
        "unique_seed_denominator": len(unique),
        "mean_max_chain": sum(unique) / len(unique) if unique else None,
        "premature": sum(r["premature"] for r in rows),
        "game_over": sum(r["game_over"] for r in rows),
        "repeat_pairs": len(paired),
        "repeat_mismatch_seeds": mismatch,
        "decision_latency_seconds": latency,
        "placement_denominator": sum(r["placements"] for r in rows),
    }


def checks_gate(names, evidence):
    checks = {name: evidence.get(name) for name in names}
    # Exact bool required: missing evidence, empty collections, or "PASS" are not success.
    if any(v is not None and type(v) is not bool for v in checks.values()):
        raise ValueError("gate evidence must be true, false or null")
    status = (
        "FAIL"
        if False in checks.values()
        else "PASS"
        if all(v is True for v in checks.values())
        else "BLOCKED"
    )
    return {"status": status, "checks": checks}


def evaluate(*, rows, contract, threats=None, g0=None, g1=None, g3=None, g4=None):
    if (
        contract.get("thresholds") != THRESHOLDS
        or contract.get("seeds") != list(SEEDS)
        or contract.get("repeats") != list(REPEATS)
    ):
        raise ValueError("frozen threshold/seed contract mismatch")
    summary = safe_build_summary(rows)
    checks = {
        "all_60_complete": summary["completed_runs"] == 60,
        "mean_chain_at_least_10": summary["mean_max_chain"] is not None
        and summary["mean_max_chain"] >= 10,
        "premature_zero": summary["premature"] == 0,
        "game_over_zero": summary["game_over"] == 0,
        "repeat_digest_match": summary["repeat_pairs"] == 30
        and not summary["repeat_mismatch_seeds"],
        "public_runtime": contract.get("runtime_information") == "public_only",
        "reference_profile_calibrated": contract.get("reference_profile_calibrated")
        is True,
        "required_threat_fixtures": threats is not None
        and threats.get("complete") is True
        and threats.get("failed") == 0,
        "known_solution_gap_zero": threats is not None
        and threats.get("reference_source") == "public_reference"
        and threats.get("candidate_denominator", 0) > 0
        and threats.get("candidate_gap") == 0,
    }
    gates = {
        "G0": checks_gate(G0_CHECKS, g0 or {}),
        "G1": checks_gate(G1_CHECKS, g1 or {}),
    }
    checks["G0_passed"] = gates["G0"]["status"] == "PASS"
    checks["G1_passed"] = gates["G1"]["status"] == "PASS"
    gates["G2"] = {
        "status": "PASS" if all(checks.values()) else "BLOCKED",
        "checks": checks,
        "failed_conditions": [k for k, v in checks.items() if not v],
    }
    gates["G3"] = checks_gate(
        ("all_training_seeds", "mask_return_reward", "held_out_quality"), g3 or {}
    )
    gates["G4"] = checks_gate(
        (
            "paired_win_ci",
            "opponent_strata_ci",
            "noninferiority",
            "quality",
            "normal_gui_qa",
            "measured_latency",
            "artifact_lineage",
            "resource_adoption",
            "frozen_human_latency_thresholds",
        ),
        g4 or {},
    )
    for current, prior in (("G3", "G2"), ("G4", "G3")):
        if gates[prior]["status"] != "PASS":
            gates[current]["status"] = "BLOCKED"
            gates[current]["blocked_by"] = prior
    p95 = summary["decision_latency_seconds"]["p95"]
    return {
        "schema": SCHEMA,
        "gates": gates,
        "safe_build": summary,
        "safe_build_performance": {
            "status": "BLOCKED"
            if p95 is None or not contract.get("reference_profile_calibrated")
            else "PASS"
            if p95 <= 1
            else "FAIL",
            "p95_seconds": p95,
            "limit_seconds": 1,
        },
        "long_training_allowed": gates["G3"]["status"] == "PASS",
    }


def throughput_estimate(self_decisions, opponent_decisions, elapsed_seconds):
    if self_decisions <= 0 or elapsed_seconds <= 0:
        return {"status": "BLOCKED", "reason": "no completed rollout sample"}
    throughput = self_decisions / elapsed_seconds
    return {
        "status": "MEASURED_ROLLOUT_ONLY",
        "self_decisions": self_decisions,
        "opponent_decisions": opponent_decisions,
        "elapsed_seconds": elapsed_seconds,
        "self_decisions_per_second": throughput,
        "opponent_per_self": opponent_decisions / self_decisions,
        "estimated_hours": {
            "wiring_2000": 2000 / throughput / 3600,
            "ppo_20000_x3": 60000 / throughput / 3600,
            "long_1000000_x5": 5000000 / throughput / 3600,
        },
        "assumptions": [
            "one worker, one search thread, same profile and hardware",
            "rollout includes both players and environment; optimizer/checkpoint/gradient costs unmeasured",
            "no linear multiworker speedup assumed; truncated smoke is not representative quality evidence",
            "estimates do not authorize training; G2/G3 remain prerequisites",
        ],
    }
