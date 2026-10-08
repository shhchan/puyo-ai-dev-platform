import gzip
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from agents import nextgen_contracts as c
from agents.nextgen_survival import apply_envelope, value
from agents.nextgen_tactic_manager import RuleTacticSelector

root = Path(
    "docs/benchmarks/puyo-266-safe-build/sprint14-human-20261008/inference-v1-fixed"
)
rows = []
for seed in (55, 123, 124, 126, 128):
    raw = json.loads(gzip.decompress((root / f"gtr-{seed}.json.gz").read_bytes()))
    for i, w in enumerate(raw["ledger"], 1):
        req = c.NextgenRequest.from_dict(w["request"])
        batch = c.CandidateBatch.from_dict(w["batch"])
        cs = {v.candidate_id: v for v in batch.candidates}
        row = next(t for t in batch.tactics if t.tactic_id == "fire_main")

        def eligible(cid):
            v = cs[cid]
            return value(v, "fatal_rate") == 0 and (value(v, "chain_count") or 0) >= 10

        ids = tuple(sorted(row.candidate_ids, key=lambda cid: not eligible(cid)))
        changed = replace(
            batch,
            tactics=tuple(
                replace(
                    t, candidate_ids=ids, best_id=ids[0], evidence=cs[ids[0]].evidence
                )
                if t.tactic_id == "fire_main" and ids
                else t
                for t in batch.tactics
            ),
        )
        selectors = []
        for b in (batch, changed):
            selected = RuleTacticSelector().select(
                req,
                b,
                SimpleNamespace(action_mask=b.action_mask),
                SimpleNamespace(threat="none"),
            )
            selected = apply_envelope(b, selected)
            selectors.append(
                (selected.selected_tactic_id, selected.validate_batch(b).root_action)
            )
        result = {
            "seed": seed,
            "decision": i,
            "before": selectors[0],
            "after": selectors[1],
            "old_fire_best": cs[row.best_id].root_action if row.best_id else None,
            "new_fire_best": cs[ids[0]].root_action if ids else None,
        }
        rows.append(result)
        if selectors[0] != selectors[1]:
            print(result)
Path(__file__).with_name("fire-eligibility-audit.json").write_text(
    json.dumps(rows, indent=2) + "\n"
)
