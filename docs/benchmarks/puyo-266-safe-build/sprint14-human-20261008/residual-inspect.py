import gzip
import json
from pathlib import Path
from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices, transition
from agents.nextgen_shared_search import ResponseBudget, _public_state, _pairs
from agents.nextgen_survival import inferred_state, probe, refine_inferred

root = Path(
    "docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/inference-v1-fixed"
)


def inspect(seed, decision):
    raw = json.loads(gzip.decompress((root / f"gtr-{seed}.json.gz").read_bytes()))
    w = raw["ledger"][decision - 1]
    req = c.NextgenRequest.from_dict(w["request"])
    batch = c.CandidateBatch.from_dict(w["batch"])
    state = inferred_state(req, _public_state(req)[0])
    pairs = _pairs(req.public.own.known_pieces)
    ids = next(t.candidate_ids for t in batch.tactics if t.tactic_id == "build_main")
    byid = {v.candidate_id: v for v in batch.candidates}
    order = tuple(dict.fromkeys(byid[i].root_action for i in ids))
    budget = ResponseBudget(256)
    cache = {}
    rs, diag = probe(
        req, state, legal_action_indices(state), budget, transition_cache=cache
    )
    pn = budget.nodes
    refined, diag = refine_inferred(req, state, rs, diag, budget, order, cache)
    print(
        "\nCASE",
        seed,
        decision,
        "chosen",
        raw["rows"][decision - 1]["action"],
        "height",
        state.column_heights,
        "nodes",
        pn,
        budget.nodes,
        "proof",
        diag.get("control_proof"),
    )
    print("ORDER", order)
    print("ROOTS", [(a, v.status, v.root_chain, v.witness) for a, v in rs.items()])
    paths = []
    for cand in batch.candidates:
        if not all(step.provenance == "public_known" for step in cand.plan):
            continue
        st = state
        chain = []
        ok = True
        for pair, step in zip(pairs, cand.plan):
            tr = transition(st, pair, step.action)
            st = tr.state
            chain.append(tr.chain_count)
            ok &= tr.valid and not tr.game_over
        if max(chain) > 0:
            paths.append(
                {
                    "path": [s.action for s in cand.plan],
                    "chains": chain,
                    "valid_nonfatal": ok,
                    "tactics": cand.tactics,
                }
            )
    print("KNOWN CLEARS", paths)
    return req, state, pairs, order, rs, diag, cache, pn


if __name__ == "__main__":
    for seed, ds in [(128, [36, 37, 38, 39]), (126, [33, 34, 35, 36, 37, 38])]:
        for d in ds:
            inspect(seed, d)
