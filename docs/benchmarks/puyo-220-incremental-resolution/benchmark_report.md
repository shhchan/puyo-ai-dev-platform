# PUYO-220 differential virtual resolution verification

Decision: **PASS**.

Virtual resolve plus remaining structure fell from 3130.497 ms to 231.108 ms per 600,000 nodes (92.618% reduction).
The fixed PUYO-219 allocation is 236.044 ms; measured margin is 4.935 ms.
Combined p95 changed from 4961.052 ms to 3244.640 ms.

## Gates

- fixture / transition / selected-child mismatches: 0 / 0 / 0
- incremental/exact candidate comparisons: 256, mismatches: 0
- response SHA-256 and all logical counter distributions: unchanged
- normal hot-path allocations: 0; child/result ABI: 80/24 bytes
- production max_added_puyos remains 3
