# PUYO-79 Benchmark And Ablation Report

- schema: `puyo.benchmark_suite.v1`
- digest: `ccb19d2a0ba657ade8a0ad778280d7f98459162675d2327f2d5d6a325fc01c77`
- dry_run: `False`
- recommended_model: `manager_rule`
- recommended_model_latency_feasible: `False`
- recommendation_reason: no candidate met latency budget; selected highest combined score

## Trade-off Summary

| suite | variant | metric | mean | 95% CI | count |
|---|---|---|---:|---:|---:|
| chain_search | beam | decision_ms | 33.160 | [33.160, 33.160] | 1 |
| chain_search | beam | max_chain | 0.000 | [0.000, 0.000] | 1 |
| chain_search | greedy | decision_ms | 8.291 | [8.291, 8.291] | 1 |
| chain_search | greedy | max_chain | 1.000 | [1.000, 1.000] | 1 |
| realtime_paired_arena | manager_rule | deadline_miss | 0.000 | [0.000, 0.000] | 2 |
| realtime_paired_arena | manager_rule | max_chain | 0.000 | [0.000, 0.000] | 2 |
| realtime_paired_arena | manager_rule | policy_elapsed_ms | 443.672 | [415.908, 471.437] | 2 |
| realtime_paired_arena | manager_rule | score_rate | 0.000 | [0.000, 0.000] | 2 |
| tactical_scenarios | manager_rule | accuracy | 1.000 | [1.000, 1.000] | 6 |

## Ablation Matrix

| ablation | purpose |
|---|---|
| objective_conditioning_off | 数値 objective が探索挙動へ与える寄与を測る |
| parameter_learning_off | 学習済み探索制御と固定 worker の差を測る |
| tactical_options_off | 非固定 profile / option 表現の寄与を測る |
| teacher_policy_off | teacher / BC 初期化の寄与を測る |
| latency_penalty_off | decision time と勝率・連鎖の trade-off を測る |

## PUYO-56 Completion Checklist

- objective_conditioned_search: covered by PUYO-74 and validated through tactical/realtime objective diagnostics
- learned_search_control_comparison: covered by PUYO-75 and comparable through benchmark summaries
- n_turn_plan_api: covered by PUYO-77 and validated through realtime replay diagnostics
- curriculum_teacher_selfplay: covered by PUYO-78 and tracked by artifact/lineage outputs
- tradeoff_report: covered by this PUYO-79 benchmark suite and markdown report

## Next Epic Readiness

- PUYO-57: ready to start UI integration; gate release selection on latency feasibility
- PUYO-58: not implementation-ready until PUYO-57 UI exists; lineage-compatible benchmark artifacts are available here
