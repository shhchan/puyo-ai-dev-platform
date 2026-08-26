# PUYO-200 native compact transition

- result: **FAIL**
- fixed fixtures: 9 / 0 mismatches
- frozen state-actions: 11264 / 0 native mismatches
- authoritative/Python corpus mismatches: 0
- deterministic serialized response: **PASS**

## Performance

| Scope | p50 wall (us) | p95 wall (us) | p50 kernel ns/transition | p95 kernel ns/transition |
| --- | ---: | ---: | ---: | ---: |
| single | 16.786 | 33.513 | 82.000 | 112.000 |
| batch | 166425.133 | 193158.576 | 78.048 | 130.345 |

Projected transition decision p95 is 78.207 ms for 600,000 nodes against the 10.596 ms allocation: **FAIL**.

The normal Rust transition path recorded zero heap allocations; binary batch parsing/result materialization is QA-boundary overhead and is included in wall latency.

## Internal native profile

| Outcome | Records | Fraction | p50 kernel ns | p95 kernel ns |
| --- | ---: | ---: | ---: | ---: |
| quiet | 9410 | 94.100% | 57.992 | 65.278 |
| one_chain | 570 | 5.700% | 339.442 | 595.730 |
| multi_chain | 20 | 0.200% | 435.400 | 1246.500 |
| invalid | 0 | 0.000% | - | - |

The allocation-free scalar transition itself exceeds the 17.66 ns/node allocation on quiet records; chain resolution adds gravity and repeated component scans. Python/FFI result materialization is outside the reported kernel timer.

Stop condition: Return the native boundary and representation to ADR review; do not start PUYO-201 or mask the miss with fallback timing.

## Contract

- state: `puyo.compact_search_state.v1`
- batch: `puyo.native_compact_transition_batch.v1`
- kernel path: `scalar`
- Ama reference: `dea210bcd92965ae08fbc311f23565b0fab6dbbb` (MIT); poppable-mask identity adapted with notice retained
