# PUYO-221 native evaluator independent revalidation

PUYO-201 で固定した source-bound workload に対し、PUYO-220、PUYO-222、
PUYO-223、PUYO-224、PUYO-225、PUYO-226、PUYO-227 の最適化を取り込んだ
transition + chain-structure evaluator を独立に再検証した。

## Decision

Decision: **GO**.

PUYO-202 は block 解除候補にできる。これは native backend の production
昇格を意味せず、昇格には別の判断が必要である。

## Canonical contract

- frozen source states: 512
- frozen transition oracle: 11,264 transitions
- selected legal action: 1 per source state
- release wheel, single evaluator thread
- warmup: 10,000 operations
- samples: 5 x exactly 600,000 operations
- percentile: nearest-rank p95
- timeout substitution, fallback timing, outlier removal: none

## Results

| Gate | Observed p95 | Limit | Result |
| --- | ---: | ---: | --- |
| transition + evaluator | 627.423 ms | 820.625 ms | pass |
| native call total | 628.382 ms | 900.000 ms | pass |
| end-to-end | 630.228 ms | 1,000.000 ms | pass |

- fixture parity: 0 mismatches / 8 records
- transition parity: 0 mismatches / 11,264 records
- Python/native evaluator parity: 0 mismatches / 512 records
- deterministic response/checksum mismatches: 0
- normal hot-path allocations: 0
- child/result ABI: 80/24 bytes

The formal measurement is bound to source commit
`f24ace63c1d8b25fad032874740bd6e437b1b08c` and release wheel SHA-256
`073766e650cdcccab789c510911d84f0e0f9673d839256c47659d5d6eb8d4ac4`.
The manifest records the corpus, configuration, installed module, CPU, Python,
Rust toolchain, and every generated artifact digest.

## Evidence and reproduction

The complete raw samples and decision inputs are stored in
`docs/benchmarks/puyo-221-native-evaluator-revalidation/`.

```bash
./scripts/build_deep_chain_native.sh
.venv/bin/python -m eval.deep_chain_native_evaluator_revalidation run
.venv/bin/python -m eval.deep_chain_native_evaluator_revalidation verify --require-exact-wheel
```
