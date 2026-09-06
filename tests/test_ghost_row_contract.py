import copy
import importlib
import unittest

import numpy as np

from agents.compact_search import CompactSearchState, legal_action_indices, transition
from agents.deep_chain_builder import (
    DeepChainBuilderConfig,
    DeepChainBuilderPolicy,
    _compact_state_from_observation,
    _transition_plan_step,
    _visible_decision_seed,
    build_visible_runtime_input,
    load_deep_chain_builder_config,
)
from agents.deep_chain_native_transition import (
    NativeCompactBatchClient,
    NativeCompactTransitionInput,
)
from eval.simulator_parity import board_names, compare_transition
from puyo_env.actions import ACTION_TO_INDEX, PLACEMENT_ACTIONS
from puyo_env.obs import encode_observation, flatten_vector_features
from puyo_env.realtime_ai import build_realtime_info, build_realtime_observation
from puyo_env.realtime_versus import RealtimeVersusMatch
from puyo_env.single_env import SinglePuyoEnv
from puyo_env.versus_env import VersusPuyoEnv
from src.core.constants import GRID_WIDTH, Direction, PuyoColor
from src.core.field import Field
from src.core.headless import HeadlessPuyoSimulator, PlacementAction
from src.core.puyo import Puyo


def action_id(x, direction):
    return ACTION_TO_INDEX[PlacementAction(x, direction)]


def runtime(sim):
    game = sim.game
    return build_visible_runtime_input(
        encode_observation(sim, step_count=0, max_steps=40),
        {
            "score": game.score,
            "last_chain_end_score": game.last_chain_end_score,
            "all_clear_bonus_pending": game.all_clear_bonus_pending,
            "game_over": game.game_over,
        },
    )


def plan_step(root, result, pair):
    return _transition_plan_step(
        root,
        result,
        pair,
        cursor=0,
        known_tsumo=True,
        scenario_id=0,
        reason="test",
        cumulative_score=0,
        cumulative_attack=0,
    )


