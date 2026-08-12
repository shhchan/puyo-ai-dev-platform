# PUYO-179 Seeded Hidden-Future Sampling

Canonical runtime, smoke, and quality profiles sample only the hidden
future after current + NEXT 2 through the production `PuyoSequence`
distribution. `legacy-fixed-six` remains an explicit regression profile.

## Checks

- known_prefix_preserved: PASS
- authoritative_generator_match: PASS
- same_seed_queue_digest: PASS
- different_seed_changes_queue: PASS
- independent_samples: PASS
- canonical_has_no_two_pair_cycle: PASS
- canonical_profiles_are_seeded: PASS
- profile_sample_and_count_budgets_recorded: PASS
- legacy_fixed_six_is_explicit: PASS
- same_seed_latency_free_proposal_digest: PASS
- proposal_v2_k8_stable_ids_and_masks: PASS
- sample_order_invariant_ranker_input: PASS
- sample_id_not_ranker_feature: PASS
- passed: PASS

- verdict: PASS
