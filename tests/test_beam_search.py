import copy
import unittest

from agents.beam_search import (
    BUILD_POTENTIAL_SCHEMA_VERSION,
    BUILD_POTENTIAL_V1_SCHEMA_VERSION,
    BUILD_SCORING_V2,
    LEGACY_BUILD_SCORING,
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotential,
    BuildPotentialBudget,
    BuildPotentialSession,
    TriggerAlternative,
    clone_simulator,
    compare_build_potential_triggers,
    evaluate_board,
    evaluate_build_potential,
    evaluate_build_potential_v1,
    evaluate_chain_shape_v2,
    migrate_build_potential_v1,
)
from agents.state_analyzer import simulator_from_snapshot
from eval.analyzer_scenarios import load_scenarios, scenario_input
from puyo_env.actions import action_to_placement, legal_action_mask
from src.core.constants import GRID_HEIGHT, GRID_WIDTH, PuyoColor
from src.core.headless import HeadlessPuyoSimulator
from src.core.puyo import Puyo
from selfplay.policies import make_policy


class TestBeamSearchPolicy(unittest.TestCase):
    def test_config_rejects_invalid_search_size(self):
        with self.assertRaises(ValueError):
            BeamSearchConfig(depth=0)
        with self.assertRaises(ValueError):
            BeamSearchConfig(width=0)
        with self.assertRaises(ValueError):
            BeamSearchConfig(scenarios=7)
        with self.assertRaises(ValueError):
            BeamSearchConfig(minimum_chain_count=0)
        with self.assertRaises(ValueError):
            BeamSearchConfig(trigger_preservation="unsupported")
        with self.assertRaises(ValueError):
            BeamSearchConfig(probe_width=-1)
        with self.assertRaises(ValueError):
            BuildPotentialBudget(max_added_puyos=5)

    def test_policy_returns_deterministic_legal_action(self):
        simulator = HeadlessPuyoSimulator(seed=7)
        policy = BeamSearchPolicy(BeamSearchConfig(depth=2, width=8))
        info = {"action_mask": legal_action_mask(simulator), "simulator": simulator}

        action_a = policy.select_action({}, info)
        action_b = policy.select_action({}, info)

        self.assertTrue(info["action_mask"][action_a])
        self.assertEqual(action_a, action_b)
        self.assertIsNotNone(policy.last_diagnostics)
        self.assertGreater(policy.last_diagnostics.expanded_nodes, 0)
        candidates = policy.last_diagnostics.candidates
        self.assertEqual(
            [candidate.action for candidate in candidates],
            [index for index, allowed in enumerate(info["action_mask"]) if allowed],
        )
        selected = next(candidate for candidate in candidates if candidate.action == action_a)
        self.assertTrue(selected.root_generated)
        self.assertGreater(selected.base_prune_depth, 0)
        self.assertGreater(selected.final_prune_depth, 0)
        self.assertIn("fire_cost", selected.to_dict())

    def test_policy_factory_builds_beam_policy(self):
        policy = make_policy("beam", seed=17, beam_depth=2, beam_width=8, beam_scenarios=1)

        self.assertIsInstance(policy, BeamSearchPolicy)
        self.assertEqual(policy.config.depth, 2)
        self.assertEqual(policy.config.width, 8)
        self.assertEqual(policy.config.scenario_seed, 17)

    def test_policy_factory_builds_worker_and_rule_manager_baselines(self):
        worker = make_policy("worker_fire")
        manager = make_policy("manager_rule")

        self.assertEqual(worker.profile_id, 4)
        self.assertEqual(manager.profiles[0].strategy, "build_large")

    def test_policy_takes_available_chain(self):
        simulator = HeadlessPuyoSimulator(seed=3)
        for x in range(3):
            simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
        simulator.game.current_puyo_1 = Puyo(PuyoColor.RED)
        simulator.game.current_puyo_2 = Puyo(PuyoColor.BLUE)
        policy = BeamSearchPolicy(BeamSearchConfig(depth=1, width=22, minimum_chain_count=1))
        info = {"action_mask": legal_action_mask(simulator), "simulator": simulator}

        action = policy.select_action({}, info)
        result = copy.deepcopy(simulator).step(action_to_placement(action))

        self.assertEqual(result.chain_count, 1)

    def test_board_evaluation_rewards_connected_colors(self):
        isolated = HeadlessPuyoSimulator(seed=1)
        connected = HeadlessPuyoSimulator(seed=1)
        isolated.game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        isolated.game.field.place_puyo(5, 0, Puyo(PuyoColor.RED))
        connected.game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        connected.game.field.place_puyo(1, 0, Puyo(PuyoColor.RED))

        self.assertGreater(evaluate_board(connected.game), evaluate_board(isolated.game))

    def test_board_evaluation_rewards_reachable_ignition(self):
        reachable = HeadlessPuyoSimulator(seed=1)
        buried = HeadlessPuyoSimulator(seed=1)
        for y in range(3):
            reachable.game.field.place_puyo(0, y, Puyo(PuyoColor.RED))
            buried.game.field.place_puyo(0, y, Puyo(PuyoColor.RED))
        buried.game.field.place_puyo(0, 3, Puyo(PuyoColor.BLUE))
        buried.game.field.place_puyo(1, 0, Puyo(PuyoColor.BLUE))

        self.assertGreater(evaluate_board(reachable.game), evaluate_board(buried.game))

    def test_v2_chain_shape_is_mirror_symmetric(self):
        left = HeadlessPuyoSimulator(seed=2)
        right = HeadlessPuyoSimulator(seed=2)
        cells = (
            (0, 0, PuyoColor.RED),
            (0, 1, PuyoColor.RED),
            (1, 0, PuyoColor.RED),
            (2, 0, PuyoColor.BLUE),
            (2, 1, PuyoColor.GREEN),
            (3, 0, PuyoColor.GREEN),
        )
        for x, y, color in cells:
            left.game.field.place_puyo(x, y, Puyo(color))
            right.game.field.place_puyo(5 - x, y, Puyo(color))

        self.assertEqual(
            evaluate_chain_shape_v2(left.game),
            evaluate_chain_shape_v2(right.game),
        )

    def test_premature_chain_penalty_persists_after_quiet_move(self):
        policy = BeamSearchPolicy(BeamSearchConfig(minimum_chain_count=4))
        initial = policy._chain_outcome(2, 360)
        node = type("Node", (), {"best_chain_value": initial[0], "premature_penalty": initial[1]})()

        self.assertEqual(policy._advance_chain_outcome(node, 0, 0), initial)

    def test_lightweight_clone_matches_deepcopy_and_is_independent(self):
        simulator = HeadlessPuyoSimulator(seed=11)
        simulator.game.all_clear_bonus_pending = True
        lightweight = clone_simulator(simulator)
        reference = copy.deepcopy(simulator)
        action = legal_action_mask(simulator).index(True)

        lightweight_result = lightweight.step(action_to_placement(action))
        reference_result = reference.step(action_to_placement(action))

        self.assertEqual(lightweight_result, reference_result)
        self.assertEqual(lightweight.game.field.to_color_grid(), reference.game.field.to_color_grid())
        self.assertEqual(lightweight.game.score, reference.game.score)
        self.assertTrue(lightweight.game.all_clear_bonus_pending)
        self.assertNotEqual(lightweight.game.field.to_color_grid(), simulator.game.field.to_color_grid())

    def test_lightweight_clone_preserves_future_pairs_without_sharing_rng(self):
        simulator = HeadlessPuyoSimulator(seed=19)
        first = clone_simulator(simulator)
        second = clone_simulator(simulator)

        self.assertEqual(first.game.puyo_sequence.next_pair()[0].color, second.game.puyo_sequence.next_pair()[0].color)
        self.assertEqual(first.game.puyo_sequence.next_pair()[1].color, second.game.puyo_sequence.next_pair()[1].color)

    def test_build_potential_v1_keeps_exact_single_column_contract(self):
        potentials = []
        for existing in (3, 2, 1):
            simulator = HeadlessPuyoSimulator(seed=31)
            for x in range(existing):
                simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
            before = simulator.game.field.to_color_grid()

            potential = evaluate_build_potential_v1(simulator)

            potentials.append(potential)
            self.assertEqual(simulator.game.field.to_color_grid(), before)

        self.assertEqual([item.required_puyos for item in potentials], [1, 2, 3])
        self.assertTrue(all(item.chain_count == 1 for item in potentials))
        self.assertTrue(all(item.trigger_color == PuyoColor.RED for item in potentials))
        self.assertEqual(
            potentials[0].to_dict(),
            {
                "chain_count": 1,
                "required_puyos": 1,
                "trigger": {"x": 0, "y": 1},
                "trigger_color": "RED",
            },
        )
        self.assertEqual(
            potentials[0].schema_version,
            BUILD_POTENTIAL_V1_SCHEMA_VERSION,
        )

    def test_build_potential_v1_uses_stable_trigger_tie_break(self):
        simulator = HeadlessPuyoSimulator(seed=32)
        for x in range(3):
            simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))

        first = evaluate_build_potential_v1(simulator)
        second = evaluate_build_potential_v1(simulator)

        self.assertEqual(first, second)
        self.assertEqual(first.chain_count, 1)
        self.assertEqual(first.required_puyos, 1)
        self.assertEqual((first.trigger_x, first.trigger_y), (0, 1))
        self.assertEqual(first.trigger_color, PuyoColor.RED)

    def test_build_potential_v2_finds_four_puyo_multi_column_alternatives(self):
        simulator = HeadlessPuyoSimulator(seed=34)
        budget = BuildPotentialBudget(
            max_added_puyos=4,
            max_pattern_nodes=1_024,
            max_resolution_nodes=192,
            max_alternatives=32,
            max_continuation_actions=8,
        )
        before = simulator.game.field.to_color_grid()

        first = evaluate_build_potential(simulator, budget=budget)
        second = evaluate_build_potential(simulator, budget=budget)
        payload = first.to_dict()

        self.assertEqual(first, second)
        self.assertEqual(simulator.game.field.to_color_grid(), before)
        self.assertEqual(first.schema_version, BUILD_POTENTIAL_SCHEMA_VERSION)
        self.assertEqual(first.evaluation_status, "available")
        self.assertTrue(first.search_complete)
        self.assertEqual(first.required_puyos, 4)
        self.assertTrue(
            any(len(alternative.columns) > 1 for alternative in first.alternatives)
        )
        self.assertEqual(payload["ignition_cost"]["puyos"], 4)
        self.assertEqual(payload["ignition_cost"]["turns_lower_bound"], 2)
        self.assertGreater(payload["trigger_equivalence"]["alternative_count"], 1)

    def test_v2_minimality_compacts_stacked_virtual_puyos_under_gravity(self):
        simulator = HeadlessPuyoSimulator(seed=40)
        for x, y in ((0, 0), (1, 0), (0, 2), (1, 3)):
            simulator.game.field.place_puyo(x, y, Puyo(PuyoColor.OJAMA))
        for x, y in ((0, 1), (1, 1), (1, 2)):
            simulator.game.field.place_puyo(x, y, Puyo(PuyoColor.RED))

        one_puyo = evaluate_build_potential(
            simulator,
            budget=BuildPotentialBudget(
                max_added_puyos=1,
                max_pattern_nodes=100,
                max_resolution_nodes=20,
                max_alternatives=8,
            ),
        )
        two_puyos = evaluate_build_potential(
            simulator,
            budget=BuildPotentialBudget(
                max_added_puyos=2,
                max_pattern_nodes=200,
                max_resolution_nodes=20,
                max_alternatives=8,
            ),
        )

        self.assertEqual(one_puyo.evaluation_status, "not_found")
        self.assertTrue(
            any(
                alternative.trigger_color == PuyoColor.RED
                and alternative.placements == ((2, 0), (2, 1))
                and alternative.chain_count == 1
                for alternative in two_puyos.alternatives
            )
        )

    def test_build_potential_v2_ranges_and_budget_bounds_hold_across_fields(self):
        budget = BuildPotentialBudget(
            max_added_puyos=4,
            max_pattern_nodes=96,
            max_resolution_nodes=6,
            max_alternatives=4,
            max_continuation_actions=4,
        )
        simulators = [HeadlessPuyoSimulator(seed=seed) for seed in range(3)]
        for index, simulator in enumerate(simulators):
            for x in range(index + 1):
                simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))

        for simulator in simulators:
            potential = evaluate_build_potential(simulator, budget=budget)
            payload = potential.to_dict()

            self.assertLessEqual(potential.pattern_nodes, budget.max_pattern_nodes)
            self.assertLessEqual(
                potential.resolution_nodes,
                budget.max_resolution_nodes,
            )
            self.assertLessEqual(len(potential.alternatives), budget.max_alternatives)
            for value in (
                potential.predicted_chain_potential,
                potential.continuation_flexibility,
                potential.danger_margin,
            ):
                if value is not None:
                    self.assertGreaterEqual(value, 0.0)
                    self.assertLessEqual(value, 1.0)
            self.assertIn(
                potential.evaluation_status,
                {"available", "not_found", "budget_exhausted"},
            )
            self.assertEqual(payload["search"]["budget"], budget.to_dict())

    def test_v2_bounded_ignition_search_has_no_left_right_cutoff_bias(self):
        budget = BuildPotentialBudget(
            max_added_puyos=4,
            max_pattern_nodes=512,
            max_resolution_nodes=16,
            max_alternatives=6,
            max_continuation_actions=6,
        )

        for scenario in load_scenarios():
            original = simulator_from_snapshot(scenario_input(scenario).own)
            mirrored = HeadlessPuyoSimulator(seed=1)
            for y in range(GRID_HEIGHT):
                for x in range(GRID_WIDTH):
                    puyo = original.game.field.grid[y][x]
                    if not puyo.is_empty():
                        mirrored.game.field.place_puyo(
                            GRID_WIDTH - 1 - x,
                            y,
                            Puyo(puyo.color),
                        )
            left = evaluate_build_potential(original, budget=budget)
            right = evaluate_build_potential(mirrored, budget=budget)

            self.assertEqual(
                (
                    left.evaluation_status,
                    left.chain_count,
                    left.required_puyos,
                    left.predicted_chain_potential,
                    left.continuation_flexibility,
                    left.pattern_nodes,
                    left.resolution_nodes,
                    len(left.alternatives),
                    left.equivalence_class_count,
                    left.truncation_reason,
                ),
                (
                    right.evaluation_status,
                    right.chain_count,
                    right.required_puyos,
                    right.predicted_chain_potential,
                    right.continuation_flexibility,
                    right.pattern_nodes,
                    right.resolution_nodes,
                    len(right.alternatives),
                    right.equivalence_class_count,
                    right.truncation_reason,
                ),
                scenario["name"],
            )

    def test_v2_continuation_budget_has_no_left_right_cutoff_bias(self):
        original = HeadlessPuyoSimulator(seed=35)
        mirrored = HeadlessPuyoSimulator(seed=35)
        for y in range(11):
            original.game.field.place_puyo(0, y, Puyo(PuyoColor.RED))
            mirrored.game.field.place_puyo(
                GRID_WIDTH - 1,
                y,
                Puyo(PuyoColor.RED),
            )

        for max_actions in range(1, GRID_WIDTH):
            budget = BuildPotentialBudget(
                max_added_puyos=1,
                max_pattern_nodes=100,
                max_resolution_nodes=20,
                max_alternatives=8,
                max_continuation_actions=max_actions,
            )

            left = evaluate_build_potential(original, budget=budget)
            right = evaluate_build_potential(mirrored, budget=budget)

            self.assertEqual(
                left.continuation_flexibility,
                right.continuation_flexibility,
                max_actions,
            )

    def test_build_potential_status_distinguishes_zero_from_not_evaluated(self):
        simulator = HeadlessPuyoSimulator(seed=35)
        budget = BuildPotentialBudget(
            max_added_puyos=1,
            max_pattern_nodes=24,
            max_resolution_nodes=1,
            max_alternatives=1,
            max_continuation_actions=1,
        )

        evaluated_zero = evaluate_build_potential(simulator, budget=budget)
        session = BuildPotentialSession(budget=budget, max_evaluations=1)
        session.evaluate(simulator)
        different = clone_simulator(simulator)
        different.game.field.place_puyo(0, 0, Puyo(PuyoColor.RED))
        not_evaluated = session.evaluate(different)

        self.assertEqual(evaluated_zero.evaluation_status, "not_found")
        self.assertEqual(evaluated_zero.predicted_chain_potential, 0.0)
        self.assertTrue(evaluated_zero.evaluated)
        self.assertEqual(not_evaluated.evaluation_status, "not_evaluated")
        self.assertIsNone(not_evaluated.predicted_chain_potential)
        self.assertIsNone(not_evaluated.to_dict()["predicted_chain_count"])
        self.assertFalse(not_evaluated.evaluated)
        self.assertEqual(not_evaluated.truncation_reason, "decision_probe_budget")

    def test_build_potential_session_is_deterministic_with_or_without_cache(self):
        budget = BuildPotentialBudget(
            max_added_puyos=1,
            max_pattern_nodes=24,
            max_resolution_nodes=4,
            max_alternatives=2,
            max_continuation_actions=2,
        )
        root = HeadlessPuyoSimulator(seed=36)
        same_field_different_tsumo = clone_simulator(root)
        same_field_different_tsumo.game.current_puyo_1 = Puyo(PuyoColor.YELLOW)
        same_field_different_tsumo.game.current_puyo_2 = Puyo(PuyoColor.GREEN)
        same_field_different_tsumo.game.all_clear_bonus_pending = True
        child = clone_simulator(root)
        child.game.field.place_puyo(0, 0, Puyo(PuyoColor.BLUE))
        overflow = clone_simulator(root)
        overflow.game.field.place_puyo(1, 0, Puyo(PuyoColor.GREEN))
        boards = (root, same_field_different_tsumo, child, overflow)

        cached = BuildPotentialSession(
            budget=budget,
            max_evaluations=2,
            use_cache=True,
        )
        uncached = BuildPotentialSession(
            budget=budget,
            max_evaluations=2,
            use_cache=False,
        )
        cached_results = tuple(cached.evaluate(board) for board in boards)
        uncached_results = tuple(uncached.evaluate(board) for board in boards)

        self.assertEqual(cached_results, uncached_results)
        self.assertEqual(cached.evaluation_count, 2)
        self.assertEqual(uncached.evaluation_count, 2)
        self.assertEqual(cached.cache_hits, 1)
        self.assertEqual(uncached.cache_hits, 0)
        self.assertEqual(cached_results[-1].evaluation_status, "not_evaluated")

    def test_v2_full_payload_depends_only_on_board_cells(self):
        root = HeadlessPuyoSimulator(seed=39)
        for x in range(3):
            root.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
        lifecycle_variant = clone_simulator(root)
        lifecycle_variant.game.current_puyo_1 = Puyo(PuyoColor.YELLOW)
        lifecycle_variant.game.current_puyo_2 = Puyo(PuyoColor.GREEN)
        lifecycle_variant.game.all_clear_bonus_pending = True

        baseline = evaluate_build_potential(root)
        varied = evaluate_build_potential(lifecycle_variant)

        self.assertEqual(varied, baseline)
        self.assertEqual(varied.to_dict(), baseline.to_dict())

    def test_trigger_comparison_accepts_exact_equivalent_and_bounded_recovery(self):
        def potential(
            *,
            cost: int,
            color: PuyoColor,
            placement: tuple[int, int],
            complete: bool = True,
        ) -> BuildPotential:
            placements = tuple(
                (placement[0], placement[1] + offset) for offset in range(cost)
            )
            alternative = TriggerAlternative(
                chain_count=2,
                score=360,
                added_puyos=cost,
                trigger_color=color,
                placements=placements,
                anchor_cells=(placement,),
                danger_margin=0.75,
            )
            return BuildPotential(
                chain_count=2,
                required_puyos=cost,
                trigger_x=placement[0],
                trigger_y=placement[1],
                trigger_color=color,
                alternatives=(alternative,),
                predicted_chain_potential=0.5,
                continuation_flexibility=0.5,
                danger_margin=0.75,
                evaluation_status="available" if complete else "budget_exhausted",
                search_complete=complete,
                budget=BuildPotentialBudget(),
            )

        root = potential(cost=1, color=PuyoColor.RED, placement=(0, 0))
        equivalent = potential(cost=1, color=PuyoColor.BLUE, placement=(1, 0))
        recoverable = potential(cost=3, color=PuyoColor.GREEN, placement=(2, 0))
        lost = potential(cost=4, color=PuyoColor.YELLOW, placement=(3, 0))

        self.assertEqual(compare_build_potential_triggers(root, root).status, "exact")
        self.assertEqual(
            compare_build_potential_triggers(root, equivalent).status,
            "equivalent",
        )
        recovery = compare_build_potential_triggers(
            root,
            recoverable,
            max_recovery_puyos=2,
        )
        self.assertEqual(recovery.status, "recoverable")
        self.assertEqual(recovery.recovery_cost_puyos, 2)
        loss = compare_build_potential_triggers(
            root,
            lost,
            max_recovery_puyos=2,
        )
        self.assertEqual(loss.status, "lost")
        self.assertFalse(loss.policy_preserved)

    def test_trigger_comparison_does_not_treat_truncated_zero_as_absent(self):
        root = BuildPotential(
            evaluation_status="budget_exhausted",
            search_complete=False,
            truncation_reason="pattern_nodes",
            budget=BuildPotentialBudget(),
        )
        selected = BuildPotential(
            predicted_chain_potential=0.0,
            continuation_flexibility=1.0,
            danger_margin=1.0,
            evaluation_status="not_found",
            search_complete=True,
            budget=BuildPotentialBudget(),
        )

        comparison = compare_build_potential_triggers(root, selected)

        self.assertEqual(comparison.status, "unknown")
        self.assertIsNone(root.to_dict()["predicted_chain_count"])
        self.assertIsNone(comparison.recoverable)
        self.assertFalse(comparison.policy_preserved)

    def test_trigger_comparison_skips_preservation_for_proven_empty_root(self):
        root = BuildPotential(
            predicted_chain_potential=0.0,
            continuation_flexibility=1.0,
            danger_margin=1.0,
            evaluation_status="not_found",
            search_complete=True,
            budget=BuildPotentialBudget(),
        )
        selected = BuildPotential(
            evaluation_status="not_evaluated",
            truncation_reason="decision_probe_budget",
            budget=BuildPotentialBudget(),
        )

        comparison = compare_build_potential_triggers(root, selected)

        self.assertEqual(comparison.status, "not_applicable")
        self.assertTrue(comparison.policy_preserved)

    def test_v1_migration_recomputes_with_board_or_marks_partial(self):
        simulator = HeadlessPuyoSimulator(seed=37)
        for x in range(3):
            simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
        legacy = evaluate_build_potential_v1(simulator).to_dict()
        budget = BuildPotentialBudget(
            max_added_puyos=1,
            max_pattern_nodes=24,
            max_resolution_nodes=8,
            max_alternatives=4,
            max_continuation_actions=2,
        )

        partial = migrate_build_potential_v1(legacy)
        unknown = migrate_build_potential_v1(
            {
                "chain_count": 0,
                "required_puyos": 0,
                "trigger": None,
                "trigger_color": None,
            }
        )
        recomputed = migrate_build_potential_v1(
            legacy,
            simulator=simulator,
            budget=budget,
        )

        self.assertEqual(partial.evaluation_status, "legacy_partial")
        self.assertEqual(partial.chain_count, legacy["chain_count"])
        self.assertIsNone(partial.predicted_chain_potential)
        self.assertEqual(unknown.evaluation_status, "unknown")
        self.assertFalse(unknown.evaluated)
        self.assertEqual(
            recomputed,
            evaluate_build_potential(simulator, budget=budget),
        )

    def test_v2_value_breakdown_wires_shape_weight_independently(self):
        simulator = HeadlessPuyoSimulator(seed=5)

        def run(shape_weight: float):
            policy = BeamSearchPolicy(
                BeamSearchConfig(
                    depth=1,
                    width=22,
                    scoring_mode=BUILD_SCORING_V2,
                    build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
                    future_potential_weight=0.0,
                    chain_shape_weight=shape_weight,
                    danger_weight=0.0,
                )
            )
            policy.select_action(
                {},
                {"action_mask": legal_action_mask(simulator), "simulator": simulator},
            )
            return {
                candidate.action: candidate.value_breakdown
                for candidate in policy.last_diagnostics.candidates
            }

        single = run(1.0)
        doubled = run(2.0)

        self.assertTrue(any(values["chain_shape"] != 0.0 for values in single.values()))
        for action, values in single.items():
            self.assertAlmostEqual(
                doubled[action]["chain_shape"],
                values["chain_shape"] * 2.0,
            )
            self.assertEqual(values["future_potential"], 0.0)
            self.assertEqual(values["danger"], 0.0)
            self.assertAlmostEqual(
                values["total"],
                sum(value for key, value in values.items() if key != "total"),
            )

    def test_v2_zero_future_weight_removes_only_future_contribution(self):
        simulator = HeadlessPuyoSimulator(seed=38)
        for x in range(3):
            simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
        simulator.game.current_puyo_1 = Puyo(PuyoColor.BLUE)
        simulator.game.current_puyo_2 = Puyo(PuyoColor.GREEN)
        policy = BeamSearchPolicy(
            BeamSearchConfig(
                depth=1,
                width=22,
                trigger_preservation="prefer",
                probe_width=22,
                scoring_mode=BUILD_SCORING_V2,
                build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
                future_potential_weight=0.0,
                chain_shape_weight=0.0,
                danger_weight=0.0,
                build_potential_budget=BuildPotentialBudget(
                    max_added_puyos=1,
                    max_pattern_nodes=24,
                    max_resolution_nodes=8,
                    max_alternatives=4,
                    max_continuation_actions=2,
                ),
            )
        )

        policy.select_action(
            {},
            {"action_mask": legal_action_mask(simulator), "simulator": simulator},
        )
        candidates = [
            candidate
            for candidate in policy.last_diagnostics.candidates
            if candidate.candidate_value is not None
        ]

        self.assertTrue(
            any(
                (candidate.potential.predicted_chain_potential or 0.0) > 0.0
                for candidate in candidates
            )
        )
        self.assertTrue(
            all(
                candidate.value_breakdown["future_potential"] == 0.0
                for candidate in candidates
            )
        )
        self.assertTrue(
            all(candidate.value_breakdown["chain_shape"] == 0.0 for candidate in candidates)
        )
        self.assertTrue(
            all(candidate.value_breakdown["danger"] == 0.0 for candidate in candidates)
        )

    def test_v2_future_weight_remains_active_when_trigger_policy_is_ignore(self):
        simulator = HeadlessPuyoSimulator(seed=40)

        def run(weight: float):
            policy = BeamSearchPolicy(
                BeamSearchConfig(
                    depth=1,
                    width=22,
                    trigger_preservation="ignore",
                    probe_width=22,
                    scoring_mode=BUILD_SCORING_V2,
                    build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
                    future_potential_weight=weight,
                    chain_shape_weight=0.0,
                    danger_weight=0.0,
                )
            )
            policy.select_action(
                {},
                {"action_mask": legal_action_mask(simulator), "simulator": simulator},
            )
            return policy.last_diagnostics

        disabled = run(0.0)
        enabled = run(1.0)

        self.assertEqual(disabled.potential_probe_count, 0)
        self.assertGreater(enabled.potential_probe_count, 0)
        self.assertEqual(enabled.root_potential.evaluation_status, "not_evaluated")
        self.assertTrue(
            all(
                candidate.value_breakdown["future_potential"] == 0.0
                for candidate in disabled.candidates
            )
        )
        self.assertTrue(
            any(
                candidate.value_breakdown["future_potential"] > 0.0
                for candidate in enabled.candidates
            )
        )
        self.assertTrue(
            all(
                candidate.value_breakdown["trigger_preservation"] == 0.0
                for candidate in enabled.candidates
            )
        )

    def test_v2_unknown_probe_is_neutral_in_ranking_breakdown(self):
        simulator = HeadlessPuyoSimulator(seed=41)
        for x in range(3):
            simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
        simulator.game.current_puyo_1 = Puyo(PuyoColor.BLUE)
        simulator.game.current_puyo_2 = Puyo(PuyoColor.GREEN)
        policy = BeamSearchPolicy(
            BeamSearchConfig(
                depth=1,
                width=22,
                trigger_preservation="required",
                probe_width=22,
                scoring_mode=BUILD_SCORING_V2,
                build_potential_schema_version=BUILD_POTENTIAL_SCHEMA_VERSION,
                future_potential_weight=1.0,
                chain_shape_weight=0.0,
                danger_weight=0.0,
                potential_probe_budget=1,
            )
        )

        policy.select_action(
            {},
            {"action_mask": legal_action_mask(simulator), "simulator": simulator},
        )
        ranked = [
            candidate
            for candidate in policy.last_diagnostics.candidates
            if candidate.candidate_value is not None
        ]

        self.assertEqual(policy.last_diagnostics.potential_probe_count, 1)
        self.assertTrue(ranked)
        self.assertTrue(
            all(
                candidate.potential.evaluation_status == "not_evaluated"
                for candidate in ranked
            )
        )
        self.assertTrue(
            all(
                candidate.value_breakdown["future_potential"] == 0.0
                and candidate.value_breakdown["trigger_preservation"] == 0.0
                for candidate in ranked
            )
        )

    def test_legacy_mode_keeps_fixed_action_and_candidate_values(self):
        simulator = HeadlessPuyoSimulator(seed=7)
        info = {"action_mask": legal_action_mask(simulator), "simulator": simulator}
        baseline = BeamSearchPolicy(
            BeamSearchConfig(depth=2, width=8, scenario_seed=17)
        )
        explicit = BeamSearchPolicy(
            BeamSearchConfig(
                depth=2,
                width=8,
                scenario_seed=17,
                scoring_mode=LEGACY_BUILD_SCORING,
                future_potential_weight=9.0,
                chain_shape_weight=7.0,
                danger_weight=5.0,
                build_potential_schema_version=BUILD_POTENTIAL_V1_SCHEMA_VERSION,
            )
        )

        baseline_action = baseline.select_action({}, info)
        explicit_action = explicit.select_action({}, info)

        self.assertEqual(baseline_action, 0)
        self.assertEqual(explicit_action, baseline_action)
        self.assertEqual(
            baseline.last_diagnostics.candidate_values,
            (
                (0, -192.0),
                (1, -216.0),
                (2, -192.0),
                (4, -192.0),
                (6, -216.0),
                (8, -306.0),
                (10, -192.0),
                (14, -306.0),
            ),
        )
        self.assertEqual(
            explicit.last_diagnostics.candidate_values,
            baseline.last_diagnostics.candidate_values,
        )

    def test_preserve_mode_suppresses_only_subtarget_fires_when_quiet_exists(self):
        policy = BeamSearchPolicy(
            BeamSearchConfig(
                minimum_chain_count=10,
                trigger_preservation="required",
                probe_width=1,
            )
        )
        quiet = object()
        premature = object()
        target = object()

        retained = policy._suppress_premature(
            [(premature, 9), (quiet, 0), (target, 10)]
        )

        self.assertEqual(retained, [quiet, target])

    def test_preserve_mode_avoids_premature_fire_and_resets_decision_cache(self):
        simulator = HeadlessPuyoSimulator(seed=33)
        for x in range(3):
            simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
        simulator.game.current_puyo_1 = Puyo(PuyoColor.RED)
        simulator.game.current_puyo_2 = Puyo(PuyoColor.BLUE)
        policy = BeamSearchPolicy(
            BeamSearchConfig(
                depth=1,
                width=22,
                minimum_chain_count=10,
                trigger_preservation="required",
                probe_width=8,
            )
        )
        info = {"action_mask": legal_action_mask(simulator), "simulator": simulator}

        first_action = policy.select_action({}, info)
        first_probes = policy.last_diagnostics.potential_probe_count
        second_action = policy.select_action({}, info)
        second_probes = policy.last_diagnostics.potential_probe_count
        result = copy.deepcopy(simulator).step(action_to_placement(first_action))

        self.assertEqual(result.chain_count, 0)
        self.assertEqual(first_action, second_action)
        self.assertGreater(first_probes, 0)
        self.assertEqual(first_probes, second_probes)
        self.assertEqual(policy.last_diagnostics.trigger_preservation, "required")
        self.assertEqual(policy.last_diagnostics.probe_width, 8)


if __name__ == "__main__":
    unittest.main()
