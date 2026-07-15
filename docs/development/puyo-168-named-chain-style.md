# PUYO-168 Named chain style extension contract

## Boundary

Named chain styles are an opt-in objective extension. `BuildPotential` remains a
generic estimate of chain size, ignition cost, flexibility, recoverability, and
danger. It does not recognize or reward GTR or any other named form.

The default selection is:

```yaml
schema_version: puyo.chain_style.v1
style_id: unconstrained
style_version: "1.0"
constraint_mode: unconstrained
weight: 0.0
```

When this selection is active, the planner does not inject a style evaluator
into beam search. Existing `build_main` candidate generation, pruning, score,
and safe-build behavior therefore remain unchanged.

## Registry and providers

`train/config/v1_7_chain_styles.yaml` maps a versioned style id to a provider
id. Providers implement the board evaluation contract in
`agents.chain_styles.ChainStyleProvider` and return:

- whether the style is applicable to the candidate;
- an adherence score in `[0, 1]`;
- whether a hard constraint is satisfied;
- provider-owned diagnostics.

`soft_preference` adds `chain_weight * style_weight * adherence_score` to the
separate `style_adherence` contribution. `hard_constraint` filters only
applicable candidates that explicitly fail the provider constraint. A provider
must not modify generic `BuildPotential` values.

The checked-in `gtr@1.0` entry uses a fixture/stub provider. It proves registry,
planner, checkpoint, dataset, and artifact round-trips but intentionally does
not detect GTR or contribute to scoring.

## Validation and fallback

Selections validate schema version, id/version presence, constraint mode, and a
non-negative weight. Unknown ids, version mismatches, deprecated definitions,
and missing providers resolve deterministically to `unconstrained@1.0`. The
planner request records both `requested` and `selected`, plus a diagnostic code
and `fallback_applied` flag.

## Persistence and metrics

The selected id and version are serialized in `TacticSpec`, planner requests,
manager checkpoint metadata, bootstrap dataset metadata, and tactic registry
artifacts. The v2 tactic registry migration inserts the explicit unconstrained
selection without changing feature shapes or learned weights.

Search diagnostics keep two namespaces:

- `generic_capability`: BuildPotential and its score contribution;
- `style_adherence`: provider result and style-only score contribution.

Promotion/capability gates should consume `generic_capability`. A future
style-conditioned model may define a separate adherence gate without changing
the generic safe-build contract.
