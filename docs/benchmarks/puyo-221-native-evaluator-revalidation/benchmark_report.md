# PUYO-221 independent native evaluator revalidation

Decision: **GO**.

The optimized transition-plus-evaluator was independently measured against the frozen PUYO-201 source-state contract.

| Gate | Observed | Target | Result |
| --- | ---: | ---: | --- |
| exact operations per sample | 600,000 | 600,000 | pass |
| transition + evaluator p95 | 627.423 ms | <= 820.625 ms | pass |
| native call total p95 | 628.382 ms | <= 900.000 ms | pass |
| end-to-end p95 | 630.228 ms | <= 1000.000 ms | pass |
| fixture mismatches | 0 / 8 | 0 / 8 | pass |
| transition oracle mismatches | 0 / 11,264 | 0 / 11,264 | pass |
| evaluator mismatches | 0 / 512 | 0 / 512 | pass |
| normal hot-path allocations | 0 | 0 | pass |
| child/result ABI | 80/24 bytes | 80/24 bytes | pass |

## Raw samples

| Sample | Operations | Transition + evaluator | Native total | End-to-end |
| ---: | ---: | ---: | ---: | ---: |
| 0 | 600,000 | 609.846 ms | 610.578 ms | 611.777 ms |
| 1 | 600,000 | 602.159 ms | 602.826 ms | 604.305 ms |
| 2 | 600,000 | 627.423 ms | 628.382 ms | 630.228 ms |
| 3 | 600,000 | 604.260 ms | 604.859 ms | 605.848 ms |
| 4 | 600,000 | 593.307 ms | 593.825 ms | 594.771 ms |

Nearest-rank p95 retains all five observations. No timeout value, fallback timing, or outlier removal is used.

PUYO-202 is an unblock candidate only when every gate above passes. Promotion of the native backend remains a separate decision.

Re-run commands:

- `./scripts/build_deep_chain_native.sh`
- `.venv/bin/python -m eval.deep_chain_native_evaluator_revalidation run`
- `.venv/bin/python -m eval.deep_chain_native_evaluator_revalidation verify --require-exact-wheel`
