# PUYO-266 diagnostic evidence

This corpus is **not a formal G2 PASS**. Human GUI QA is pending. See the [Japanese report](../../development/puyo-266-post-template-safe-build.md) for results, comparison limits and rerun commands.

- `baseline/`: original `b36bbddd` policy, three seeds with 40 resolved placements each; full v1 ledgers and the original probe. Only the fixture's internal infinite sort sentinels are serialized as strings; the trajectory records are unchanged.
- `paired-before-cache/`: `6e82d788`, three seeds × two repeats per policy, configured zero inference ticks and an inactive opponent.
- `paired-final/`: `851b02fc`, the same three seeds × one repeat per policy after caching optimizations. All six semantic digests equal their before-cache repeat 1 counterparts.
- `normal-before-cache/`, `normal-final/`: measured latency, random opponent, seed 55, maximum 2400 ticks and target 15 placements; full reports and ledgers.
- `replay-final/`: independent measured-latency run, two placements, 220 ticks, full replay and receipts. Every tick and the final state hash were reconstructed from inputs.
- `calibration.json`: six fixed boards and three shared quotas; local calibration rather than a quality or latency gate.
- `manifest.json`: source revisions, environment and original/stored checksums. Per-run declarations and native diagnostics provide configuration, source fingerprints and binary provenance.
- `tests.log`: 145 related tests passing. A subsequent assertion checks cached/fresh batch equality under changed public opponent state, request identity and reachability.

Gzip files contain the full unmodified output, except `baseline/fixtures.json` as noted above. The compressed replay expands to approximately 40 MB because per-tick diagnostics repeat full candidate batches.

Verify checksums, all paired semantic digests, historical v1 records and replay hashes from the repository root:

```bash
PYTHONPATH=. python docs/benchmarks/puyo-266-safe-build/verify.py
```
