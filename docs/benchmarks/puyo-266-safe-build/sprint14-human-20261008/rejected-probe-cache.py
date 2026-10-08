import gzip
import inspect
import json
from pathlib import Path
from dataclasses import replace
from tests.test_nextgen_inferred_survival import fixtures, selection
from agents import nextgen_contracts as c
from agents.compact_search import legal_action_indices
from agents.nextgen_shared_search import _public_state, ResponseBudget
import agents.nextgen_survival as module
from eval.nextgen_gate_benchmark import SafeNoThreatMatch
from src.core.realtime import TickInput

# Read-only experiment: install the alternate probe only in this interpreter.
source = inspect.getsource(module.probe)
old = """            yield  # One placement including failed/fatal resolution.
            pair = tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in known[depth])
            result = transition(node.state, pair, action)
            if transition_cache is not None:
                transition_cache[node.state, pair, action] = result"""
new = """            pair = tuple(c.PUBLIC_CELL_TO_COLOR[v] for v in known[depth])
            key = node.state, pair, action
            if transition_cache is not None:
                yield key
                result = transition_cache[key]
            else:
                yield
                result = transition(node.state, pair, action)"""
source = source.replace(
    "                next(iterator)", "                work = next(iterator)"
)
source = source.replace(
    "            if budget.nodes - start_nodes >= limit or not budget.consume():",
    """            if transition_cache is not None and work is not None and work in transition_cache:
                pending.append((action, iterator))
                continue
            if budget.nodes - start_nodes >= limit or not budget.consume():""",
)
source = source.replace(
    "            else:\n                pending.append((action, iterator))",
    """            else:
                if transition_cache is not None and work is not None:
                    transition_cache[work] = transition(*work)
                pending.append((action, iterator))""",
)
assert old in source
scope = module.__dict__.copy()
exec(source.replace(old, new), scope)
cached_probe = scope["probe"]
root = Path("docs/benchmarks/puyo-266-safe-build")


def read(p):
    return json.loads(gzip.decompress(p.read_bytes()))


cases = fixtures()


def add(name, wire, inf):
    req = replace(
        c.NextgenRequest.from_dict(wire["request"]),
        schema_version=c.REQUEST_SCHEMA_VERSION,
    )
    req = replace(
        req,
        inference=replace(
            inf,
            episode_id=req.identity.episode_id,
            request_digest=c.inference_request_digest(req.identity, req.execution),
        ),
    )
    batch = c.CandidateBatch.from_dict(wire["batch"])
    cs = {v.candidate_id: v for v in batch.candidates}
    ids = next(t.candidate_ids for t in batch.tactics if t.tactic_id == "build_main")
    cases[name] = (
        req,
        batch,
        c.Selection.from_dict(wire["selection"]),
        tuple(dict.fromkeys(cs[cid].root_action for cid in ids)),
    )


for seed in [55, 123, 124, 126, 128]:
    raw = read(root / f"sprint14-human-20261008/inference-v1-fixed/gtr-{seed}.json.gz")
    for i, w in enumerate(raw["ledger"], 1):
        if i < 28:
            continue
        req = c.NextgenRequest.from_dict(w["request"])
        add(f"current-{seed}/{i}", w, req.inference)
for seed in [132, 135, 144]:
    raw = read(root / f"desktop-survival-20260928/before/gtr-{seed}.json.gz")
    targets = {
        row["tick"]: (i, w)
        for i, (row, w) in enumerate(zip(raw["rows"], raw["ledger"]), 1)
        if i >= 28
    }
    match = SafeNoThreatMatch(seed)
    match.public_board_inference()
    for t in raw["semantic"]["inputs"]:
        if match.tick in targets:
            i, w = targets[match.tick]
            inf = match.public_board_inference()
            assert inf.status == "known", (seed, i, inf.reason)
            add(f"old-{seed}/{i}", w, inf)
        match.step({k: TickInput.from_names(**v) for k, v in t["inputs"].items()})
rows = []
for name, (req, batch, selected, order) in cases.items():
    state = module.inferred_state(req, _public_state(req)[0])
    results = {}
    for mode, p in [("current", module.probe), ("cached", cached_probe)]:
        budget = ResponseBudget(256)
        cache = {}
        rs, diag = p(
            req, state, legal_action_indices(state), budget, transition_cache=cache
        )
        probe_nodes = budget.nodes
        refined, diag = module.refine_inferred(
            req, state, rs, diag, budget, order, cache
        )
        action, reason = selection(batch, selected, refined, diag.get("active", False))
        results[mode] = {
            "probe": probe_nodes,
            "nodes": budget.nodes,
            "root": action,
            "reason": reason,
            "diagnostics": diag,
        }
    row = {"case": name, **results}
    rows.append(row)
    if results["current"]["root"] != results["cached"]["root"] or name in [
        "current-128/38",
        "gtr-123/28",
        "human-127/26",
    ]:
        print(
            name,
            [
                (
                    mode,
                    results[mode]["root"],
                    results[mode]["probe"],
                    results[mode]["nodes"],
                    results[mode]["diagnostics"].get("control_proof", {}).get("status"),
                )
                for mode in results
            ],
        )
Path("/tmp/puyo266-rejected-probe-cache-result.json").write_text(
    json.dumps(rows, indent=2) + "\n"
)
print(
    "cases",
    len(rows),
    "changed roots",
    sum(r["current"]["root"] != r["cached"]["root"] for r in rows),
)
