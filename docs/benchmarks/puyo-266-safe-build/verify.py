"""Validate this saved diagnostic corpus without rerunning policy searches."""

import gzip
import hashlib
import json
from pathlib import Path

from agents import nextgen_contracts as c
from puyo_env.realtime_versus import RealtimeVersusMatch
from src.core.realtime import TickInput


ROOT = Path(__file__).resolve().parent


def read(relative):
    path = ROOT / relative
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def main():
    manifest = read("manifest.json")
    for row in manifest["files"]:
        raw = (ROOT / row["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row["stored_sha256"], row["path"]
    legacy_records = 0
    for seed in (55, 123, 124):
        for record in read(f"baseline/seed-{seed}.json.gz")["ledger"]:
            assert record["batch"]["schema_version"] == c.LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION
            assert c.Diagnostics.from_dict(record).to_dict() == record
            legacy_records += 1
    assert legacy_records == 120
    paired_records = 0
    for folder in ("paired-before-cache", "paired-final"):
        for path in (ROOT / folder).glob("*.json.gz"):
            run = read(path.relative_to(ROOT))
            assert c.semantic_digest(run["semantic"]) == run["semantic_digest"], path
            for record in run["ledger"]:
                assert record["batch"]["schema_version"] == c.CANDIDATE_BATCH_SCHEMA_VERSION
                assert c.Diagnostics.from_dict(record).to_dict() == record
                paired_records += 1
    before = read("paired-before-cache/summary.json")["policies"]
    after = read("paired-final/summary.json")["policies"]
    for policy in before:
        for row in after[policy]["results"]:
            matches = [v for v in before[policy]["results"] if v["seed"] == row["seed"]]
            assert all(v["semantic_digest"] == row["semantic_digest"] for v in matches)
    replay = read("replay-final/replay.json.gz")
    match = RealtimeVersusMatch(replay["seed"], **replay["match_rules"])
    replay_records = 0
    for tick in replay["ticks"]:
        inputs = {key: TickInput.from_names(**value) for key, value in tick["inputs"].items()}
        result = match.step(inputs)
        assert result.tick == tick["tick"]
        assert match.state_hash() == tick["snapshot_hash"], tick["tick"]
        for controller in tick["controller_diagnostics"].values():
            decision = controller.get("last_decision") or {}
            diagnostic = decision.get("nextgen_diagnostics")
            if diagnostic is not None:
                c.Diagnostics.from_dict(diagnostic)
                replay_records += 1
    assert match.state_hash() == replay["expected_final_hash"]
    print(json.dumps({"stored_checksums": len(manifest["files"]),
        "legacy_v1_records": legacy_records, "paired_v2_records": paired_records,
        "unchanged_policy_seed_digests": 6, "replay_ticks": len(replay["ticks"]),
        "replay_diagnostics": replay_records, "final_hash": match.state_hash()}, indent=2))


if __name__ == "__main__":
    main()
