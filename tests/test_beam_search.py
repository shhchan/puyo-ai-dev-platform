import copy
import unittest

from agents.beam_search import (
    BeamSearchConfig,
    BeamSearchPolicy,
    BuildPotential,
    clone_simulator,
    evaluate_board,
    evaluate_build_potential,
)
from puyo_env.actions import action_to_placement, legal_action_mask
from src.core.constants import PuyoColor
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

    def test_build_potential_finds_one_two_and_three_puyo_ignitions(self):
        potentials = []
        for existing in (3, 2, 1):
            simulator = HeadlessPuyoSimulator(seed=31)
            for x in range(existing):
                simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
            before = simulator.game.field.to_color_grid()

            potential = evaluate_build_potential(simulator)

            potentials.append(potential)
            self.assertEqual(simulator.game.field.to_color_grid(), before)

        self.assertEqual([item.required_puyos for item in potentials], [1, 2, 3])
        self.assertTrue(all(item.chain_count == 1 for item in potentials))
        self.assertTrue(all(item.trigger_color == PuyoColor.RED for item in potentials))

    def test_build_potential_uses_stable_trigger_tie_break(self):
        simulator = HeadlessPuyoSimulator(seed=32)
        for x in range(3):
            simulator.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))

        first = evaluate_build_potential(simulator)
        second = evaluate_build_potential(simulator)

        self.assertEqual(first, second)
        self.assertEqual(first, BuildPotential(1, 1, 0, 1, PuyoColor.RED))

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
