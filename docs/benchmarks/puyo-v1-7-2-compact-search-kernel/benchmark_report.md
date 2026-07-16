# PUYO-172 compact search kernel parity

- result: **PASS**
- fixed fixtures: 9 cases / 0 mismatches
- seeded corpus: 1408 transitions / 0 mismatches
- deterministic repeat: **PASS** (2 runs)
- hash collisions: 0 across 1175 unique states

## Checks

- `fixed_fixture_parity`: **PASS**
- `seeded_transition_parity`: **PASS**
- `legal_action_parity`: **PASS**
- `minimum_transition_count`: **PASS**
- `deterministic_repeat`: **PASS**
- `hash_collision_free`: **PASS**

## Profile

- authoritative clone + transition: 0.118856 s
- compact transition: 0.164345 s
- observed compact speedup: 0.723x
- canonical compact state: 87 bytes
- serialized authoritative snapshot: 6297 bytes

Wall-clock values are observational and excluded from the deterministic digest. Performance is not a Go condition for PUYO-172.

## Provenance

- authoritative oracle: `src/core/headless.py` at `48d795181619ed6c6add15bdc6a2f9aac5f6443f`
- Ama analysis reference: v2.0.1 `dea210bcd92965ae08fbc311f23565b0fab6dbbb` (MIT)
- copied Ama code: none
