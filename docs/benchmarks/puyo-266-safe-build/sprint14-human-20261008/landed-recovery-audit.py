"""Frozen public requests only; no private boards or native/game measurements."""
import gzip
import json
import subprocess
from pathlib import Path

from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices
from agents.nextgen_shared_search import ResponseBudget, _public_state
import agents.nextgen_survival as survival
from tests.test_nextgen_inferred_survival import selection

ROOT = Path(__file__).resolve().parent
BASE = "ce44f6ef4d47dafcb21613c907d285971f84000a"


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def run():
    # Execute the recorded baseline implementation in an isolated namespace.
    source = subprocess.check_output(["git", "show", BASE + ":agents/nextgen_survival.py"], text=True)
    start = source.index("def refine_inferred(")
    end = source.index("\ndef evidence_for(", start)
    scope = vars(survival).copy()
    exec(source[start:end], scope)
    baseline = scope["refine_inferred"]
    cases = []
    for seed in (55, 123, 124, 126, 128):
        raw = read(ROOT / f"inference-v1-fixed/gtr-{seed}.json.gz")
        cases.extend((f"gtr-{seed}/{i}", wire) for i, wire in enumerate(raw["ledger"], 1))
    raw = read(ROOT / "inference-v1-human/report.json.gz")
    cases.extend((f"human-127/{a['payload']['nextgen']['request']['identity']['decision_id']}",
                  a["payload"]["nextgen"]) for a in raw["attempts"] if a["record"]["outcome"] == "activated")
    rows, legacy = [], []
    for name, wire in cases:
        req = c.NextgenRequest.from_dict(wire["request"])
        state = survival.inferred_state(req, _public_state(req)[0])
        if state is None:
            legacy.append(name)
            continue
        batch = c.CandidateBatch.from_dict(wire["batch"])
        selected = c.Selection.from_dict(wire["selection"])
        candidates = {v.candidate_id: v for v in batch.candidates}
        ids = next(t.candidate_ids for t in batch.tactics if t.tactic_id == "build_main")
        order = tuple(dict.fromkeys(candidates[i].root_action for i in ids))
        evaluated = []
        for refine in (baseline, survival.refine_inferred):
            budget, cache = ResponseBudget(256), {}
            roots, diag = survival.probe(req, state, legal_action_indices(state), budget, transition_cache=cache)
            roots, diag = refine(req, state, roots, diag, budget, order, cache)
            recovery = diag.get("control_proof", {}).get("landed_garbage_recovery", {})
            action, reason = selection(batch, selected, roots, diag.get("active", False), recovery.get("preferred_root"))
            assert budget.nodes <= 128
            evaluated.append((roots, diag, action, reason, budget.nodes))
        before, after = evaluated
        rows.append({"case": name, "garbage": state.planes[5].bit_count(),
                     "unchanged": before == after, "before_action": before[2], "after_action": after[2],
                     "before_nodes": before[4], "after_nodes": after[4], "reason": after[3],
                     "proof": after[1].get("control_proof")})
    return {"baseline": BASE, "scope": "saved public inputs and actual apply_envelope; not a new game",
            "legacy_without_inference": legacy, "rows": rows}


if __name__ == "__main__":
    value = run()
    ROOT.joinpath("landed-recovery-audit.json.gz").write_bytes(
        gzip.compress((json.dumps(value, indent=2) + "\n").encode(), mtime=0))
    rows = value["rows"]
    print("cases", len(rows), "zero-garbage", sum(not r["garbage"] for r in rows),
          "zero-garbage changes", sum(not r["garbage"] and not r["unchanged"] for r in rows))
    for row in rows:
        if not row["unchanged"]:
            print(row["case"], row["before_action"], "->", row["after_action"], row["after_nodes"])
