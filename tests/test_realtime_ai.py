import unittest

from puyo_env.action_planner import plan_placement_action
from puyo_env.actions import NUM_ACTIONS
from puyo_env.realtime_ai import (
    REALTIME_OBSERVATION_SCHEMA_VERSION,
    RealtimeDecisionConfig,
    RealtimePolicyController,
    RealtimePuyoEnv,
    build_realtime_info,
    build_realtime_observation,
    realtime_checkpoint_metadata,
    realtime_reachable_action_mask,
    validate_realtime_checkpoint_metadata,
)
from puyo_env.realtime_versus import RealtimeVersusMatch
from selfplay.policies import FirstLegalPolicy


class TestRealtimeAI(unittest.TestCase):
    def test_realtime_observation_contains_deadline_and_reachable_mask(self):
        match = RealtimeVersusMatch(seed=123, attack_delay_ticks=12)
        match.schedule_attack("player_0", 4, delay_ticks=7)

        observation = build_realtime_observation(match, "player_1", include_action_mask=True)
        info = build_realtime_info(match, "player_1")

        self.assertEqual(observation["schema_version"], REALTIME_OBSERVATION_SCHEMA_VERSION)
        self.assertEqual(observation["action_mask"].shape, (NUM_ACTIONS,))
        self.assertEqual(info["incoming_ticks"], 7)
        self.assertEqual(info["incoming_arrival_tick"], 7)
        self.assertEqual(info["own_phase"], "control")
        self.assertIn("active_position", info)

    def test_reachable_action_mask_matches_planner(self):
        match = RealtimeVersusMatch(seed=123)

        mask = realtime_reachable_action_mask(match.player_states["player_0"].simulator)

        for index, allowed in enumerate(mask):
            with self.subTest(index=index):
                plan = plan_placement_action(
                    match.player_states["player_0"].simulator,
                    action=index_to_placement(index),
                )
                self.assertEqual(bool(allowed), plan.reachable)

    def test_controller_injects_latency_before_executing_plan(self):
        match = RealtimeVersusMatch(seed=123)
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(inference_latency_ticks=2),
        )

        first = controller.next_input(match, "player_0")
        match.step({"player_0": first})
        second = controller.next_input(match, "player_0")
        match.step({"player_0": second})
        third = controller.next_input(match, "player_0")

        self.assertEqual(first.press, ())
        self.assertEqual(second.press, ())
        self.assertNotEqual(third, first)
        self.assertEqual(controller.diagnostics.decisions_started, 1)
        self.assertEqual(controller.diagnostics.idle_ticks, 2)
        self.assertEqual(controller.diagnostics.last_decision.inference_latency_ticks, 2)

    def test_controller_timeout_uses_fallback_deadline(self):
        match = RealtimeVersusMatch(seed=123)
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(inference_latency_ticks=5, timeout_ticks=1),
        )

        controller.next_input(match, "player_0")
        match.step()
        tick_input = controller.next_input(match, "player_0")

        self.assertEqual(controller.diagnostics.timeouts, 1)
        self.assertTrue(controller.diagnostics.last_decision.timeout)
        self.assertTrue(controller.diagnostics.last_decision.fallback)
        self.assertNotEqual(tick_input, type(tick_input)())

    def test_deadline_fallback_prefers_short_reachable_plan(self):
        match = RealtimeVersusMatch(seed=123)
        controller = RealtimePolicyController(
            FirstLegalPolicy(),
            config=RealtimeDecisionConfig(
                action_deadline_ticks=48,
                use_reachable_action_mask=True,
            ),
        )

        controller.next_input(
            match,
            "player_0",
            build_realtime_observation(match, "player_0", include_action_mask=True),
            build_realtime_info(match, "player_0", use_reachable_action_mask=True),
        )

        decision = controller.diagnostics.last_decision
        self.assertTrue(decision.deadline_miss)
        self.assertTrue(decision.fallback)
        self.assertEqual(decision.reason, "deadline_fallback")
        self.assertEqual(decision.action_index, 7)
        self.assertLess(decision.plan_ticks, 62)

    def test_realtime_env_returns_reward_components_and_episode_info(self):
        env = RealtimePuyoEnv(seed=123, max_ticks=2)
        observations, infos = env.reset(seed=123)

        self.assertIn("realtime_scalars", observations["player_0"])
        self.assertEqual(len(infos["player_0"]["action_mask"]), NUM_ACTIONS)

        _, rewards, _, truncations, infos = env.step()
        _, rewards, _, truncations, infos = env.step()

        self.assertTrue(truncations["player_0"])
        self.assertIn("reward_components", infos["player_0"])
        self.assertIn("episode", infos["player_0"])
        self.assertIn("player_0", rewards)

    def test_checkpoint_metadata_accepts_native_and_turn_based_adapter(self):
        native = {"realtime_policy": realtime_checkpoint_metadata(native_realtime=True)}
        turn_based = {"model_state_dict": {"cnn.0.weight": object()}}

        self.assertEqual(validate_realtime_checkpoint_metadata(native)["mode"], "realtime_native")
        self.assertEqual(
            validate_realtime_checkpoint_metadata(turn_based)["mode"],
            "turn_based_placement_adapter",
        )
        with self.assertRaises(ValueError):
            validate_realtime_checkpoint_metadata({}, allow_turn_based_adapter=False)


def index_to_placement(index):
    from puyo_env.actions import action_to_placement

    return action_to_placement(index)


if __name__ == "__main__":
    unittest.main()
