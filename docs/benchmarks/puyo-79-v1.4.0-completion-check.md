# PUYO-79 / PUYO-56 Completion Check

This document is the human-readable checkpoint for the final PUYO-56 task.

## PUYO-79 Evidence

Run the reproducible smoke suite:

```bash
python -m eval.benchmark_suite \
  --output-dir docs/benchmarks/puyo-79-smoke \
  --seed 1 \
  --games 1 \
  --max-steps 8 \
  --max-ticks 80 \
  --beam-depth 3 \
  --beam-width 6
```

Primary artifacts:

| artifact | purpose |
|---|---|
| `docs/benchmarks/puyo-79-smoke/benchmark_manifest.json` | suite manifest, digest, ablation matrix, recommendation |
| `docs/benchmarks/puyo-79-smoke/report.md` | human-readable trade-off and completion report |
| `docs/benchmarks/puyo-79-smoke/summary.csv` | metric means and 95% confidence intervals |
| `docs/benchmarks/puyo-79-smoke/metric_records.csv` | raw metric records for re-aggregation |

## PUYO-56 Completion Criteria

| condition | evidence |
|---|---|
| 数値 objective に応じて探索挙動が変化する | PUYO-74 implementation plus PUYO-79 tactical/realtime diagnostics in benchmark manifest |
| 学習済み制御器が固定 worker 群と比較可能である | PUYO-75/76 manager controls and PUYO-79 baseline/ablation matrix |
| N 手 plan と各手の予測連鎖・攻撃・再計画条件を取得できる | PUYO-77 plan API and realtime replay diagnostics |
| 大連鎖性能と対戦性能の trade-off を固定 seed・信頼区間付きで報告できる | PUYO-79 `summary.csv` and `report.md` |

## PUYO-57 / PUYO-58 Readiness

| epic | readiness check |
|---|---|
| PUYO-57 / v1.5.0 | Ready to start UI integration once PUYO-79 is merged. The UI should treat `recommended_model.feasible` as a gate/warning because smoke latency may exceed the configured budget. |
| PUYO-58 / v1.6.0 | Not implementation-ready before PUYO-57, by Jira dependency. Benchmark artifacts are lineage-visible and reusable for the later promotion gate. |

PUYO-58 still depends on PUYO-57 UI work by design, so it should not start as implementation before PUYO-57 establishes the integrated UI and controls.
