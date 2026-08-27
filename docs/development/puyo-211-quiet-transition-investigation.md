# PUYO-211 quiet transition investigation

- Status: complete; **no implementation candidate selected**
- Baseline authority: PUYO-207 release wheel and measurement contract
- Canonical artifact: [benchmark manifest](../benchmarks/puyo-211-quiet-transition-investigation/benchmark_manifest.json)
- Production impact: none; the native production backend is unchanged

## Decision

Do not start the PUYO-212 production optimization from this investigation.
The identical PUYO-207 release wheel does not reproduce either fixed component
gate reliably across three fresh processes, and the strongest isolated
candidate is too small to create the required engineering margin.

This is an intentional `NO_IMPLEMENTATION_CANDIDATE` result. It does not relax
the PUYO-205/207 targets, remove samples, reinterpret p95, enlarge the compact
state, or authorize another optimization implicitly. PUYO-212 may proceed only
after a controlled baseline or a newly evidenced candidate can satisfy the
same fixed gates.

## Locked baseline reproduction

The run reused wheel SHA-256
`c7214697031e553b5d6ae9047e3dff918a134069f76f9264e183801458db06cc`
from source revision `cff9ea5d0295087db8332c2a6efa59db74f886ef`. CPU 0,
one thread, five warm-ups, 120 mixed samples, 40 samples per outcome,
nearest-rank percentile, frozen corpus, and no-outlier-removal remained
unchanged.

| Fresh process | mixed p95 ns | quiet p95 ns | one-chain p95 ns | multi-chain p95 ns |
| ---: | ---: | ---: | ---: | ---: |
| 1 | 106.607 | 41.552 | 374.439 | 637.352 |
| 2 | 108.904 | 65.578 | 337.608 | 447.915 |
| 3 | 98.655 | 50.918 | 381.168 | 444.590 |

Two of three mixed runs exceed 100 ns and two of three quiet runs exceed 50
ns. The quiet run-p95 median is 50.918 ns, above the required 45 ns engineering
margin. All raw observations, execution order, process IDs, affinity, and CPU
frequency provenance are retained in the canonical artifact.

Three additional same-wheel reference/control pairs show maximum absolute p95
drift of 30.0% for mixed and 33.3% for quiet. This is much larger than the 2%
per-run regression tolerance required for a code comparison, so a small code
difference cannot be resolved safely in this environment.

## PUYO-206 / PUYO-207 difference

PUYO-206 measured quiet p95 at 33.904 ns and PUYO-207 measured 57.325 ns. Both
commits contain the identical Git object for
`native/deep_chain_native/src/compact.rs`, and both use the same locked sample,
warm-up, corpus, and percentile contract. There is therefore no native
algorithm source change that explains the 23.421 ns difference.

The fresh-process and paired same-wheel results reproduce variance of the same
order. The gap is measurement/environment variation under this WSL2 setup,
not evidence for an algorithmic regression between PUYO-206 and PUYO-207.

## Quiet cost profile

Cycles use LFENCE-serialized RDTSC. Instructions, branches, and cache events
use Valgrind 3.22.0 Cachegrind simulation because WSL2 does not expose the
required hardware PMU events.

| Stage | adjusted p50 cycles | simulated instructions | branches | L1 data misses | writes |
| --- | ---: | ---: | ---: | ---: | ---: |
| direct placement | 44.417 | 133.317 | 14.163 | 0.303 | 21.734 |
| color-plane extraction | 34.554 | 101.055 | 4.853 | 3.467 | 9.008 |
| inserted connectivity | 60.550 | 354.997 | 10.765 | 0.000 | 36.004 |
| state/result materialization | 17.518 | 39.566 | 0.574 | 0.020 | 11.004 |
| score/lifecycle | 3.172 | 7.160 | 1.490 | 0.000 | 1.003 |

Inserted connectivity remains the largest stage by both cycles and simulated
instructions. The fixed semantic output remains an 80-byte child state plus a
24-byte result, with zero authorization to remove those stores or change the
ABI. The three-slice placement profile copies 80 bytes and updates 54 bytes.

## Candidate comparison

The isolated portable-scalar AB/BA microbenchmark compares the current parent
plus inserted-mask reconstruction with reusing color planes from the already
placed child state. It uses all 4,096 records in the locked quiet workload and
reports zero semantic mismatches.

| Candidate | Result | Decision |
| --- | --- | --- |
| Reuse placed child color planes | median ratio 0.7665; conservative p05 saving 0.521 ns | insufficient; no selection |
| Equal/different and topology specialization | connectivity is large, but branch-only benefit is not isolated | defer |
| Local degree/popcount prefilter | current three-step expansion already short-circuits; no positive evidence | defer |
| Reduce child/result stores | violates fixed 80/24-byte output contract | reject |
| Persistent component cache | enlarges state and was slower in prior evidence | reject |
| `target-cpu=native` codegen | violates portable scalar fallback | reject |

Applying only the plane-reuse p05 saving projects quiet p95 to 41.031, 65.057,
and 50.397 ns, with a 50.397 ns median. Mixed remains above target in two
runs. The candidate therefore cannot support the required margin and must not
be transferred to production as a speculative change.

## Fixed follow-up and rollback gate

Any later authorized candidate must retain the PUYO-205/207 contract and pass:

1. three fresh-process quiet p95 values all at or below 50.0 ns and their
   median at or below 45.0 ns;
2. three mixed p95 values all at or below 100.0 ns;
3. paired candidate/baseline mixed p95 median with no regression and every
   individual regression within 2%;
4. zero frozen/property mismatches, 80/24-byte ABI, zero normal hot-path heap
   allocations, and portable scalar fallback;
5. the unchanged 600,000-call model and outer combined/native/end-to-end
   budgets.

Failure of any condition stops the attempt and rolls back the candidate as one
local unit. Targets, samples, outlier policy, ABI, or corpus must not be changed
to obtain a pass.

## Verification

```bash
.venv/bin/python -m eval.deep_chain_native_transition_investigation verify \
  --artifact-dir docs/benchmarks/puyo-211-quiet-transition-investigation
.venv/bin/python -m unittest \
  tests.test_deep_chain_native_transition_profile \
  tests.test_deep_chain_native_transition_investigation
```
