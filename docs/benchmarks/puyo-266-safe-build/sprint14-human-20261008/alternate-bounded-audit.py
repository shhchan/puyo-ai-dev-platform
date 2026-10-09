"""Read-only saved-public-input comparison; no offline private board is read."""
import gzip
import json
from pathlib import Path
from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices
from agents.nextgen_shared_search import ResponseBudget, _public_state
from agents.nextgen_survival import inferred_state, probe, refine_inferred
from tests.test_nextgen_inferred_survival import fixtures, selection

DIRECTORY = Path(__file__).resolve().parent


def read(path):
    return json.loads(gzip.decompress(path.read_bytes()))


def cases():
    yield from fixtures().items()
    for folder, seeds in (("inference-v1-fixed", (55, 123, 124, 126, 128)),
                          ("fire-eligible-fixed", (55, 123, 124, 126))):
        for seed in seeds:
            for decision, wire in enumerate(read(DIRECTORY / folder / f"gtr-{seed}.json.gz")["ledger"], 1):
                yield f"{folder}-{seed}/{decision}", decode(wire)
    for folder in ("inference-v1-human", "landed-recovery-human", "landed-recovery-extended"):
        for attempt in read(DIRECTORY / folder / "report.json.gz")["attempts"]:
            if attempt["record"]["outcome"] == "activated":
                wire = attempt["payload"]["nextgen"]
                decision = wire["request"]["identity"]["decision_id"]
                yield f"{folder}/{decision}", decode(wire)


def decode(wire):
    req = c.NextgenRequest.from_dict(wire["request"])
    batch = c.CandidateBatch.from_dict(wire["batch"])
    by_id = {v.candidate_id: v for v in batch.candidates}
    ids = next(t.candidate_ids for t in batch.tactics if t.tactic_id == "build_main")
    order = tuple(dict.fromkeys(by_id[cid].root_action for cid in ids))
    return req, batch, c.Selection.from_dict(wire["selection"]), order


def compare():
    rows = []
    for name, (req, batch, selected, order) in cases():
        state = inferred_state(req, _public_state(req)[0])
        if state is None:
            rows.append({"case": name, "status": "legacy_unknown_or_pending"})
            continue
        modes = {}
        for mode, reuse in (("before", False), ("after", True)):
            budget, cache = ResponseBudget(256), {}
            roots, diag = probe(req, state, legal_action_indices(state), budget,
                                transition_cache=cache, reuse_transitions=reuse)
            roots, diag = refine_inferred(req, state, roots, diag, budget, order, cache)
            proof = diag.get("control_proof", {})
            recovery = proof.get("landed_garbage_recovery", {}).get("preferred_root")
            action, reason = selection(batch, selected, roots, diag.get("active", False), recovery)
            modes[mode] = {"action": action, "reason": reason, "logical_nodes": budget.nodes,
                           "response_remaining": budget.quota - budget.nodes, "diagnostics": diag}
            assert budget.nodes <= 128
        rows.append({"case": name, **modes})
    return rows


if __name__ == "__main__":
    rows = compare()
    output = DIRECTORY / "alternate-bounded-audit.json.gz"
    output.write_bytes(gzip.compress((json.dumps(rows, indent=2) + "\n").encode(), mtime=0))
    evaluated = [r for r in rows if "before" in r]
    changes = [(r["case"], r["before"]["action"], r["after"]["action"])
               for r in evaluated if r["before"]["action"] != r["after"]["action"]]
    print(json.dumps({"total": len(rows), "evaluated": len(evaluated), "changes": changes,
                      "quota_violations": 0, "changed_logical_debits": sum(
                          r["before"]["logical_nodes"] != r["after"]["logical_nodes"]
                          for r in evaluated)}, indent=2))
