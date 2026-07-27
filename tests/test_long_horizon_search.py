import json
import math
import unittest
from collections import deque
from pathlib import Path

from agents.beam_search import (
    BUILD_POTENTIAL_SCHEMA_VERSION,
    CHAIN_STRUCTURE_NODE_EVALUATOR,
    COMPACT_LONG_HORIZON_SEARCH_BACKEND,
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotentialBudget,
)
from agents.long_horizon_search import (
    EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION,
    EXPECTED_CHAIN_RANKING_RULE_VERSION,
    FIRE_CLASS_FORCED_SAFETY,
    FIRE_CLASS_PREMATURE,
    FIRE_CLASS_QUIET,
    FIRE_CLASS_TARGET,
    FIRE_CONTEXT_FORCED_SAFETY,
    QUALITY_D16_PROFILE,
    TERMINAL_FIRE_CONTINUE,
    ChainFireEvidence,
    LongHorizonSearchConfig,
    ScenarioRootEvidence,
    aggregate_expected_chain_evidence,
    build_scenario_sequences,
    classify_build_main_fire,
    long_horizon_profile,
    run_long_horizon_search,
)
from agents.worker_proposals import (
    WORKER_PROPOSAL_SCHEMA_VERSION,
    build_worker_proposal_batch,
)
from puyo_env.actions import ACTION_TO_INDEX, action_to_placement, legal_action_mask
from src.core.constants import (
    GRID_HEIGHT,
    GRID_WIDTH,
    Direction,
    PuyoColor,
)
from src.core.headless import HeadlessPuyoSimulator, PlacementAction
from src.core.puyo import Puyo


FIRE_FIXTURE_PATH = Path("tests/fixtures/build_main_fire_cases.json")
FIRE_FIXTURE_COLORS = {
    "R": PuyoColor.RED,
    "B": PuyoColor.BLUE,
    "G": PuyoColor.GREEN,
    "Y": PuyoColor.YELLOW,
    "P": PuyoColor.PURPLE,
    "O": PuyoColor.OJAMA,
}


class _FastEvaluation:
    def __init__(self, state):
        self.score = float(
            state.cell_count * 4
            - sum(height * height for height in state.column_heights)
        )
        self.danger = min(1.0, max(state.column_heights, default=0) / 14.0)
        self.continuation_flexibility = max(0.0, 1.0 - self.danger)
        self.tie_break_digest = state.to_bytes().hex()[:24]

    def to_dict(self):
        return {
            "evaluation_status": "available",
            "score": self.score,
            "danger": self.danger,
            "continuation_flexibility": self.continuation_flexibility,
            "tie_break_digest": self.tie_break_digest,
        }


class _FastEvaluator:
    def __init__(self):
        self.evaluation_count = 0

    def evaluate(self, state, **_kwargs):
        self.evaluation_count += 1
        return _FastEvaluation(state)


def _set_pairs(simulator, current, next_pairs):
    game = simulator.game
    game.current_puyo_1 = Puyo(current[0])
    game.current_puyo_2 = Puyo(current[1])
    game.next_puyo_queue = deque((Puyo(pair[0]), Puyo(pair[1])) for pair in next_pairs)
    game.state = "control"
    game.game_over = False


def _fire_simulator():
    simulator = HeadlessPuyoSimulator(seed=0)
    game = simulator.game
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            game.field.grid[y][x] = Puyo(PuyoColor.EMPTY)
    for x in range(3):
        game.field.grid[0][x] = Puyo(PuyoColor.RED)
    _set_pairs(
        simulator,
        (PuyoColor.RED, PuyoColor.BLUE),
        (
            (PuyoColor.GREEN, PuyoColor.YELLOW),
            (PuyoColor.GREEN, PuyoColor.YELLOW),
        ),
    )
    return simulator


def _fire_fixture_simulator(case):
    simulator = HeadlessPuyoSimulator(seed=178)
    game = simulator.game
    for y in range(GRID_HEIGHT):
        for x in range(GRID_WIDTH):
            char = case["rows_bottom_up"][y][x]
            game.field.grid[y][x] = Puyo(
                PuyoColor.EMPTY if char == "." else FIRE_FIXTURE_COLORS[char]
            )
    current = tuple(PuyoColor[name] for name in case["current_pair"])
    next_pairs = tuple(
        tuple(PuyoColor[name] for name in pair)
        for pair in case["next_pairs"]
    )
    _set_pairs(simulator, current, next_pairs)
    return simulator


