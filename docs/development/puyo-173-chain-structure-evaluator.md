# PUYO-173 structural chain evaluator

## Responsibility boundary

`agents.chain_structure` is the cheap, generic ordering evaluator for compact
beam nodes. Its input is `CompactSearchState`; it does not construct or clone a
`HeadlessPuyoSimulator`, consume a future-tsumo queue, or inspect named chain
styles. The default production beam backend remains `legacy`. Experiments opt
in with `node_evaluator_backend="chain_structure_v1"` without changing beam
depth, width, or scenario count.

`BuildPotential v2` remains the authoritative, higher-cost diagnostic for the
final survivor/final K and safe-build gate analysis. The structural evaluator
does not change `puyo.build_potential.v2`, its cache or budget, trigger
recoverability, checkpoint metadata, or benchmark schema.

Named GTR, new-GTR, Fron, and other form recognition remains provider-owned in
`agents.chain_styles`. The structural result uses the
`generic_chain_structure` metric namespace and contains no named-style signal.

## Versioned result

The result schema is `puyo.chain_structure_evaluation.v1`, feature version is
`puyo.chain_structure_features.v1`, and the checked-in weights are `v1.0` in
`train/config/v1_7_chain_structure.yaml`.

Every result carries:

- `evaluation_status`, `evaluated`, and `truncation_reason`;
- raw typed features, score breakdown, total score, and mirror-stable tie-break digest;
- color components with normalized IDs and their actual cells;
- one-key component bridge candidates;
- bounded quiescence candidates, gravity-tracked ignition/component relations, and remaining links;
- action-local tear, waste, trigger damage, premature-fire, danger delta, and death features.

Status meanings are:

| Status | Meaning |
|---|---|
| `available` | The count budget completed and at least one minimal ignition was found. |
| `not_found` | The count budget completed and proved no 1-3 key ignition. This includes an evaluated score of exactly zero. |
| `budget_exhausted` | An orbit-atomic pattern or resolution limit stopped the search. The result is evaluated but not a proof of absence. |
| `not_evaluated` | The caller did not evaluate the node. `score` and `features` are `null`. |

## Feature definitions

Raw values are not pre-weighted. Positive YAML weights reward capacity and
negative weights are costs.

| Feature | Unit and normalization | Sign |
|---|---|---|
| potential chain count | resolved chain steps after a minimal virtual ignition | positive |
| potential chain score | repository chain score points, no all-clear entitlement | positive |
| required key count | 1-3 same-color virtual puyos | negative |
| trigger height | zero-based landing row | negative |
| trigger protection | occupied non-virtual sides / in-bounds sides of anchor cells, `[0,1]` | positive |
| link-2 / link-3 | connected components of exactly two/three same-color puyos | positive |
| connectivity edges | orthogonal same-color edges, counted once | positive |
| connection candidates | gravity-reachable empty cells bridging two same-color components | positive |
| reachable ignitions | link-3 components with a gravity-reachable extension | positive |
| growth sites | distinct gravity-reachable component-adjacent cells | positive |
| foundation cells | supported normal puyos in rows 1-3 | positive |
| fold space | adjacent-column visible headroom available for a turn/fold | positive |
| roughness / spread / well / bump | raw row differences | negative |
| danger | symmetric center/peak/nuisance ratio, clamped to `[0,1]` | negative |
| nuisance / hidden-row puyos | raw cell count | negative |
| tear | lost connectivity plus lost one-key bridges across an action | negative |
| waste | resource loss or premature vanish count, without double counting, plus hidden-row growth | negative |
| trigger damage | lost potential chains plus extra required keys relative to the parent | negative |
| premature fire | one event when `0 < chain < target` | negative |

Column-height features use the lexicographically smaller of the board and its
reflection. Component IDs hash color plus canonical reflected cells. Trigger
columns and digest inputs are likewise reflection-normalized; actual cells and
placements remain in the detailed records so no location information is lost.

## Bounded quiescence

The evaluator enumerates gravity-valid same-color additions for 1, 2, and 3
keys. Column multisets and their horizontal reflections form one orbit. A
pattern or resolution budget is consumed only when the full orbit fits, so a
cutoff cannot retain only the left or right representative. A candidate is
discarded if a one-key-smaller gravity-compacted pattern already ignites.

Resolution uses compact bit planes and repository score tables. Each retained
candidate records chain count/score, key count, trigger position/height,
protection, remaining link-2/link-3/connectivity, extension space, and typed
ignition relations. The default limits are part of the versioned config:

- `max_added_puyos: 3`
- `max_pattern_nodes: 512`
- `max_resolution_nodes: 96`
- `max_candidates: 12`

The tie-break digest includes canonical planes, normalized features, the
canonical best ignition, search counters/status, action features, feature
version, and weight version. Wall clock and future tsumo state are excluded.

## Fatal override

Death, a proven unreachable trigger on a non-empty field, and a proven
structural dead end use `fatal_score` as the total. This is an override, not an
additive penalty. Positive quiescence, connectivity, shape, or foundation terms
therefore cannot compensate for a fatal state. A truncated search does not
claim the trigger is unreachable.

## Beam interface

`BeamSearchConfig.node_evaluator_backend` accepts:

- `legacy`: existing `evaluate_board` / `evaluate_chain_shape_v2` behavior;
- `chain_structure_v1`: compact structural evaluation for the root and every generated, non-game-over beam node.

The structure backend stores the full typed result in root and candidate
diagnostics and contributes a separate `chain_structure` value-breakdown term.
Legacy `chain_shape` and `danger` contributions are zero in that mode to avoid
double counting. BuildPotential probing and `style_adherence` remain separate.

## Benchmark and QA

The fixed corpus covers evaluated zero, extendable versus unreachable boards,
and trigger preservation. The tuning corpus contains non-canonical connected,
mixed, nuisance, and fold-space boards. The benchmark records baseline, cheap
quiescence, and full structural rankings, plus config, commit, seed, deterministic
digest, symmetry, budget bounds, and observed node throughput.

```bash
python -m pytest -q tests/test_chain_structure.py tests/test_beam_search.py
python -m eval.v1_7_chain_structure_benchmark verify
```

Artifacts live in `docs/benchmarks/puyo-v1-7-2-chain-structure/`.

## Reference boundary

Ama v2.0.1 at commit `dea210bcd92965ae08fbc311f23565b0fab6dbbb`
was reviewed for the separation of bounded quiet search, chain/key/trigger
signals, link/shape/nuisance terms, and action tear/waste terms. No source code
or numeric configuration weights were copied.
