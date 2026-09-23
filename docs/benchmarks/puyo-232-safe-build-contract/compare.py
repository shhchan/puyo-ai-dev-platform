"""Recompute the PUYO-232 comparison from preserved old/new target10 raw."""

import gzip
import hashlib
import json
from pathlib import Path

repo = Path(__file__).resolve().parents[3]
root = repo / "docs/benchmarks/puyo-232-safe-build-contract"
old = repo / "docs/benchmarks/puyo-231-target-ablation/target-10"
rows = []
for seed in (123, 126, 130, 133, 135, 147):
    name = f"seed-{seed:03d}-repeat-01.json.gz"
    a = json.loads(gzip.decompress((old / name).read_bytes()))
    b = json.loads(gzip.decompress((root / "target-10" / name).read_bytes()))

    def outcomes(run):
        return [
            {
                k: r["actual_result"][k]
                for k in ("chain_count", "score_delta", "game_over")
            }
            for r in run["records"]
            if "action" in r
        ]

    def actions(run):
        return [r["action"] for r in run["records"] if "action" in r]

    rows.append(
        {
            "seed": seed,
            "repeat": 1,
            "old_sha256": hashlib.sha256((old / name).read_bytes()).hexdigest(),
            "new_sha256": hashlib.sha256(
                (root / "target-10" / name).read_bytes()
            ).hexdigest(),
            "actions_match": actions(a) == actions(b),
            "actual_results_match": outcomes(a) == outcomes(b),
            "old_maximum_chain": a["maximum_actual_fire_chain_count"],
            "new_maximum_chain": b["maximum_actual_fire_chain_count"],
            "old_premature": a["premature_fire_count"],
            "new_premature": b["premature_fire_count"],
            "old_game_over": a["game_over"],
            "new_game_over": b["game_over"],
            "new_termination": b["termination_reason"],
            "new_parity_mismatches": b["simulator_parity_mismatch_count"],
        }
    )
result = {
    "schema_version": "puyo.safe_build_target_regression.v1",
    "ticket": "PUYO-232",
    "scope": "six prespecified regression seeds, repeat1 only; not a representative quality estimate or baseline acceptance",
    "comparison_basis": "PUYO-231 explicit target10 vs PUYO-232 canonical target10 after PUYO-237/238",
    "records": rows,
}
(root / "target10_regression.json").write_text(
    json.dumps(result, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps(result, ensure_ascii=False, indent=2))