class TestGhostRowContract(unittest.TestCase):
    def test_each_column_can_be_written_once_until_new_game(self):
        field = Field()
        for x in range(GRID_WIDTH):
            with self.subTest(x=x):
                original = Puyo(PuyoColor.BLUE)
                self.assertTrue(field.place_puyo(x, 13, original))
                for replacement in (PuyoColor.RED, PuyoColor.EMPTY, PuyoColor.OJAMA):
                    self.assertFalse(field.place_puyo(x, 13, Puyo(replacement)))
                    self.assertIs(field.get_puyo(x, 13), original)
                field.remove_puyos({(x, 13)})
                field.drop_puyo()
                self.assertIs(field.get_puyo(x, 13), original)
                self.assertTrue(
                    all(
                        field.get_puyo(other, 13).is_empty()
                        for other in range(x + 1, GRID_WIDTH)
                    )
                )
        self.assertTrue(Field().place_puyo(0, 13, Puyo(PuyoColor.RED)))

    def test_first_pair_placement_reaches_each_empty_ghost_slot(self):
        for x in range(GRID_WIDTH):
            with self.subTest(x=x):
                sim = HeadlessPuyoSimulator(seed=0)
                for y in range(12):
                    color = (PuyoColor.RED, PuyoColor.BLUE)[y % 2]
                    sim.game.field.place_puyo(x, y, Puyo(color))
                sim.game.current_puyo_1 = Puyo(PuyoColor.GREEN)
                sim.game.current_puyo_2 = Puyo(PuyoColor.YELLOW)
                result = sim.step((x, Direction.UP))
                self.assertTrue(result.valid)
                self.assertEqual(result.axis_y, 12)
                self.assertEqual(sim.game.field.get_puyo(x, 13).color, PuyoColor.YELLOW)
                self.assertEqual(result.game_over, x == 2)  # Existing choke at (2, 11).

    def test_clear_holes_later_pairs_and_gravity_never_move_ghost(self):
        for x in range(GRID_WIDTH):
            with self.subTest(x=x):
                sim = HeadlessPuyoSimulator(seed=0)
                sim.game.field.place_puyo(x, 13, Puyo(PuyoColor.BLUE))
                for y in (0, 1):
                    sim.game.field.place_puyo(x, y, Puyo(PuyoColor.RED))
                sim.game.current_puyo_1 = Puyo(PuyoColor.RED)
                sim.game.current_puyo_2 = Puyo(PuyoColor.RED)
                result = sim.step((x, Direction.DOWN))
                self.assertEqual(result.chain_count, 1)
                self.assertFalse(result.all_clear_achieved)
                self.assertTrue(
                    all(sim.game.field.get_puyo(x, y).is_empty() for y in range(13))
                )
                for _ in range(3):
                    self.assertTrue(sim.step(((x + 1) % 6, Direction.DOWN)).valid)
                    sim.game.field.drop_puyo()
                    self.assertEqual(
                        sim.game.field.get_puyo(x, 13).color, PuyoColor.BLUE
                    )

    def test_occupied_ghost_rejects_input_without_mutating_pair_queue_or_state(self):
        for x in range(GRID_WIDTH):
            sim = HeadlessPuyoSimulator(seed=0)
            sim.game.field.place_puyo(x, 13, Puyo(PuyoColor.BLUE))
            before = copy.deepcopy(sim.game.__dict__)
            result = sim.step((x, Direction.UP))
            self.assertFalse(result.valid)
            self.assertEqual(
                sim.game.field.to_color_grid(), before["field"].to_color_grid()
            )
            self.assertEqual(sim.game.score, before["score"])
            self.assertEqual(
                [(a.color, b.color) for a, b in sim.game.next_puyo_queue],
                [(a.color, b.color) for a, b in before["next_puyo_queue"]],
            )
            self.assertEqual(
                sim.game.current_puyo_1.color, before["current_puyo_1"].color
            )
            self.assertTrue(sim.step((x, Direction.DOWN)).valid)

    def test_overlapping_spawn_lock_cannot_overwrite_or_consume_pair(self):
        sim = HeadlessPuyoSimulator(seed=0)
        sim.game.field.place_puyo(2, 13, Puyo(PuyoColor.BLUE))
        sim.game.soft_drop_used_this_pair = True
        pair = (sim.game.current_puyo_1, sim.game.current_puyo_2)
        self.assertFalse(sim.game.lock_puyo())
        self.assertEqual(sim.game.score, 0)
        self.assertEqual((sim.game.current_puyo_1, sim.game.current_puyo_2), pair)
        self.assertEqual(sim.game.field.get_puyo(2, 13).color, PuyoColor.BLUE)
        self.assertTrue(sim.game.field.get_puyo(2, 12).is_empty())

    def test_observation_distinguishes_ghost_without_changing_checkpoint_tensors(self):
        empty = HeadlessPuyoSimulator(seed=0)
        occupied = copy.deepcopy(empty)
        occupied.game.field.place_puyo(1, 13, Puyo(PuyoColor.BLUE))
        left = encode_observation(empty, 0, 40)
        right = encode_observation(occupied, 0, 40)
        np.testing.assert_array_equal(left["board"], right["board"])
        np.testing.assert_array_equal(
            flatten_vector_features(left), flatten_vector_features(right)
        )
        self.assertEqual(left["board"].shape, (6, 13, 6))
        self.assertFalse(np.array_equal(left["ghost_row"], right["ghost_row"]))
        first, second = runtime(empty), runtime(occupied)
        self.assertNotEqual(
            _visible_decision_seed(first), _visible_decision_seed(second)
        )
        for sim, observed in ((empty, first), (occupied, second)):
            self.assertEqual(
                _compact_state_from_observation(observed),
                CompactSearchState.from_simulator(sim),
            )
        self.assertIn(
            action_id(1, Direction.UP),
            legal_action_indices(_compact_state_from_observation(first)),
        )
        self.assertNotIn(
            action_id(1, Direction.UP),
            legal_action_indices(_compact_state_from_observation(second)),
        )
        right["ghost_row"][:] = 0
        self.assertEqual(
            _compact_state_from_observation(second).color_at(1, 13), PuyoColor.BLUE
        )

    def test_missing_or_invalid_ghost_is_unknown_and_has_no_full_prediction(self):
        sim = HeadlessPuyoSimulator(seed=0)
        observation = encode_observation(sim, 0, 40)
        del observation["ghost_row"]
        value = build_visible_runtime_input(observation, {"action_mask": [True] * 22})
        with self.assertRaisesRegex(ValueError, "unknown, not empty"):
            _compact_state_from_observation(value)
        policy = DeepChainBuilderPolicy(profile="smoke", backend="python")
        policy.select_action(observation, {"action_mask": [True] * 22})
        diagnostics = policy.tactical_diagnostics
        self.assertTrue(diagnostics["fallback"]["used"])
        self.assertEqual(diagnostics["plan"]["steps"][0]["state_fingerprint"], "")
        for row in (np.full((6, 6), np.nan), np.ones((6, 6)), np.full((6, 6), 0.5)):
            with self.assertRaises(ValueError):
                _compact_state_from_observation(
                    build_visible_runtime_input({**observation, "ghost_row": row}, {})
                )

    def test_single_versus_and_realtime_propagate_complete_own_state(self):
        single = SinglePuyoEnv(seed=0)
        single.reset()
        versus = VersusPuyoEnv(seed=0)
        versus.reset()
        match = RealtimeVersusMatch(seed=0)
        simulators = (
            single.simulator,
            versus.player_states["player_0"].simulator,
            match.player_states["player_0"].simulator,
        )
        for sim in simulators:
            sim.game.field.place_puyo(4, 13, Puyo(PuyoColor.GREEN))
            sim.game.score = 2150
            sim.game.last_chain_end_score = 2140
            sim.game.all_clear_bonus_pending = True
        single_obs, single_info = single._observation_and_info()
        versus_obs, versus_info = (
            versus._observation("player_0"),
            versus._info("player_0"),
        )
        realtime_obs = build_realtime_observation(match, "player_0")
        realtime_info = build_realtime_info(match, "player_0")
        self.assertTrue(single.observation_space.contains(single_obs))
        self.assertTrue(versus.observation_space("player_0").contains(versus_obs))
        for sim, obs, info in zip(
            simulators,
            (single_obs, versus_obs, realtime_obs),
            (single_info, versus_info, realtime_info),
            strict=True,
        ):
            self.assertEqual(
                _compact_state_from_observation(build_visible_runtime_input(obs, info)),
                CompactSearchState.from_simulator(sim),
            )

    def test_parity_rejects_each_semantic_mismatch_and_missing_evidence(self):
        sim = HeadlessPuyoSimulator(seed=0)
        sim.game.field.place_puyo(1, 13, Puyo(PuyoColor.BLUE))
        root = CompactSearchState.from_simulator(sim)
        pair = (sim.game.current_puyo_1.color, sim.game.current_puyo_2.color)
        predicted = transition(root, pair, 0, capture_visuals=True)
        step = plan_step(root, predicted, pair)
        actual = sim.step(PLACEMENT_ACTIONS[0])
        state = CompactSearchState.from_simulator(sim)

        def compare(candidate):
            return compare_transition(
                action=0,
                predicted=candidate,
                actual=actual,
                root_state=root,
                actual_state=state,
                board_after=board_names(state),
            )

        self.assertTrue(compare(step)["passed"])
        for field, value, component in (
            ("action", 1, "action"),
            ("valid", False, "valid"),
            ("predicted_chain_count", 10, "chain_count"),
            ("predicted_score", 999, "score_delta"),
            ("game_over", True, "game_over"),
            ("state_fingerprint", "", "complete_state"),
            ("root_state_fingerprint", "", "root_state"),
        ):
            with self.subTest(component=component):
                result = compare({**step, field: value})
                self.assertFalse(result["passed"])
                self.assertIn(component, result["mismatches"])
        for y, component in (
            (0, "public_board"),
            (12, "public_board"),
            (13, "ghost_row"),
        ):
            modified = copy.deepcopy(step)
            modified["predicted_board"][y][1] = "RED"
            result = compare(modified)
            self.assertFalse(result["passed"])
            self.assertIn(component, result["mismatches"])
            self.assertEqual(result["cell_differences"][0]["y"], y)
        for candidate in (
            None,
            {**step, "predicted_board": step["predicted_board"][:13]},
        ):
            self.assertFalse(compare(candidate)["passed"])


