# PUYO-178 Build-main fire ranking benchmark

- status: PASS
- ranking rule: `puyo.expected_chain_ranking.v2`
- fixture fire classes: True
- quiet > premature: True
- target > quiet: True
- root quota covered: True
- compact/authoritative parity: True
- Proposal v2 K=8 compatibility: True
- latency-free 2-repeat determinism: True
- response guard: 6/6

## Premature terminal score trace

- target gap: 9.0
- target-gap penalty: -9000.0
- structural premature-fire penalty: -10000.0
- trigger damage: 2
- danger: 0.029166666666666664
- terminal score: -76391.33333333333

Reproduce with:

```bash
python -m eval.v1_7_build_main_fire_ranking_benchmark run
python -m eval.v1_7_build_main_fire_ranking_benchmark verify
```
