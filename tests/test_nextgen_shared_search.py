"""Fixed-budget six-tactic batch, actual Python/native parity and provenance."""

import importlib.util
import unittest
from dataclasses import replace
from unittest.mock import patch

from agents import nextgen_contracts as c
from agents import template_catalog as template_module
from agents.deep_chain_builder import DeepChainBuilderPolicy
from agents.deep_chain_search_backend import (
    NativeLongHorizonSearchBackend,
    PythonLongHorizonSearchBackend,
)
from agents.long_horizon_search import LongHorizonSearchConfig
from agents.nextgen_shared_search import (
    ResponseProposal,
    ResponseSearchResult,
    SharedSearchBatchBuilder,
    SharedSearchCache,
    scenario_provenance,
)
from agents.template_catalog import TemplateCatalog, match_templates


def config(**values):
    return LongHorizonSearchConfig(
        **dict(
            depth=4,
            width=2,
            scenarios=2,
            minimum_chain_count=2,
            max_expanded_nodes=200,
            decision_seed=23,
            **values,
        )
    )


def catalog():
    return TemplateCatalog.from_dict(
        {
            "schema_version": "puyo.template_catalog.v1",
            "enabled": True,
            "selection": {
                "mode": "argmax",
                "temperature": 0.2,
                "seed_stream": "template",
            },
            "default_commit_turns": 14,
            "templates": [
                {
                    "id": "fixture",
                    "version": "1",
                    "enabled": True,
                    "commit_turns": None,
                    "variants": [
                        {
                            "id": "base",
                            "origin": {"x": 0, "y_from_bottom": 0},
                            "pattern_rows_bottom_up": ["A"],
                            "same": [],
                            "different": [],
                            "empty_cells": [],
                            "occupied_cells": [],
                            "weight": 1.0,
                            "transforms": ["identity"],
                        }
                    ],
                }
            ],
        }
    )


def request(
    cfg=None,
    *,
    quota=(160, 8, 7),
    board=None,
    mask=None,
    active=False,
    threat=False,
    cat=None,
):
    cfg = cfg or config()
    player = c.PublicPlayerState(
        board or tuple((0,) * 6 for _ in range(14)),
        ((1, 2), (2, 3), (3, 4)),
        "control",
        (c.PublicAttackPacket("packet", 12, 30, None),) if threat else (),
        0,
        False,
        False,
        False,
    )
    public = c.PublicSnapshot(player, replace(player, attack_packets=()), ())
    h = c.semantic_digest("fixture")
    return c.NextgenRequest(
        c.DecisionIdentity("episode", 0, 1, "request", public.digest),
        public,
        c.ExecutionContext(
            mask if mask is not None else (True,) * c.NUM_ACTIONS,
            0,
            10,
            "fixture",
            h,
            "configured",
        ),
        c.ControlContext(
            c.PhaseSnapshot(
                "phase" if active else None,
                "fixture" if active else None,
                active,
                14,
                14,
                0.0,
                "unknown",
                None,
                0,
            ),
            c.SearchProfile("smoke", *quota),
            cat.semantic_digest if cat else h,
            scenario_provenance(player.known_pieces, cfg),
        ),
    )


class CountingBackend(PythonLongHorizonSearchBackend):
    def __init__(self):
        self.calls = []

    def search(self, request):
        self.calls.append(request)
        return super().search(request)


class MockResponse:
    def __init__(self):
        self.calls = []

    def search(self, context, budget):
        self.calls.append(context)
        if not budget.consume(feature_evaluations=1):
            return ResponseSearchResult(
                status="partial",
                cutoff_reason="response_quota",
                cancel_reason="not_found_within_budget",
                counter_reason="not_found_within_budget",
            )
        plan = (
            c.PlanStep(
                context.legal_roots[0],
                context.request.public.own.known_pieces[0],
                "public_known",
            ),
        )
        return ResponseSearchResult(
            (
                ResponseProposal(
                    plan,
                    ("cancel",),
                    (
                        c.NamedEvidence(
                            "canceled",
                            c.NumericEvidence(1, "partial", "public_estimate"),
                        ),
                    ),
                ),
            ),
            "partial",
            None,
            "available",
            "not_found_within_budget",
        )