try:
    NATIVE_MODULE = importlib.import_module("_puyo_deep_chain_native")
except (ImportError, OSError):
    NATIVE_MODULE = None


@unittest.skipIf(NATIVE_MODULE is None, "release native extension is not installed")
class TestGhostRowNativeContract(unittest.TestCase):
    def test_policy_search_and_materialized_plan_keep_complete_ghost_state(self):
        payload = load_deep_chain_builder_config().to_dict()
        payload["profiles"]["smoke"].update(
            depth=3, width=4, scenarios=2, max_expanded_nodes=512
        )
        config = DeepChainBuilderConfig.from_dict(payload)
        for ghost_x in (0, 2, 5):
            sim = HeadlessPuyoSimulator(seed=229)
            sim.game.field.place_puyo(ghost_x, 13, Puyo(PuyoColor.GREEN))
            sim.game.field.place_puyo(ghost_x, 0, Puyo(PuyoColor.RED))
            sim.game.field.place_puyo(ghost_x, 1, Puyo(PuyoColor.RED))
            observation = encode_observation(sim, 0, 40)
            outputs = []
            for backend in ("python", "native"):
                policy = DeepChainBuilderPolicy(
                    profile="smoke", config=config, backend=backend
                )
                action = policy.select_action(observation, {})
                diagnostics = policy.tactical_diagnostics
                self.assertFalse(diagnostics["fallback"]["used"])
                step = diagnostics["plan"]["steps"][0]
                root = CompactSearchState.from_simulator(sim)
                actual_sim = copy.deepcopy(sim)
                actual = actual_sim.step(PLACEMENT_ACTIONS[action])
                final = CompactSearchState.from_simulator(actual_sim)
                self.assertTrue(
                    compare_transition(
                        action=action,
                        predicted=step,
                        actual=actual,
                        root_state=root,
                        actual_state=final,
                        board_after=board_names(final),
                    )["passed"]
                )
                outputs.append(
                    (
                        action,
                        diagnostics["plan"]["steps"],
                        diagnostics["search"]["deterministic_digest"],
                    )
                )
            self.assertEqual(outputs[0], outputs[1])

    def test_every_action_and_column_matches_authoritative_including_rejected_input(
        self,
    ):
        inputs, expected = [], []
        for x in range(GRID_WIDTH):
            for tower in (False, True):
                sim = HeadlessPuyoSimulator(seed=0)
                if tower:
                    for y in range(12):
                        sim.game.field.place_puyo(
                            x, y, Puyo((PuyoColor.RED, PuyoColor.BLUE)[y % 2])
                        )
                else:
                    sim.game.field.place_puyo(x, 13, Puyo(PuyoColor.GREEN))
                    sim.game.field.place_puyo(x, 0, Puyo(PuyoColor.RED))
                    sim.game.field.place_puyo(x, 1, Puyo(PuyoColor.RED))
                sim.game.current_puyo_1 = Puyo(PuyoColor.RED)
                sim.game.current_puyo_2 = Puyo(PuyoColor.RED)
                root = _compact_state_from_observation(runtime(sim))
                for action, placement in enumerate(PLACEMENT_ACTIONS):
                    actual_sim = copy.deepcopy(sim)
                    actual = actual_sim.step(placement)
                    python = transition(root, (PuyoColor.RED,) * 2, action)
                    self.assertEqual(
                        python.state, CompactSearchState.from_simulator(actual_sim)
                    )
                    for key in (
                        "valid",
                        "axis_y",
                        "chain_count",
                        "score_delta",
                        "attack_score_delta",
                        "game_over",
                        "all_clear_bonus_pending",
                        "all_clear_achieved",
                    ):
                        self.assertEqual(getattr(python, key), getattr(actual, key))
                    inputs.append(
                        NativeCompactTransitionInput(root, (PuyoColor.RED,) * 2, action)
                    )
                    expected.append(
                        (python, tuple(ACTION_TO_INDEX[p] for p in sim.legal_actions()))
                    )
        outputs = NativeCompactBatchClient(NATIVE_MODULE).transition_batch(
            inputs, include_actions=True
        )
        for native, (python, legal) in zip(outputs.records, expected, strict=True):
            self.assertEqual(native.state, python.state)
            self.assertEqual(native.legal_action_indices, legal)
            for key in (
                "valid",
                "axis_y",
                "chain_count",
                "score_delta",
                "game_over",
                "all_clear_achieved",
            ):
                self.assertEqual(getattr(native, key), getattr(python, key))


if __name__ == "__main__":
    unittest.main()
