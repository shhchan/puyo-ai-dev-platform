# PUYO-201 native chain-structure prototype

Decision: **NO_GO_CLOSE_PR_UNMERGED**.

The bounded transition-plus-evaluator prototype preserves exact semantics
but fails every latency envelope. It is not routed into production.

| Gate | Observed | Target | Result |
| --- | ---: | ---: | --- |
| exact operations | 600,000 | 600,000 | pass |
| transition + evaluator p95 | 13973.529 ms | <= 820.625 ms | fail |
| native call total p95 | 13983.520 ms | <= 900.000 ms | fail |
| end-to-end p95 | 13985.087 ms | <= 1000.000 ms | fail |
| fixture mismatches | 0 | 0 | pass |
| transition oracle mismatches | 0 | 0 | pass |
| evaluator Python/native mismatches | 0 | 0 | pass |

Nearest-rank p95 retains every sample; no outlier was removed. The profile
uses all 512 source states and their selected legal actions from the frozen
11,264-transition corpus. Each measured sample executes exactly 600,000
combined operations.

Per PUYO-213, the implementation PR must close unmerged and PUYO-202 must
remain To Do. Further implementation requires a new reviewed decision.