class SharedBatchTests(unittest.TestCase):
    def test_fire_and_response_dedup_cannot_promote_a_build_root(self):
        from agents.compact_search import transition
        from agents.nextgen_shared_search import _pairs, _public_state

        cfg = replace(config(), minimum_chain_count=10)
        board = ((0,) * 6,) * 11 + ((1, 0, 0, 0, 0, 0),) * 3
        req = request(cfg, board=board, quota=(160, 0, 7))
        result = SharedSearchBatchBuilder(
            PythonLongHorizonSearchBackend(), cfg, response_provider=MockResponse(),
        ).build(req)
        state, _ = _public_state(req)
        pair = _pairs(req.public.own.known_pieces)[0]
        chosen = result.select("build_main")
        self.assertEqual(chosen.root_action, result.root_rankings[0])
        self.assertEqual(transition(state, pair, chosen.root_action).chain_count, 0)
        firing = [v for v in result.batch.candidates if len(v.plan) == 1
                  and transition(state, pair, v.root_action).chain_count > 0]
        self.assertTrue(firing)
        self.assertTrue(any("decisive_short_attack" in v.tactics and "build_main" in v.tactics for v in firing))
        # The response rank still chooses its own root, while build keeps the
        # quiet continuation. One plan retains one semantic candidate ID.
        self.assertEqual(result.select("cancel").root_action, 0)
        self.assertNotEqual(chosen.root_action, result.select("cancel").root_action)
        self.assertEqual(len({v.candidate_id for v in result.batch.candidates}), len(result.batch.candidates))
        c.validate_request_batch(req, result.batch)
        self.assertEqual(c.CandidateBatch.from_dict(result.batch.to_dict()), result.batch)
        build = result.batch.tactics[0]
        global_ids = tuple(v.candidate_id for v in sorted(result.batch.candidates, key=lambda v: v.rank)
                           if "build_main" in v.tactics and v.root_reachable)
        self.assertNotEqual(build.candidate_ids, global_ids)
        self.assertEqual(result.batch.schema_version, c.CANDIDATE_BATCH_SCHEMA_VERSION)
        with self.assertRaisesRegex(ValueError, "legacy.*rank"):
            # Strip the v3 extension before testing v1 fixed global ordering.
            legacy_candidates = tuple(replace(v, evidence=tuple(e for e in v.evidence if not e.name.startswith("survival_"))) for v in result.batch.candidates)
            legacy = replace(result.batch, candidates=legacy_candidates)
            replace(legacy, schema_version=c.LEGACY_CANDIDATE_BATCH_SCHEMA_VERSION)
        with self.assertRaises(ValueError):
            incomplete = replace(build, candidate_ids=build.candidate_ids[:-1])
            replace(result.batch, tactics=(incomplete, *result.batch.tactics[1:]))
        with self.assertRaises(ValueError):
            replace(build, candidate_ids=build.candidate_ids + (build.best_id,))
        with self.assertRaises(ValueError):
            unreachable = tuple(replace(v, root_reachable=False) if v.candidate_id == build.best_id else v for v in result.batch.candidates)
            replace(result.batch, candidates=unreachable)
        with patch.object(PythonLongHorizonSearchBackend, "search", side_effect=AssertionError("late search")):
            self.assertEqual(result.select("build_main"), chosen)

    def test_retry_reuses_only_pure_shared_search_and_rebuilds_public_batch(self):
        backend, response = PythonLongHorizonSearchBackend(), MockResponse()
        cfg = config()
        builder = SharedSearchBatchBuilder(
            backend, cfg, shared_cache=SharedSearchCache(), response_provider=response,
        )
        first = request(cfg, quota=(12, 0, 7))
        public = replace(
            first.public,
            own=replace(first.public.own, attack_packets=(c.PublicAttackPacket("new", 20, 1, None),)),
            opponent=replace(first.public.opponent, score_carry=10),
        )
        retry = replace(
            first, public=public,
            identity=replace(first.identity, decision_id=2, request_id="retry", snapshot_digest=public.digest),
            execution=replace(first.execution, request_tick=1, timeout_tick=11,
                              reachable_mask=(True,) * 21 + (False,)),
        )
        with patch.object(backend, "search", wraps=backend.search) as search:
            before = builder.build(first)
            after = builder.build(retry)
            self.assertEqual(search.call_count, 1)
        self.assertEqual(len(response.calls), 2)
        self.assertEqual(response.calls[-1].request, retry)
        self.assertTrue(after.diagnostics["shared_reuse"]["hit"])
        self.assertEqual(before.shared_result, after.shared_result)
        self.assertEqual(before.batch.counters.shared_nodes, after.batch.counters.shared_nodes)
        self.assertNotEqual(before.batch.identity, after.batch.identity)
        self.assertNotEqual(before.batch.digest, after.batch.digest)
        self.assertTrue(set(v.candidate_id for v in before.batch.candidates).isdisjoint(
            v.candidate_id for v in after.batch.candidates
        ))
        self.assertTrue(all(not v.root_reachable for v in after.batch.candidates if v.root_action == 21))
        fresh = SharedSearchBatchBuilder(backend, cfg, response_provider=MockResponse()).build(retry)
        self.assertEqual(after.deterministic_digest, fresh.deterministic_digest)
        c.validate_request_batch(retry, after.batch)

    def test_shared_cache_invalidates_all_backend_inputs_and_is_one_entry(self):
        backend = PythonLongHorizonSearchBackend()
        cache = SharedSearchCache()
        builder = SharedSearchBatchBuilder(backend, config(), shared_cache=cache)
        req = request(quota=(4, 0, 0))
        builder.build(req)
        _, key, _, _ = cache.entry
        changes = (
            {"root_state": replace(key.root_state, all_clear_bonus_pending=True)},
            {"root_state": replace(key.root_state, planes=(1, 0, 0, 0, 0, 0))},
            {"known_pairs": tuple(reversed(key.known_pairs))},
            {"search_config": replace(key.search_config, decision_seed=999)},
            {"search_config": replace(key.search_config, max_expanded_nodes=3)},
            {"evaluator_config": replace(key.evaluator_config, weight_version="other")},
            {"profile_name": "other"},
        )
        with patch.object(backend, "search", wraps=backend.search) as search:
            for change in changes:
                with self.subTest(change=change):
                    _, source = cache.search(backend, replace(key, **change))
                    self.assertIsNone(source)
                    _, source = cache.search(backend, key)
                    self.assertIsNone(source)
            self.assertEqual(search.call_count, 2 * len(changes))
        injected = CountingBackend()
        cache.search(injected, key)
        cache.search(injected, key)
        self.assertEqual(len(injected.calls), 2)
        self.assertIsNone(cache.entry)

    def test_quota_split_no_redistribution_and_select_does_not_search(self):
        backend, provider = CountingBackend(), MockResponse()
        cfg = config()
        req = request(cfg, quota=(7, 50, 4), threat=True)
        result = SharedSearchBatchBuilder(
            backend, cfg, response_provider=provider
        ).build(req)
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(backend.calls[0].search_config.max_expanded_nodes, 7)
        self.assertEqual(result.batch.counters.shared_nodes, 7)
        self.assertEqual(result.batch.counters.template_nodes, 0)
        self.assertEqual(result.batch.counters.response_nodes, 1)
        self.assertEqual(result.batch.action_mask[3], True)
        self.assertFalse(result.batch.tactics[3].known_witness)
        for _ in range(3):
            for row in result.batch.tactics:
                if row.available:
                    self.assertEqual(
                        result.select(row.tactic_id).candidate_id, row.best_id
                    )
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(len(provider.calls), 1)
        self.assertIs(provider.calls[0].shared_result, result.shared_result)

    def test_zero_quota_has_all_legal_roots_and_reachable_fallback(self):
        backend = CountingBackend()
        mask = tuple(i == 19 for i in range(c.NUM_ACTIONS))
        result = SharedSearchBatchBuilder(backend, config()).build(
            request(quota=(0, 0, 0), mask=mask)
        )
        self.assertEqual(backend.calls, [])
        self.assertEqual(len(result.root_rankings), c.NUM_ACTIONS)
        self.assertEqual(
            result.batch.action_mask, (True, False, False, False, False, False)
        )
        selected = result.select("build_main")
        self.assertEqual(selected.root_action, 19)
        self.assertTrue(selected.fallback)
        self.assertEqual(selected.tactics, ("build_main",))
        self.assertEqual(result.batch.counters.shared_nodes, 0)
        self.assertEqual(result.batch.status, "partial")
        with self.assertRaisesRegex(ValueError, "masked"):
            result.select("counter")

    def test_unavailable_empty_queue_terminal_and_unreachable(self):
        req = request()
        for player, mask in (
            (req.public.own, (False,) * c.NUM_ACTIONS),
            (replace(req.public.own, known_pieces=()), (True,) * c.NUM_ACTIONS),
            (replace(req.public.own, phase="game_over"), (True,) * c.NUM_ACTIONS),
        ):
            public = replace(req.public, own=player)
            modified = replace(
                req,
                public=public,
                identity=replace(req.identity, snapshot_digest=public.digest),
                execution=replace(req.execution, reachable_mask=mask),
                control=replace(
                    req.control,
                    scenario_provenance=scenario_provenance(
                        player.known_pieces, config()
                    ),
                ),
            )
            result = SharedSearchBatchBuilder(CountingBackend(), config()).build(
                modified
            )
            self.assertEqual(result.batch.status, "unavailable")
            self.assertEqual(result.batch.candidates, ())
            self.assertFalse(any(result.batch.action_mask))

    def test_template_witness_uses_its_own_quota_and_active_phase(self):
        cat = catalog()
        req = request(quota=(0, 8, 0), active=True, cat=cat)
        result = SharedSearchBatchBuilder(
            CountingBackend(),
            config(),
            template_catalog=cat,
            template_binding_budget=10,
        ).build(req)
        self.assertTrue(result.batch.action_mask[1])
        self.assertEqual(result.batch.counters.shared_nodes, 0)
        self.assertLessEqual(result.batch.counters.template_nodes, 8)
        self.assertTrue(result.select("build_template").plan)
        self.assertTrue(result.batch.tactics[1].known_witness)
        self.assertIsNotNone(result.template_result)
        inactive = replace(
            req,
            control=replace(
                req.control, phase=replace(req.control.phase, active=False)
            ),
        )
        off = SharedSearchBatchBuilder(
            CountingBackend(), config(), template_catalog=cat
        ).build(inactive)
        self.assertFalse(off.batch.action_mask[1])
        self.assertEqual(off.batch.counters.template_nodes, 0)
        self.assertEqual(off.batch.tactics[1].mask_reason, "inactive_phase")

    def test_unknown_board_stays_partial_and_never_becomes_known_witness(self):
        board = ((None,) * 6,) * 2 + ((0,) * 6,) * 11 + ((1, 1, 1, 0, 0, 0),)
        req = request(board=board)
        result = SharedSearchBatchBuilder(CountingBackend(), config()).build(req)
        self.assertEqual(result.batch.status, "partial")
        self.assertIn("public_board_incomplete", result.batch.cutoff_reason)
        self.assertFalse(any(t.known_witness for t in result.batch.tactics))
        self.assertTrue(result.batch.action_mask[5])
        for candidate in result.batch.candidates:
            for evidence in candidate.evidence:
                if evidence.name in ("score", "chain_count", "fire_depth"):
                    self.assertEqual(evidence.evidence.source, "public_estimate")
                    self.assertEqual(
                        evidence.evidence.status,
                        "partial"
                        if evidence.evidence.value is not None
                        else "not_evaluated",
                    )
        self.assertFalse(result.diagnostics["safety_guarantee"])

    def test_known_fire_masks_do_not_require_lethal_or_large_chain(self):
        board = ((0,) * 6,) * 13 + ((1, 1, 1, 0, 0, 0),)
        result = SharedSearchBatchBuilder(CountingBackend(), config()).build(
            request(board=board)
        )
        self.assertTrue(result.batch.action_mask[5])
        fire = result.select("decisive_short_attack")
        self.assertTrue(all(s.provenance == "public_known" for s in fire.plan))
        self.assertTrue(result.batch.tactics[5].known_witness)
        self.assertEqual(result.batch.tactics[3].mask_reason, "no_public_threat")
        self.assertEqual(result.batch.tactics[4].mask_reason, "no_public_threat")

    def test_unsupported_response_does_not_spend_or_borrow_budget(self):
        result = SharedSearchBatchBuilder(CountingBackend(), config()).build(
            request(threat=True)
        )
        self.assertEqual(result.batch.tactics[3].mask_reason, "unsupported")
        self.assertEqual(result.batch.tactics[4].evaluation_status, "unsupported")
        self.assertEqual(result.batch.counters.response_nodes, 0)

    def test_provenance_and_template_hash_mismatch_fail_before_search(self):
        backend = CountingBackend()
        req = request()
        bad = replace(
            req,
            control=replace(
                req.control,
                scenario_provenance=replace(
                    req.control.scenario_provenance, scenario_digest="a" * 64
                ),
            ),
        )
        with self.assertRaisesRegex(ValueError, "scenario provenance"):
            SharedSearchBatchBuilder(backend, config()).build(bad)
        with self.assertRaisesRegex(ValueError, "template config"):
            SharedSearchBatchBuilder(
                backend, config(), template_catalog=catalog()
            ).build(req)
        self.assertEqual(backend.calls, [])

    def test_sampled_fire_and_full_quiet_support_are_not_known_attack_or_safety(self):
        class SampledOnlyBackend(CountingBackend):
            def search(self, request):
                execution = super().search(request)

                def sampled(fire):
                    return (
                        None
                        if fire is None
                        else replace(fire, depth=4, path=(fire.root_action, 0, 0, 0))
                    )

                roots = tuple(
                    replace(
                        root,
                        scenario_values=tuple(
                            replace(
                                v,
                                best_fire=sampled(v.best_fire),
                                selected_fire=sampled(v.selected_fire),
                            )
                            for v in root.scenario_values
                        ),
                    )
                    for root in execution.result.root_evidence
                )
                return replace(
                    execution, result=replace(execution.result, root_evidence=roots)
                )

        board = ((0,) * 6,) * 13 + ((1, 1, 1, 0, 0, 0),)
        sampled = SharedSearchBatchBuilder(SampledOnlyBackend(), config()).build(
            request(board=board)
        )
        self.assertTrue(any(v.support for v in sampled.shared_result.root_evidence))
        self.assertFalse(sampled.batch.action_mask[2])
        self.assertFalse(sampled.batch.action_mask[5])
        cfg = replace(config(), depth=1, scenarios=6)
        quiet = SharedSearchBatchBuilder(CountingBackend(), cfg).build(
            request(cfg, quota=(500, 0, 0))
        )
        self.assertTrue(
            any(v.quiet_support == 6 for v in quiet.shared_result.root_evidence)
        )
        self.assertFalse(quiet.diagnostics["safety_guarantee"])
        for candidate in quiet.batch.candidates:
            evidence = {v.name: v.evidence for v in candidate.evidence}
            self.assertIsNone(evidence["trigger_survives"].value)
            self.assertEqual(evidence["fatal_rate"].status, "partial")

    def test_template_feature_counter_matches_actual_evaluation_calls(self):
        cat = catalog()
        req = request(quota=(0, 8, 0), active=True, cat=cat)
        with patch(
            "agents.template_catalog._evaluate", wraps=template_module._evaluate
        ) as evaluate:
            result = SharedSearchBatchBuilder(
                CountingBackend(), config(), template_catalog=cat
            ).build(req)
        self.assertGreater(evaluate.call_count, 0)
        self.assertEqual(
            result.template_result.feature_evaluations, evaluate.call_count
        )
        self.assertEqual(result.batch.counters.feature_evaluations, evaluate.call_count)
        direct = match_templates(
            cat,
            req.public.own.visible_board,
            req.public.own.known_pieces,
            node_budget=8,
            binding_budget=4096,
            reachable_mask=req.execution.reachable_mask,
        )
        self.assertEqual(direct, result.template_result)

    def test_policy_entry_point_uses_existing_backend(self):
        backend = CountingBackend()
        policy = DeepChainBuilderPolicy(profile="smoke", search_backend=backend)
        result = policy.build_candidate_batch(
            request(quota=(3, 0, 0)), search_config=config()
        )
        self.assertTrue(result.batch.action_mask[0])
        self.assertEqual(len(backend.calls), 1)
        self.assertEqual(result.batch.counters.shared_nodes, 3)

    def test_all_evidence_dimensions_have_explicit_missingness(self):
        result = SharedSearchBatchBuilder(CountingBackend(), config()).build(
            request(quota=(0, 0, 0))
        )
        for candidate in result.batch.candidates:
            self.assertEqual(
                tuple(e.name for e in candidate.evidence), c.EVIDENCE_NAMES
            )
            self.assertTrue(all(e.evidence.value is None for e in candidate.evidence))

    def test_provider_budget_overrun_rejected(self):
        class BadProvider:
            def search(self, context, budget):
                budget.nodes = budget.quota + 1
                return ResponseSearchResult()

        with self.assertRaisesRegex(ValueError, "response quota"):
            SharedSearchBatchBuilder(
                CountingBackend(), config(), response_provider=BadProvider()
            ).build(request(threat=True))

    @unittest.skipUnless(
        importlib.util.find_spec("_puyo_deep_chain_native"),
        "native release extension unavailable",
    )
    def test_repeat_and_real_native_parity_all_roots_evidence_and_provenance(self):
        for seed, quota, mode, hidden in (
            (23, 1, "oracle-1", False),
            (24, 80, "oracle-1", False),
            (25, 500, "oracle-1", False),
            (26, 160, "scenario-6", False),
            (27, 160, "scenario-6", True),
        ):
            native = NativeLongHorizonSearchBackend(execution_mode=mode)
            cfg = replace(config(), decision_seed=seed)
            cat = catalog()
            board = (
                None
                if seed < 26
                else (
                    ((None if hidden else 0,) * 6,) * 2
                    + ((0,) * 6,) * 11
                    + ((1, 1, 1, 0, 0, 0),)
                )
            )
            req = request(cfg, quota=(quota, 8, 3), cat=cat, active=True, board=board)
            python = SharedSearchBatchBuilder(
                CountingBackend(), cfg, template_catalog=cat
            ).build(req)
            first = SharedSearchBatchBuilder(native, cfg, template_catalog=cat).build(
                req
            )
            repeat = SharedSearchBatchBuilder(native, cfg, template_catalog=cat).build(
                req
            )
            with self.subTest(seed=seed, quota=quota):
                c.validate_request_batch(req, python.batch)
                c.validate_request_batch(req, first.batch)
                self.assertEqual(python.root_rankings, first.root_rankings)
                self.assertEqual(python.provenance, first.provenance)
                self.assertEqual(python.batch.digest, first.batch.digest)
                self.assertEqual(
                    first.deterministic_digest, python.deterministic_digest
                )
                self.assertEqual(
                    repeat.deterministic_digest, first.deterministic_digest
                )
                self.assertEqual(
                    python.shared_result.root_evidence,
                    first.shared_result.root_evidence,
                )
                self.assertEqual(first.diagnostics["backend"]["boundary_call_count"], 1)
                self.assertEqual(
                    c.CandidateBatch.from_dict(first.batch.to_dict()), first.batch
                )


if __name__ == "__main__":
    unittest.main()
