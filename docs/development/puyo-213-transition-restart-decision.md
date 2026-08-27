# PUYO-213 transition restart decision

- Status: **NO_GO_STOP**
- Decision date: 2026-08-27
- Integration authority: `integration/puyo-113-v1-7-2` at
  `b5865038d1bffa0970baaad20aa79369fa21d7f1`
- Machine-readable decision:
  [`final_decision.json`](../benchmarks/puyo-213-transition-restart-decision/final_decision.json)
- Production impact: none

## Decision

Stop the current deep-chain native implementation line before PUYO-201. Do not
start the evaluator/quiescence port, PUYO-202, or any later native task from the
current transition baseline. PUYO-207 remains `NO_GO`; this decision does not
rewrite its fixed 100.0 ns mixed or 50.0 ns quiet target and does not accept the
quiet miss as a pass.

`GO_OPTIMIZED` is unavailable because PUYO-211 selected no implementation
candidate and PUYO-212 produced no candidate commit or PR. The alternative
`GO_WITH_RISK_ACCEPTANCE` is not selected: the combined result has not been
measured, the same-wheel baseline is not stable enough to support a bounded
comparison, and no human reviewer approval to waive the component miss is
recorded.

## PUYO-212 and repository audit

The PUYO-212 completion comment records
`NO_IMPLEMENTATION_CANDIDATE`, `selected_candidate: null`, no implementation
PR, and no repository change. The independent repository and GitHub audit
confirmed:

| Check | Result |
| --- | --- |
| PR #105 | merged to the integration branch as `5e76617` |
| PR #106 | merged to the integration branch as `b586503` |
| PUYO-212 PR search | no matching PR |
| Integration head | `b586503`; identical to the PUYO-213 branch point |
| `native/deep_chain_native` diff, `5e76617..b586503` | empty |
| PUYO-212 candidate commit | none |

The only commits after PR #105 are the PUYO-211 investigation harness and
no-candidate evidence plus PR #106's merge commit. No candidate kernel entered
the integration branch. PUYO-209 is the cancelled `DROP` predecessor and has
no artifact or PR authority; PUYO-211 is its formal replacement.

## Evidence review

Both checked-in evidence verifiers pass and bind the source commit, release
wheel, frozen corpora, raw samples, and manifest digests. PUYO-207 remains the
component authority:

| Evidence | Observed | Fixed gate | Result |
| --- | ---: | ---: | --- |
| mixed p95 | 77.119 ns | <= 100.0 ns | pass |
| quiet p95 | 57.325 ns | <= 50.0 ns | **fail** |
| parity / deterministic mismatch | 0 | 0 | pass |
| normal hot-path allocation | 0 | 0 | pass |
| child state / hot result | 80 / 24 bytes | 80 / 24 bytes | pass |

At 600,000 calls the mixed value projects to 46.272 ms and leaves 774.353 ms
inside the unchanged 820.625 ms transition-plus-evaluator envelope. That is
not a measured combined result: PUYO-201 does not exist, and satisfying the
residual would require about 1,869.269x speedup against the frozen Python
evaluator reference. The native 900 ms, end-to-end 1,000 ms, and 600,000-node
limits remain unchanged.

PUYO-211 then ran the identical PUYO-207 wheel in three fresh processes:

| Run | mixed p95 ns | quiet p95 ns |
| ---: | ---: | ---: |
| 1 | 106.607 | 41.552 |
| 2 | 108.904 | 65.578 |
| 3 | 98.655 | 50.918 |

Two mixed runs and two quiet runs miss their fixed targets. Same-wheel p95
drift reached 30.0% for mixed and 33.3% for quiet. The strongest safe candidate
has only a 0.521 ns conservative saving; its projection still leaves two mixed
failures and a 50.397 ns quiet median, above the required 45.0 ns engineering
margin. These results support neither an optimized restart nor a stable
combined prototype exception.

## Decision matrix

| Candidate outcome | Required authority | Finding |
| --- | --- | --- |
| `GO_OPTIMIZED` | selected candidate, all gates, reviewed merged PR | rejected; none exists |
| `GO_WITH_RISK_ACCEPTANCE` | stable feasibility plus explicit human approval | not granted |
| `NO_GO_STOP` | no sufficient optimization or combined-feasibility basis | **selected** |

## Jira and downstream state

| Ticket | Final state | Rule |
| --- | --- | --- |
| PUYO-200 | Complete | negative performance result is closed; no more work on this line |
| PUYO-201 | To Do / blocked | do not implement evaluator/quiescence |
| PUYO-202 | To Do / blocked by the stopped line | do not start |
| PUYO-207 | Complete / `NO_GO` | decision and fixed targets remain authoritative |
| PUYO-213 | Complete / `NO_GO_STOP` | final successor decision |

The existing `PUYO-200 blocks PUYO-201`, `PUYO-207 blocks PUYO-201`, and
`PUYO-213 blocks PUYO-201` links remain as the audit chain. No production
backend, search behavior, schema, ABI, corpus, or performance target changes.

## Successor policy

This closes the current line; it is not a permanent ban on a separately
authorized design. Any successor must start from a new ADR, preserve or
explicitly replace the source-bound target with human approval, provide a
stable controlled baseline and a selected candidate, and define independent
semantic, allocation, memory, and combined-budget gates before implementation.
The stopped PUYO-201/202 chain cannot be reused to bypass that review.

## Verification

Run from the repository root:

```bash
.venv/bin/python -m eval.deep_chain_native_transition_profile verify \
  --ticket PUYO-207
.venv/bin/python -m eval.deep_chain_native_transition_investigation verify \
  --artifact-dir docs/benchmarks/puyo-211-quiet-transition-investigation
.venv/bin/python -m eval.deep_chain_native_transition_restart_decision
.venv/bin/python -m unittest \
  tests.test_deep_chain_native_transition_profile \
  tests.test_deep_chain_native_transition_investigation \
  tests.test_deep_chain_native_transition_restart_decision
```
