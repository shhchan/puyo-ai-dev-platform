"""Read old G2 requests only; offline full planes diagnose changed candidates."""

import gzip
import json
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from agents import nextgen_contracts as c
from agents.compact_search import transition
from agents.nextgen_shared_search import _public_state, _pairs
from agents.nextgen_survival import apply_envelope, value
from agents.nextgen_tactic_manager import RuleTacticSelector

root = Path("docs/benchmarks/puyo-266-safe-build/integrated-g2-20260927")


def read(p):
    return json.loads(gzip.decompress(p.read_bytes()))


rows = []
for seed in range(123, 153):
    raw = read(root / f"native-g2/seed-{seed}-repeat-1.json.gz")
    repeat = read(root / f"native-g2/seed-{seed}-repeat-2.json.gz")
    locks = read(root / f"paired/{seed}-1.adoption.json.gz")
    records = {r["decision"]: r for r in locks["records"]}
    changes = []
    baseline_mismatch = []
    for d, w in enumerate(raw["ledger"], 1):
        req = c.NextgenRequest.from_dict(w["request"])
        batch = c.CandidateBatch.from_dict(w["batch"])
        byid = {v.candidate_id: v for v in batch.candidates}
        row = next(t for t in batch.tactics if t.tactic_id == "fire_main")
        active = raw["rows"][d - 1]["search"]["survival"].get("active", False)

        def rank(cid):
            v = byid[cid]
            safe = value(v, "survival_safe")
            safety = (0 if safe == 1 else 3 if safe == 0 else 2) if active else 0
            return safety, value(v, "fatal_rate") != 0

        ids = tuple(sorted(row.candidate_ids, key=rank))
        changed = replace(
            batch,
            tactics=tuple(
                replace(
                    t, candidate_ids=ids, best_id=ids[0], evidence=byid[ids[0]].evidence
                )
                if t.tactic_id == "fire_main" and ids
                else t
                for t in batch.tactics
            ),
        )
        choices = []
        for b in (batch, changed):
            selected = RuleTacticSelector().select(
                req,
                b,
                SimpleNamespace(action_mask=b.action_mask),
                SimpleNamespace(threat="none"),
            )
            selected = apply_envelope(b, selected)
            choices.append(selected.validate_batch(b))
        if choices[0].root_action != raw["rows"][d - 1]["action"]:
            baseline_mismatch.append(d)
        if choices[0].root_action != choices[1].root_action:
            candidate = choices[1]
            full = replace(
                _public_state(req)[0], planes=tuple(records[d]["offline_full_planes"])
            )
            result = transition(
                full, _pairs(req.public.own.known_pieces)[0], candidate.root_action
            )
            changes.append(
                {
                    "decision": d,
                    "before": choices[0].root_action,
                    "after": candidate.root_action,
                    "public_chain": value(candidate, "chain_count"),
                    "fire_depth": value(candidate, "fire_depth"),
                    "offline_actual_chain": result.chain_count,
                    "offline_nonfatal": result.valid and not result.game_over,
                    "survival_status": value(candidate, "survival_status"),
                }
            )
    row = {
        "seed": seed,
        "old_quality": {
            k: raw[k] for k in ["max_chain", "premature", "game_over", "placements"]
        },
        "repeat_semantic_equal": raw["semantic_digest"] == repeat["semantic_digest"],
        "baseline_mismatches": baseline_mismatch,
        "changed_candidates": changes,
    }
    rows.append(row)
    if changes or seed in (126, 128, 132, 135, 144):
        print(
            seed,
            row["old_quality"],
            "changed",
            len(changes),
            "first",
            changes[:1],
            "mismatch",
            baseline_mismatch,
        )
Path(__file__).with_name("formal-fire-audit.json").write_text(
    json.dumps(
        {
            "scope": "old 30x2 frozen public inputs; no new games, full planes offline only",
            "rows": rows,
        },
        indent=2,
    )
    + "\n"
)
