# PUYO-213 transition restart decision

- Status: **GO_WITH_RISK_ACCEPTANCE**
- Decision date: 2026-08-27
- Human approver: Shion MORISHITA
- Integration authority: `integration/puyo-113-v1-7-2` at
  `b5865038d1bffa0970baaad20aa79369fa21d7f1`
- Machine-readable decision:
  [`final_decision.json`](../benchmarks/puyo-213-transition-restart-decision/final_decision.json)
- Production impact: none

## Decision

Authorize PUYO-201 to start in a new work session with one bounded combined
transition-plus-evaluator prototype. This authorization becomes actionable
only after PR #107 is reviewed and merged into
`integration/puyo-113-v1-7-2`. The new session must then re-read PUYO-201,
transition it from To Do to In Progress, and create its work branch from the
updated integration branch.

The decision is **GO_WITH_RISK_ACCEPTANCE**, not `GO_OPTIMIZED`. Shion
MORISHITA explicitly accepts the known quiet-transition and repeatability
risks for that prototype on 2026-08-27. The exception is limited to measuring
the combined design. It is not a retroactive pass for PUYO-207, permission to
change a fixed target, approval to promote the production backend, or
authorization to start PUYO-202 before the prototype passes.

PUYO-207 therefore remains `NO_GO`: its mixed target passed, its quiet target
failed, and no measured combined result exists yet. PUYO-211 still selected no
optimization candidate, and PUYO-212 still has no candidate commit or PR.

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
| quiet p95 | 57.325 ns | <= 50.0 ns | **fail, risk accepted only for prototype** |
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
margin. The prototype must treat this variability as a known risk rather than
as proof that the component gate passed.

## Why the bounded experiment is permitted

The existing line has useful positive evidence: semantic and deterministic
parity pass, the hot path allocates nothing, the ABI/memory constraints pass,
the authoritative mixed p95 passes 100 ns, and its 46.272 ms projection leaves
diagnostic room inside the outer envelope. It also has material unresolved
risk: the quiet p95 misses 50 ns, fresh-process repeatability is poor, no
optimization candidate exists, the combined result is unmeasured, and the
evaluator projection requires an extreme speedup.

Those facts do not establish feasibility, but they are sufficient to define a
falsifiable prototype. The human approval accepts the cost of running that
experiment while preserving all outer gates and a hard failure exit.

## Decision matrix

| Candidate outcome | Required authority | Finding |
| --- | --- | --- |
| `GO_OPTIMIZED` | selected candidate, all gates, reviewed merged PR | rejected; none exists |
| `GO_WITH_RISK_ACCEPTANCE` | explicit human approval plus bounded unchanged outer gates | **selected** |
| `NO_GO_STOP` | no accepted experiment despite the known risks | not selected after human approval |

## Bounded PUYO-201 prototype contract

PUYO-201's first and only initially authorized work unit is the combined
transition-plus-evaluator prototype. It passes only if one source-bound result
meets every row below:

| Gate | Required result |
| --- | --- |
| Expanded-node authority | exactly 600,000 nodes; no timeout or reduced profile substitute |
| Transition plus evaluator p95 | <= 820.625 ms |
| Native call total p95 | <= 900.000 ms |
| End-to-end p95 | <= 1,000.000 ms |
| Fixture, oracle, and Python/native parity mismatches | 0 |
| Determinism mismatches | 0 |
| Normal hot-path heap allocations | 0 |
| Child state / hot result | exactly 80 / 24 bytes |
| Production backend promotion | prohibited by this decision |

If any row fails, the PUYO-201 implementation PR must not merge and must be
closed unmerged. PUYO-202 remains To Do and must not start. Further work then
requires a new reviewed decision; target rewriting, outlier removal, node-count
reduction, fallback timing, and production routing cannot be used to convert a
failure into a pass.

## Jira and downstream state

| Ticket | Final state | Rule |
| --- | --- | --- |
| PUYO-200 | Complete | negative component result remains recorded; risk exception is downstream-only |
| PUYO-201 | To Do / `READY_TO_START_WITH_RISK_ACCEPTANCE` | start only in a new session after PR #107 merges |
| PUYO-202 | To Do / blocked | start only after PUYO-201 passes every prototype gate |
| PUYO-207 | Complete / `NO_GO` | fixed targets and quiet miss remain authoritative |
| PUYO-213 | Complete / `GO_WITH_RISK_ACCEPTANCE` | bounded successor decision |

The existing `PUYO-200 blocks PUYO-201`, `PUYO-207 blocks PUYO-201`, and
`PUYO-213 blocks PUYO-201` links remain as the audit chain. Their completed
states and this explicit exception document why PUYO-201 can start without
rewriting the historical No-Go. No production backend, search behavior,
schema, ABI, corpus, or performance target changes.

## Next-session handoff

The next Codex session must perform these steps in order:

1. Confirm PR #107 is reviewed and merged into
   `integration/puyo-113-v1-7-2`; do not start from the pre-decision head.
2. Fetch PUYO-201 from Jira, confirm it is To Do under PUYO-184 in Sprint 8,
   and re-read this decision and the PUYO-201 acceptance criteria.
3. Transition PUYO-201 to In Progress and create
   `PUYO-201/native-evaluator-quiescence-prototype` from the updated
   integration branch.
4. Implement and measure only the bounded combined prototype before widening
   scope to search, integration, or production work.
5. Merge no implementation and start no PUYO-202 work unless every gate in the
   preceding table passes with source-bound evidence.

This handoff is preparation only. PUYO-201 remains unstarted and To Do at the
end of PUYO-213.

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