def _scenario_value(scenario_id, chain_count, chain_score):
    best = None
    if chain_count > 0:
        best = ChainFireEvidence(
            root_action=3,
            scenario_id=scenario_id,
            chain_count=chain_count,
            chain_score=chain_score,
            depth=scenario_id + 1,
            trigger_action=7 + scenario_id,
            state_fingerprint=f"state-{scenario_id}",
            path=(3, 7 + scenario_id),
            terminal=True,
            terminal_reason="chain_count_gte_1",
            fire_class=FIRE_CLASS_TARGET,
            target_chain_count=1,
            allowed=True,
        )
    return ScenarioRootEvidence(
        root_action=3,
        scenario_id=scenario_id,
        evaluated=True,
        search_complete=True,
        reached_depth=scenario_id + 1,
        max_chain_count=chain_count,
        max_chain_score=chain_score,
        best_fire=best,
        fire_count=int(chain_count > 0),
        terminal_fire_count=int(chain_count > 0),
        survivor_evaluator_score=float(10 + scenario_id),
        expanded_nodes=20,
        pruned_nodes=2,
        transposition_hits=1,
        truncation_reason=None,
        terminal_fire_rule="record_and_stop",
        terminal_fire_chain_count=1,
    )


class TestLongHorizonSearch(unittest.TestCase):
    def test_versioned_profiles_separate_runtime_and_quality_budgets(self):
        runtime = long_horizon_profile("runtime")
        quality = long_horizon_profile(QUALITY_D16_PROFILE)
        config = BeamSearchConfig.for_profile(QUALITY_D16_PROFILE)

        self.assertEqual((runtime.depth, runtime.width, runtime.scenarios), (3, 24, 1))
        self.assertEqual(runtime.budget_authority, "external_runtime_deadline")
        self.assertEqual(runtime.wall_clock_mode, "external_deadline_contract")
        self.assertEqual(
            (quality.depth, quality.width, quality.scenarios), (16, 250, 6)
        )
        self.assertEqual(quality.budget_authority, "expanded_nodes")
        self.assertEqual(quality.wall_clock_mode, "observational")
        self.assertEqual(config.search_backend, COMPACT_LONG_HORIZON_SEARCH_BACKEND)
        self.assertEqual(config.node_evaluator_backend, CHAIN_STRUCTURE_NODE_EVALUATOR)
        self.assertEqual(config.root_ranking_rule, EXPECTED_CHAIN_RANKING_RULE_VERSION)
        self.assertEqual(
            config.build_potential_schema_version, BUILD_POTENTIAL_SCHEMA_VERSION
        )
        self.assertEqual(config.probe_width, 0)
        with self.assertRaises(ValueError):
            BeamSearchConfig.for_profile("missing")

    def test_known_queue_boundary_and_six_scenario_digests_are_stable(self):
        simulator = HeadlessPuyoSimulator(seed=174)
        simulator.game.next_puyo_queue.append(
            (Puyo(PuyoColor.RED), Puyo(PuyoColor.BLUE))
        )
        first = build_scenario_sequences(simulator, scenarios=6, depth=8)
        second = build_scenario_sequences(simulator, scenarios=6, depth=8)
        game = simulator.game
        known = (
            (game.current_puyo_1.color, game.current_puyo_2.color),
            *tuple(
                tuple(puyo.color for puyo in pair)
                for pair in tuple(game.next_puyo_queue)[:2]
            ),
        )

        self.assertEqual([item.scenario_id for item in first], list(range(6)))
        self.assertEqual(
            [item.sequence_digest for item in first],
            [item.sequence_digest for item in second],
        )
        self.assertEqual(len({item.sequence_digest for item in first}), 6)
        for sequence in first:
            self.assertEqual(sequence.known_pairs, known)
            self.assertEqual(sequence.known_pair_count, 3)
            self.assertEqual(sequence.to_dict()["unknown_boundary_cursor"], 3)
            self.assertEqual(
                [item["source"] for item in sequence.to_dict()["pairs"][:4]],
                ["known", "known", "known", "unknown"],
            )

    def test_root_aggregation_preserves_raw_values_and_expected_statistics(self):
        values = (
            _scenario_value(0, 2, 100),
            _scenario_value(1, 4, 300),
            _scenario_value(2, 0, 0),
        )
        aggregate = aggregate_expected_chain_evidence(
            3,
            values,
            requested_scenarios=3,
        )
        payload = aggregate.to_dict()

        self.assertEqual(
            aggregate.schema_version, EXPECTED_CHAIN_EVIDENCE_SCHEMA_VERSION
        )
        self.assertEqual(aggregate.chain_score_sum, 400)
        self.assertAlmostEqual(aggregate.chain_score_mean, 400.0 / 3.0)
        self.assertEqual(aggregate.chain_count_sum, 6)
        self.assertEqual(aggregate.support, 2)
        self.assertEqual(aggregate.worst_chain_score, 0)
        self.assertAlmostEqual(
            aggregate.chain_score_dispersion,
            math.sqrt(
                ((100 - 400 / 3) ** 2 + (300 - 400 / 3) ** 2 + (0 - 400 / 3) ** 2) / 3
            ),
        )
        self.assertEqual(aggregate.best_fire.scenario_id, 1)
        self.assertEqual(len(payload["scenario_values"]), 3)
        self.assertEqual(
            [item["max_chain_score"] for item in payload["scenario_values"]],
            [100, 300, 0],
        )

    def test_terminal_fire_is_recorded_before_branch_stops(self):
        simulator = _fire_simulator()
        action = ACTION_TO_INDEX[PlacementAction(3, Direction.RIGHT)]
        result = run_long_horizon_search(
            simulator,
            LongHorizonSearchConfig(
                depth=3,
                width=64,
                scenarios=1,
                minimum_chain_count=10,
                max_expanded_nodes=3_000,
            ),
            evaluator=_FastEvaluator(),
        )
        evidence = result.evidence_by_action[action]
        raw = evidence.scenario_values[0]

        self.assertEqual(evidence.max_chain_count, 1)
        self.assertEqual(evidence.max_chain_score, 40)
        self.assertEqual(raw.fire_count, 1)
        self.assertEqual(raw.terminal_fire_count, 1)
        self.assertEqual(raw.reached_depth, 1)
        self.assertTrue(evidence.best_fire.terminal)
        self.assertEqual(evidence.best_fire.terminal_reason, "chain_count_gte_1")
        self.assertEqual(evidence.best_fire.path, (action,))
        self.assertEqual(result.representatives[action].path, (action,))

    def test_fixed_fire_semantics_fixture_reproduces_all_four_classes(self):
        payload = json.loads(FIRE_FIXTURE_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            payload["schema_version"],
            "puyo.build_main_fire_fixtures.v1",
        )
        observed = {}
        for case in payload["cases"]:
            result = run_long_horizon_search(
                _fire_fixture_simulator(case),
                LongHorizonSearchConfig(
                    depth=1,
                    width=24,
                    scenarios=1,
                    minimum_chain_count=case["target_chain_count"],
                    max_expanded_nodes=100,
                    fire_context=case["fire_context"],
                ),
                evaluator=_FastEvaluator(),
            )
            observed[case["id"]] = result.evidence_by_action[
                case["action"]
            ].fire_class
        self.assertEqual(
            observed,
            {
                case["id"]: case["expected_fire_class"]
                for case in payload["cases"]
            },
        )
        for boundary in payload["classification_boundaries"]:
            self.assertEqual(
                classify_build_main_fire(
                    chain_count=boundary["chain_count"],
                    chain_score=boundary["chain_score"],
                    target_chain_count=boundary["target_chain_count"],
                    fire_context=boundary["fire_context"],
                    winning_score_threshold=boundary.get(
                        "winning_score_threshold"
                    ),
                ),
                boundary["expected_fire_class"],
            )

    def test_safe_build_fire_classes_rank_quiet_above_premature_and_target_above_quiet(
        self,
    ):
        simulator = _fire_simulator()
        action = ACTION_TO_INDEX[PlacementAction(3, Direction.RIGHT)]
        safe = run_long_horizon_search(
            simulator,
            LongHorizonSearchConfig(
                depth=2,
                width=24,
                scenarios=1,
                minimum_chain_count=10,
                max_expanded_nodes=2_000,
            ),
        )
        premature = safe.evidence_by_action[action]
        quiet = next(
            evidence
            for evidence in safe.root_evidence
            if evidence.fire_class == FIRE_CLASS_QUIET
        )
        fire = premature.best_fire

        self.assertEqual(premature.fire_class, FIRE_CLASS_PREMATURE)
        self.assertFalse(fire.allowed)
        self.assertGreater(quiet.ranking_key, premature.ranking_key)
        self.assertEqual(fire.target_chain_gap, 9)
        self.assertEqual(
            fire.terminal_score_breakdown["official_score"],
            0.0,
        )
        self.assertLess(
            fire.terminal_score_breakdown["target_gap_penalty"],
            0.0,
        )
        self.assertLess(
            fire.terminal_evaluation["score_breakdown"]["premature_fire"],
            0.0,
        )
        self.assertGreater(fire.terminal_evaluation["trigger_damage"], 0)
        self.assertGreaterEqual(fire.terminal_evaluation["danger"], 0.0)

        target = run_long_horizon_search(
            simulator,
            LongHorizonSearchConfig(
                depth=1,
                width=24,
                scenarios=1,
                minimum_chain_count=1,
                max_expanded_nodes=100,
            ),
            evaluator=_FastEvaluator(),
        ).evidence_by_action[action]
        self.assertEqual(target.fire_class, FIRE_CLASS_TARGET)
        self.assertTrue(target.best_fire.allowed)
        self.assertGreater(target.ranking_key, quiet.ranking_key)
        self.assertEqual(
            classify_build_main_fire(
                chain_count=10,
                chain_score=70_000,
                target_chain_count=10,
            ),
            FIRE_CLASS_TARGET,
        )

    def test_forced_safety_fire_is_allowed_but_separate_from_safe_build(self):
        simulator = _fire_simulator()
        action = ACTION_TO_INDEX[PlacementAction(3, Direction.RIGHT)]
        forced = run_long_horizon_search(
            simulator,
            LongHorizonSearchConfig(
                depth=1,
                width=24,
                scenarios=1,
                minimum_chain_count=10,
                max_expanded_nodes=100,
                fire_context=FIRE_CONTEXT_FORCED_SAFETY,
            ),
            evaluator=_FastEvaluator(),
        ).evidence_by_action[action]

        self.assertEqual(forced.fire_class, FIRE_CLASS_FORCED_SAFETY)
        self.assertTrue(forced.best_fire.allowed)
        self.assertEqual(
            forced.best_fire.terminal_score_breakdown["official_score"],
            40.0,
        )

    def test_root_survivor_quota_precedes_shared_beam_and_records_shortfalls(self):
        simulator = HeadlessPuyoSimulator(seed=7)
        covered = run_long_horizon_search(
            simulator,
            LongHorizonSearchConfig(
                depth=2,
                width=24,
                scenarios=1,
                minimum_chain_count=10,
                max_expanded_nodes=5_000,
                root_survivor_quota=1,
            ),
            evaluator=_FastEvaluator(),
        )
        for evidence in covered.root_evidence:
            scenario = evidence.scenario_values[0]
            self.assertGreaterEqual(dict(scenario.survivor_counts)[1], 1)
            self.assertGreaterEqual(dict(scenario.survivor_counts)[2], 1)
            self.assertFalse(scenario.survivor_shortfalls)

        constrained = run_long_horizon_search(
            simulator,
            LongHorizonSearchConfig(
                depth=1,
                width=8,
                scenarios=1,
                minimum_chain_count=10,
                max_expanded_nodes=100,
                root_survivor_quota=1,
            ),
            evaluator=_FastEvaluator(),
        )
        shortfalls = [
            reason
            for evidence in constrained.root_evidence
            for _depth, reason in evidence.scenario_values[0].survivor_shortfalls
        ]
        self.assertIn("beam_width_below_root_quota", shortfalls)

    def test_multiple_fires_keep_each_roots_maximum_evidence_isolated(self):
        simulator = _fire_simulator()
        pair = (PuyoColor.RED, PuyoColor.RED)
        _set_pairs(simulator, pair, (pair, pair))
        result = run_long_horizon_search(
            simulator,
            LongHorizonSearchConfig(
                depth=3,
                width=64,
                scenarios=1,
                minimum_chain_count=10,
                max_expanded_nodes=10_000,
                terminal_fire_rule=TERMINAL_FIRE_CONTINUE,
            ),
            evaluator=_FastEvaluator(),
        )
        multi_fire = [
            evidence
            for evidence in result.root_evidence
            if evidence.scenario_values[0].fire_count > 1
        ]

        self.assertTrue(multi_fire)
        self.assertGreater(
            len({evidence.max_chain_score for evidence in multi_fire}),
            1,
        )
        for evidence in multi_fire:
            raw = evidence.scenario_values[0]
            self.assertEqual(raw.max_chain_score, raw.best_fire.chain_score)
            self.assertEqual(raw.root_action, raw.best_fire.root_action)
            self.assertEqual(raw.terminal_fire_count, 0)

    def test_transposition_table_preserves_root_scores_and_order(self):
        simulator = HeadlessPuyoSimulator(seed=0)
        pair = (PuyoColor.RED, PuyoColor.RED)
        _set_pairs(simulator, pair, (pair, pair))

        def run(enabled):
            return run_long_horizon_search(
                simulator,
                LongHorizonSearchConfig(
                    depth=3,
                    width=512,
                    scenarios=1,
                    minimum_chain_count=10,
                    max_expanded_nodes=30_000,
                    use_transposition_table=enabled,
                ),
                evaluator=_FastEvaluator(),
            )

        without_tt = run(False)
        with_tt = run(True)
        without_scores = [
            (item.root_action, item.chain_score_sum, item.chain_count_sum)
            for item in without_tt.ranked_roots
        ]
        with_scores = [
            (item.root_action, item.chain_score_sum, item.chain_count_sum)
            for item in with_tt.ranked_roots
        ]

        self.assertEqual(with_scores, without_scores)
        self.assertGreater(with_tt.counters.transposition_hits, 0)
        self.assertLessEqual(
            with_tt.counters.expanded_nodes,
            without_tt.counters.expanded_nodes,
        )

        budget = BuildPotentialBudget(
            max_added_puyos=1,
            max_pattern_nodes=1,
            max_resolution_nodes=1,
            max_alternatives=1,
            max_continuation_actions=1,
            max_recovery_puyos=0,
        )

        def run_beam(enabled):
            policy = BeamSearchPolicy(
                BeamSearchConfig.for_profile(
                    "runtime",
                    depth=3,
                    width=512,
                    max_expanded_nodes=30_000,
                    candidate_limit=8,
                    potential_probe_budget=8,
                    build_potential_budget=budget,
                    node_evaluator=_FastEvaluator(),
                    use_transposition_table=enabled,
                )
            )
            proposals = policy.generate_candidates(
                {},
                {
                    "simulator": simulator,
                    "action_mask": legal_action_mask(simulator),
                },
            )
            return policy, tuple(proposal.to_dict() for proposal in proposals)

        beam_without_tt, proposals_without_tt = run_beam(False)
        beam_with_tt, proposals_with_tt = run_beam(True)
        self.assertEqual(proposals_with_tt, proposals_without_tt)
        self.assertEqual(
            beam_with_tt.last_diagnostics.expected_chain_evidence["proposal_digest"],
            beam_without_tt.last_diagnostics.expected_chain_evidence["proposal_digest"],
        )
        self.assertLessEqual(
            beam_with_tt.last_diagnostics.expanded_nodes,
            beam_without_tt.last_diagnostics.expanded_nodes,
        )

    def test_beam_profile_is_deterministic_and_probes_exact_potential_for_final_k(self):
        simulator = HeadlessPuyoSimulator(seed=7)
        info = {
            "simulator": simulator,
            "action_mask": legal_action_mask(simulator),
        }
        budget = BuildPotentialBudget(
            max_added_puyos=1,
            max_pattern_nodes=1,
            max_resolution_nodes=1,
            max_alternatives=1,
            max_continuation_actions=1,
            max_recovery_puyos=0,
        )

        def run():
            policy = BeamSearchPolicy(
                BeamSearchConfig.for_profile(
                    "runtime",
                    depth=2,
                    width=24,
                    max_expanded_nodes=1_100,
                    candidate_limit=8,
                    potential_probe_budget=8,
                    build_potential_budget=budget,
                    node_evaluator=_FastEvaluator(),
                )
            )
            candidates = policy.generate_candidates({}, info)
            return policy, tuple(item.to_dict() for item in candidates)

        first, first_payload = run()
        second, second_payload = run()

        self.assertEqual(first_payload, second_payload)
        self.assertEqual(len(first_payload), 8)
        self.assertEqual(first.last_diagnostics.potential_probe_count, 8)
        self.assertEqual(
            sum(
                candidate.potential.evaluation_status != "not_evaluated"
                for candidate in first.last_diagnostics.candidates
            ),
            8,
        )
        self.assertEqual(
            first.last_diagnostics.expected_chain_evidence["deterministic_digest"],
            second.last_diagnostics.expected_chain_evidence["deterministic_digest"],
        )
        self.assertEqual(
            first.last_diagnostics.scenario_budget["known_pair_count"],
            3,
        )
        self.assertEqual(
            first.last_diagnostics.scenario_budget["wall_clock_mode"],
            "external_deadline_contract",
        )

    def test_worker_proposal_v2_keeps_k8_masks_and_rank_zero_compatibility(self):
        simulator = HeadlessPuyoSimulator(seed=11)
        policy = BeamSearchPolicy(
            BeamSearchConfig.for_profile(
                "runtime",
                depth=1,
                width=24,
                max_expanded_nodes=30,
                candidate_limit=8,
                potential_probe_budget=8,
                build_potential_budget=BuildPotentialBudget(
                    max_added_puyos=1,
                    max_pattern_nodes=1,
                    max_resolution_nodes=1,
                    max_alternatives=1,
                    max_continuation_actions=1,
                    max_recovery_puyos=0,
                ),
                node_evaluator=_FastEvaluator(),
            )
        )
        candidates = policy.generate_candidates(
            {},
            {
                "simulator": simulator,
                "action_mask": legal_action_mask(simulator),
            },
        )
        diagnostics = policy.last_diagnostics
        batch = build_worker_proposal_batch(
            candidates,
            selected_action=candidates[0].action,
            candidate_limit=8,
            legal_action_mask=legal_action_mask(simulator),
            profile_id=0,
            profile_name="long-horizon-smoke",
            strategy="build_large",
            simulator=simulator,
            expanded_nodes=diagnostics.expanded_nodes,
            scenario_budget=diagnostics.scenario_budget,
        )

        self.assertEqual(batch.schema_version, WORKER_PROPOSAL_SCHEMA_VERSION)
        self.assertEqual(batch.candidate_limit, 8)
        self.assertEqual(batch.candidate_count, 8)
        self.assertTrue(all(batch.candidate_mask))
        self.assertEqual(batch.selected_index, 0)
        self.assertEqual(batch.selected_action, candidates[0].action)
        self.assertTrue(
            all(batch.legal_action_mask[item.root_action] for item in batch.candidates)
        )
        self.assertEqual(
            batch.deterministic_digest,
            type(batch).from_dict(batch.to_dict()).deterministic_digest,
        )
        preview = type(batch).from_dict(batch.to_dict()).selected_candidate
        placement = action_to_placement(preview.root_action)
        self.assertIsNotNone(placement)
        self.assertIsNotNone(preview.evidence.expected_chain)
        self.assertIsNotNone(preview.evidence.structural_chain)
        self.assertEqual(
            preview.evidence.trajectory["fire_class"],
            FIRE_CLASS_QUIET,
        )
        self.assertIn(
            "survivor_coverage",
            preview.evidence.trajectory["root_diagnostics"],
        )
        self.assertEqual(
            preview.evidence.scenario_digest,
            batch.shared_context.scenario_digest,
        )
        self.assertNotIn("search_latency_ms", batch.ranker_input.to_dict()["feature_names"])


if __name__ == "__main__":
    unittest.main()
